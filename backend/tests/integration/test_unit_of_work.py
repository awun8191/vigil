"""The store is not the transaction boundary. The request, the worker and the test are.

Four properties, one test each:

* two writes into one transaction either both land or neither does, so a store
  that fails on a constraint leaves no half change behind,
* a worker job's rows are durable when the job ends, read back by a session that
  never took part in the job,
* a write in one HTTP request is visible to the next request,
* a store keeps its own work readable inside the transaction it was given.

Before the stores stopped committing, the first test failed: the first write had
already committed when the second one hit the constraint.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config.settings import Settings
from src.main import create_app
from src.modules.auth.repository import DeviceCreateDTO
from src.modules.auth.state import DEFAULT_USER_ID, SqlAuthStore
from src.modules.chat.state import SqlChatRepository
from src.modules.connectors.repository import MCPServerDTO
from src.modules.connectors.service import MCPRegistrationError
from src.modules.connectors.state import SqlConnectorStore
from src.modules.jobs.repository import JobDTO
from src.modules.jobs.service import JobsService
from src.schemas.connectors import MCPServerModel
from src.schemas.identity import DeviceModel
from src.schemas.runtime import ThreadModel
from src.schemas.system import JobModel
from src.worker import JobContext, process_job

SEEDED_DEVICE_TOKEN = "vgt_testtoken"
"""The bearer token the suite seeds. HTTP tests authenticate with it."""


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield a session factory, or skip when the database cannot serve the test."""
    engine = create_async_engine(Settings(APP_ENV="test").DATABASE_URL)
    try:
        async with engine.connect() as connection:
            for table in ("users", "devices", "threads", "mcp_servers", "jobs"):
                present = await connection.scalar(
                    text(f"select to_regclass('public.{table}')")
                )
                if present is None:
                    pytest.skip(
                        f"table {table!r} is missing from the test database; "
                        "run the migrations first"
                    )
    except Exception as exc:  # pragma: no cover - depends on the host
        await engine.dispose()
        pytest.skip(f"PostgreSQL test database not available: {exc}")
    try:
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_failed_second_write_leaves_the_first_row_out_of_the_database(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A device write that is followed by a duplicate token hash leaves nothing."""
    suffix = uuid4().hex[:8]
    first_hash = f"uow-first-{suffix}"
    taken_hash = f"uow-taken-{suffix}"
    first_device_id: UUID | None = None

    async with session_factory() as setup:
        setup.add(
            DeviceModel(
                id=uuid4(),
                user_id=DEFAULT_USER_ID,
                name="taken",
                token_hash=taken_hash,
            )
        )
        await setup.commit()

    try:
        async with session_factory() as session:
            store = SqlAuthStore(session)

            first = await store.create_device(
                DeviceCreateDTO(
                    user_id=DEFAULT_USER_ID, name="first", token_hash=first_hash
                )
            )
            first_device_id = first.id
            # The store did its work: the row is readable inside the transaction.
            assert await session.get(DeviceModel, first.id) is not None

            # The second write names a token hash that is already taken.
            with pytest.raises(IntegrityError):
                await store.create_device(
                    DeviceCreateDTO(
                        user_id=DEFAULT_USER_ID,
                        name="second",
                        token_hash=taken_hash,
                    )
                )
            # The boundary discards the transaction it owns.
            await session.rollback()

        async with session_factory() as check:
            # The first write never reached the database.
            assert await check.get(DeviceModel, first_device_id) is None
            assert (
                await check.scalar(
                    select(func.count())
                    .select_from(DeviceModel)
                    .where(DeviceModel.token_hash == first_hash)
                )
                == 0
            )
            # The row the second write collided with is the only one left.
            assert (
                await check.scalar(
                    select(func.count())
                    .select_from(DeviceModel)
                    .where(DeviceModel.token_hash == taken_hash)
                )
                == 1
            )
    finally:
        async with session_factory() as cleanup:
            await cleanup.execute(
                text(
                    "delete from devices where token_hash in (:first, :taken)"
                ),
                {"first": first_hash, "taken": taken_hash},
            )
            await cleanup.commit()


@pytest.mark.asyncio
async def test_a_duplicate_mcp_server_name_leaves_no_first_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A second registration of one name discards the first, uncommitted row."""
    name = f"uow-mcp-{uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    first_server_id: UUID | None = None

    def candidate() -> MCPServerDTO:
        return MCPServerDTO(
            id=uuid4(),
            name=name,
            transport="sse",
            command=None,
            url="http://127.0.0.1:9/sse",
            enabled=True,
            created_at=now,
        )

    try:
        async with session_factory() as session:
            store = SqlConnectorStore(session)

            first = await store.register_server(candidate())
            first_server_id = first.id
            # Inside the transaction the row is there.
            assert await store.get_server(name) is not None

            with pytest.raises(MCPRegistrationError, match="already registered"):
                await store.register_server(candidate())
            await session.rollback()

        async with session_factory() as check:
            assert await check.get(MCPServerModel, first_server_id) is None
            assert (
                await check.scalar(
                    select(func.count())
                    .select_from(MCPServerModel)
                    .where(MCPServerModel.name == name)
                )
                == 0
            )
    finally:
        async with session_factory() as cleanup:
            await cleanup.execute(
                text("delete from mcp_servers where name = :name"), {"name": name}
            )
            await cleanup.commit()


@pytest.mark.asyncio
async def test_a_worker_job_commits_its_rows_at_the_job_boundary(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Rows a handler writes through a store survive the job and its session."""
    suffix = uuid4().hex[:8]
    title = f"uow-worker-{suffix}"
    job_id = uuid4()
    now = datetime.now(timezone.utc)
    settings = Settings(APP_ENV="test")

    try:
        async with session_factory() as setup:
            setup.add(
                JobModel(
                    id=job_id,
                    type="cleanup_sandbox",
                    payload={},
                    run_at=now,
                    attempts=1,
                    max_attempts=5,
                    status="locked",
                    locked_by="uow-worker",
                    locked_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            await setup.commit()

        async with session_factory() as session:
            jobs = JobsService(session=session, settings=settings)
            job = JobDTO(
                id=job_id,
                type="cleanup_sandbox",
                payload={},
                run_at=now,
                attempts=1,
                max_attempts=5,
                status="locked",
                locked_by="uow-worker",
                locked_at=now,
                last_error=None,
                created_at=now,
                updated_at=now,
                priority=0,
                heartbeat_at=None,
            )

            async def handler(claimed: JobDTO, context: JobContext) -> None:
                await SqlChatRepository(context.session).create_session(
                    user_id=DEFAULT_USER_ID, title=title
                )
                # The store must not commit mid-job: a reader outside this
                # transaction sees nothing until the boundary commits.
                async with session_factory() as mid_job:
                    seen = await mid_job.scalar(
                        select(ThreadModel.id).where(ThreadModel.title == title)
                    )
                assert seen is None, "a store committed inside the job"

            await process_job(
                job,
                jobs,
                JobContext(settings=settings, session=session),
                handlers={"cleanup_sandbox": handler},
            )

        # Closing the session rolled its transaction back. Only the commit the
        # worker made at the end of the job can explain a row here.
        async with session_factory() as check:
            thread_ids = list(
                await check.scalars(
                    select(ThreadModel.id).where(ThreadModel.title == title)
                )
            )
            assert len(thread_ids) == 1
            assert (
                await check.scalar(
                    select(JobModel.status).where(JobModel.id == job_id)
                )
                == "completed"
            )
    finally:
        async with session_factory() as cleanup:
            await cleanup.execute(
                text("delete from threads where title = :title"), {"title": title}
            )
            await cleanup.execute(
                text("delete from jobs where id = :id"), {"id": job_id}
            )
            await cleanup.commit()


@pytest.mark.asyncio
async def test_a_write_in_one_request_is_visible_to_the_next_request() -> None:
    """A chat session created by one request is readable by the next one."""
    settings = Settings(APP_ENV="test")
    app = create_app(settings)
    headers = {"Authorization": f"Bearer {SEEDED_DEVICE_TOKEN}"}
    title = f"uow-request-{uuid4().hex[:8]}"
    thread_id: UUID | None = None
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    engine = create_async_engine(settings.DATABASE_URL)

    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            created = await client.post(
                "/api/v1/chat/sessions", json={"title": title}, headers=headers
            )
            assert created.status_code == 201
            thread_id = UUID(created.json()["session_id"])
            assert created.json()["title"] == title

            listed = await client.get("/api/v1/chat/sessions", headers=headers)
            assert listed.status_code == 200
            assert any(
                row["session_id"] == str(thread_id) for row in listed.json()
            ), "a later request must see the row the first request wrote"

            history = await client.get(
                f"/api/v1/chat/sessions/{thread_id}/messages", headers=headers
            )
            assert history.status_code == 200
            assert history.json() == []

        # A connection outside the request confirms the row is in the database,
        # not in the memory of the process that answered.
        async with engine.connect() as connection:
            stored = await connection.scalar(
                select(ThreadModel.id).where(ThreadModel.id == thread_id)
            )
            assert stored == thread_id
    finally:
        await engine.dispose()
        cleanup_engine = create_async_engine(settings.DATABASE_URL)
        try:
            async with cleanup_engine.begin() as connection:
                if thread_id is not None:
                    await connection.execute(
                        text("delete from threads where id = :id"),
                        {"id": thread_id},
                    )
        finally:
            await cleanup_engine.dispose()


async def test_a_step_checkpoint_reaches_a_reader_mid_run(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A step written during a run is visible to the SSE reader before the run ends.

    The step journal is a live feed. The reader holds its own request session, so
    a step that waits for the job's final commit is invisible and the live view
    stalls. The checkpoint is what makes the step visible while the writer is
    still inside the run.
    """
    from src.modules.runs.repository import (
        AgentConfigDTO,
        RunCreateDTO,
        RunStepCreateDTO,
    )
    from src.modules.runs.state import SqlRunJournal

    run_id: UUID | None = None
    config_id: UUID | None = None
    try:
        async with session_factory() as writer:
            journal = SqlRunJournal(writer)
            config = await journal.save_config(
                AgentConfigDTO(
                    id=uuid4(),
                    name=f"uow-steps-{uuid4().hex[:8]}",
                    system_prompt="Checkpoint probe.",
                    model="test-model",
                    tools=[],
                    skills=[],
                    memory_scopes=[],
                    approval_policy={},
                    limits={"max_cost_micros": 0},
                )
            )
            config_id = config.id
            run = await journal.create_run(
                RunCreateDTO(config_id=config.id, status="running")
            )
            run_id = run.id
            await journal.append_step(
                RunStepCreateDTO(
                    run_id=run.id,
                    step_number=0,
                    event_type="message_delta",
                    payload={"delta": "live"},
                )
            )

            # Uncommitted, so the live reader cannot see it yet.
            async with session_factory() as before_checkpoint:
                assert await SqlRunJournal(before_checkpoint).list_steps(run.id) == []

            await journal.checkpoint()

            # The writer is still open, exactly like a run in progress.
            async with session_factory() as reader:
                steps = await SqlRunJournal(reader).list_steps(run.id)

            assert [step.event_type for step in steps] == ["message_delta"]
            assert steps[0].payload["delta"] == "live"
    finally:
        cleanup = create_async_engine(Settings(APP_ENV="test").DATABASE_URL)
        try:
            async with cleanup.begin() as connection:
                if run_id is not None:
                    for statement in (
                        "delete from run_steps where run_id = :id",
                        "delete from messages where run_id = :id",
                        "delete from runs where id = :id",
                    ):
                        await connection.execute(text(statement), {"id": run_id})
                if config_id is not None:
                    await connection.execute(
                        text("delete from agent_configs where id = :id"),
                        {"id": config_id},
                    )
        finally:
            await cleanup.dispose()


async def _seed_locked_job(
    session_factory: async_sessionmaker[AsyncSession], job_id: UUID, now: datetime
) -> None:
    """Insert one job row in the locked state a worker would hold."""
    async with session_factory() as setup:
        setup.add(
            JobModel(
                id=job_id,
                type="cleanup_sandbox",
                payload={},
                run_at=now,
                attempts=1,
                max_attempts=5,
                status="locked",
                locked_by="uow-worker",
                locked_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await setup.commit()


def _locked_job(job_id: UUID, now: datetime) -> JobDTO:
    """Return the DTO the dispatcher hands to a handler for that row."""
    return JobDTO(
        id=job_id,
        type="cleanup_sandbox",
        payload={},
        run_at=now,
        attempts=1,
        max_attempts=5,
        status="locked",
        locked_by="uow-worker",
        locked_at=now,
        last_error=None,
        created_at=now,
        updated_at=now,
        priority=0,
        heartbeat_at=None,
    )


class _FailingCommitSession:
    """A session whose commit fails, to prove no completion is reported."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def __getattr__(self, name: str):
        return getattr(self._session, name)

    async def commit(self) -> None:
        raise RuntimeError("commit refused")


@pytest.mark.asyncio
async def test_a_plain_handler_error_discards_the_partial_write(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A failed job keeps only what it checkpointed, not a half-applied write."""
    suffix = uuid4().hex[:8]
    title = f"uow-error-{suffix}"
    job_id = uuid4()
    now = datetime.now(timezone.utc)
    settings = Settings(APP_ENV="test")

    try:
        await _seed_locked_job(session_factory, job_id, now)

        async with session_factory() as session:
            jobs = JobsService(session=session, settings=settings)

            async def handler(claimed: JobDTO, context: JobContext) -> None:
                await SqlChatRepository(context.session).create_session(
                    user_id=DEFAULT_USER_ID, title=title
                )
                raise RuntimeError("handler exploded")

            await process_job(
                _locked_job(job_id, now),
                jobs,
                JobContext(settings=settings, session=session),
                handlers={"cleanup_sandbox": handler},
            )

        async with session_factory() as check:
            partial = await check.scalar(
                select(func.count())
                .select_from(ThreadModel)
                .where(ThreadModel.title == title)
            )
            status = await check.scalar(
                select(JobModel.status).where(JobModel.id == job_id)
            )

        assert partial == 0, "the failed job's uncommitted write must not survive"
        assert status == "pending", "the job is retried, not completed"
    finally:
        async with session_factory() as cleanup:
            await cleanup.execute(
                text("delete from threads where title = :title"), {"title": title}
            )
            await cleanup.execute(
                text("delete from jobs where id = :id"), {"id": job_id}
            )
            await cleanup.commit()


@pytest.mark.asyncio
async def test_a_failed_commit_raises_and_reports_no_completion(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A commit failure must not be logged as a completed job."""
    suffix = uuid4().hex[:8]
    title = f"uow-commit-{suffix}"
    job_id = uuid4()
    now = datetime.now(timezone.utc)
    settings = Settings(APP_ENV="test")

    try:
        await _seed_locked_job(session_factory, job_id, now)

        async with session_factory() as session:
            jobs = JobsService(session=session, settings=settings)

            async def handler(claimed: JobDTO, context: JobContext) -> None:
                await SqlChatRepository(context.session).create_session(
                    user_id=DEFAULT_USER_ID, title=title
                )

            with pytest.raises(RuntimeError, match="commit refused"):
                await process_job(
                    _locked_job(job_id, now),
                    jobs,
                    JobContext(
                        settings=settings,
                        session=_FailingCommitSession(session),  # type: ignore[arg-type]
                    ),
                    handlers={"cleanup_sandbox": handler},
                )

        # The real session was never committed, so closing it rolled everything
        # back: no completed job, no thread.
        async with session_factory() as check:
            status = await check.scalar(
                select(JobModel.status).where(JobModel.id == job_id)
            )
            partial = await check.scalar(
                select(func.count())
                .select_from(ThreadModel)
                .where(ThreadModel.title == title)
            )

        assert status == "locked", "a failed commit must leave the outcome unrecorded"
        assert partial == 0
    finally:
        async with session_factory() as cleanup:
            await cleanup.execute(
                text("delete from threads where title = :title"), {"title": title}
            )
            await cleanup.execute(
                text("delete from jobs where id = :id"), {"id": job_id}
            )
            await cleanup.commit()
