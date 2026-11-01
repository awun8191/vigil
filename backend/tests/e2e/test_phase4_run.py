"""End-to-end proof of the Phase 4 run path.

One card starts one run. The worker claims the execute job and drives the run
through the loop: the run writes a file in a real Docker sandbox, asks for an
approval on one external tool call, and waits. The user approves. The approval
worker builds a new service from the database rows alone and resumes the run,
which calls the external tool and completes with one artifact.

The test uses a scripted model client and a fake connector. It uses a real
sandbox only when Docker holds an allowed image, and skips with a reason when
it does not.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config.settings import Settings
from src.dependencies import get_approvals_service, get_runs_service
from src.integrations.sandbox.contract import resolve_image_allowlist
from src.integrations.sandbox.sandbox import SandboxRunner, docker_binary_available
from src.main import create_app
from src.modules.approvals.service import ApprovalsService
from src.modules.jobs.service import JOB_TYPE_PRIORITIES, JobsService
from src.modules.runs.service import RunsService
from src.modules.runs.state import InMemoryRunJournal, SqlRunJournal
from src.schemas import Base
from src.schemas.identity import UserModel
from src.schemas.runtime import (
    AgentConfigModel,
    ApprovalModel,
    ArtifactModel,
    MessageModel,
    RunModel,
)
from src.schemas.system import JobModel
from src.schemas.triage import CardModel
from src.worker import JobContext, process_job
from tests.e2e.fixtures.fake_connector import FakeConnector
from tests.e2e.fixtures.fake_model import (
    BASH_CALL_ID,
    DELIVER_CALL_ID,
    EXTERNAL_ARGUMENTS,
    EXTERNAL_CALL_ID,
    EXTERNAL_TOOL,
    REPORT_NAME,
    REPORT_TEXT,
    FakeModel,
    background_response,
    final_response,
)

AUTH_HEADERS = {"Authorization": "Bearer vgt_testtoken"}
"""Bearer token that the test environment accepts."""

CANDIDATE_IMAGES: tuple[str, ...] = (
    "python:3.14-slim",
    "python:3.13-slim",
    "python:3.12-slim",
    "python:3-slim",
)
"""Sandbox images that can write one file with a POSIX shell."""

FIRST_STAGES: tuple[str, ...] = (
    "assemble",
    "compact_check",
    "infer",
    "partition",
    "dispatch",
    "record",
)
"""Stages of the iteration that pauses on the approval or the background job."""

RESUME_STAGES: tuple[str, ...] = (
    "assemble",
    "compact_check",
    "dispatch",
    "record",
    "assemble",
    "compact_check",
    "infer",
    "record",
)
"""Stages of the approval resume: the approved call, then the final model turn."""

FINAL_STAGES: tuple[str, ...] = RESUME_STAGES
"""Stages of a resume that records a background result, then calls the model."""


class SandboxE2ESettings(Settings):
    """Settings whose image allowlist holds every candidate test image."""

    SANDBOX_IMAGE_ALLOWLIST: str = ",".join(CANDIDATE_IMAGES)


class RecordingObserver:
    """Record the end of every loop stage."""

    def __init__(self) -> None:
        self.stages: list[str] = []

    async def on_stage(self, run_id: UUID, stage: str) -> None:
        """Record one stage."""
        self.stages.append(stage)


@dataclass
class Harness:
    """One scratch database and the fakes that drive it."""

    settings: Settings
    engine: Any
    database_url: str
    model: FakeModel
    connector: FakeConnector

    def maker(self) -> async_sessionmaker[AsyncSession]:
        """Return a session factory for the scratch database."""
        return _maker(self.engine)


async def _sandbox_image(settings: Settings) -> str | None:
    """Return one allowed image that Docker holds, or None."""
    if not docker_binary_available():
        return None
    runner = SandboxRunner(settings)
    for image in resolve_image_allowlist(settings):
        if await runner.image_available(image):
            return image
    return None


async def _require_scratch_database(settings: Settings) -> tuple[Any, str]:
    """Create a throwaway database and return its engine and URL.

    Raises:
        pytest.skip.Exception: PostgreSQL is not reachable.
    """
    base_url, _, _ = settings.DATABASE_URL.rpartition("/")
    admin_url = f"{base_url}/postgres"
    db_name = f"v08_e2e_{uuid4().hex[:10]}"
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(sa.text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        await admin.dispose()
        pytest.skip(f"PostgreSQL test database not available: {exc}")

    async with admin.connect() as conn:
        await conn.execute(sa.text(f'CREATE DATABASE "{db_name}"'))

    engine = create_async_engine(f"{base_url}/{db_name}")
    async with engine.begin() as conn:
        await conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)

    return engine, f"{base_url}/{db_name}"


async def _drop_scratch_database(engine: Any, database_url: str) -> None:
    """Dispose the engine and drop the throwaway database."""
    base_url, _, db_name = database_url.rpartition("/")
    admin_url = f"{base_url}/postgres"
    await engine.dispose()
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(
                sa.text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
            )
    except Exception:  # pragma: no cover - environment dependent
        pass
    finally:
        await admin.dispose()


def _maker(engine: Any) -> async_sessionmaker[AsyncSession]:
    """Return a session factory for one engine."""
    return async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )


@pytest_asyncio.fixture
async def phase4(tmp_path: Path) -> AsyncIterator[Harness]:
    """Yield a scratch database, a real sandbox image, a fake model and a fake connector."""
    probe = SandboxE2ESettings(APP_ENV="test", DEBUG=True)
    image = await _sandbox_image(probe)
    if image is None:
        pytest.skip(
            "Docker is absent or holds no allowed sandbox image "
            f"({', '.join(CANDIDATE_IMAGES)}), so the real sandbox cannot run"
        )
    settings = SandboxE2ESettings(
        APP_ENV="test",
        DEBUG=True,
        WORKSPACE_BASE_PATH=str(tmp_path / "workspaces"),
        SANDBOX_IMAGE_ALLOWLIST=image,
    )
    engine, database_url = await _require_scratch_database(settings)
    try:
        yield Harness(
            settings=settings,
            engine=engine,
            database_url=database_url,
            model=FakeModel(),
            connector=FakeConnector(),
        )
    finally:
        await _drop_scratch_database(engine, database_url)


async def _seed_card_and_config(
    session: AsyncSession,
    approval_policy: dict[str, str] | None = None,
) -> tuple[UUID, UUID]:
    """Insert one user, one card and one agent configuration."""
    user = UserModel(id=uuid4(), display_name="E2E user", settings={})
    session.add(user)
    await session.flush()
    card = CardModel(
        id=uuid4(),
        user_id=user.id,
        type="follow_up",
        urgency="normal",
        title="Send the Phase 4 report",
        summary="The card that starts the run.",
    )
    config = AgentConfigModel(
        id=uuid4(),
        name=f"phase4-e2e-{uuid4().hex[:8]}",
        system_prompt="You are Virgil. Deliver the report and send the mail.",
        model="fake-model",
        tools=["bash", "deliver_file", "generate_document"],
        approval_policy=(
            approval_policy if approval_policy is not None else {EXTERNAL_TOOL: "external"}
        ),
        limits={"max_cost_micros": 0},
    )
    session.add(card)
    session.add(config)
    await session.commit()
    return card.id, config.id


def _build_app(settings: Settings, runs: Any, approvals: Any):
    """Build a test app whose run and approval dependencies use the scratch database."""
    app = create_app(settings)
    app.dependency_overrides[get_runs_service] = lambda: runs
    app.dependency_overrides[get_approvals_service] = lambda: approvals
    return app


async def _claim_and_process(
    harness: Harness,
    session: AsyncSession,
    worker_id: str,
    observer: RecordingObserver,
) -> Any:
    """Claim one job for a worker and run it through the real dispatch table."""
    jobs = JobsService(session=session, settings=harness.settings)
    job = await jobs.claim_next_job(worker_id=worker_id)
    assert job is not None, f"worker {worker_id} found no job to claim"
    context = JobContext(
        settings=harness.settings,
        session=session,
        llm=harness.model,
        mcp_host=harness.connector,
        observer=observer,
    )
    await process_job(job, jobs, context)
    stored = await jobs.get_by_id(job.id)
    assert stored is not None
    assert stored.status == "completed", f"job {job.type} failed: {stored.last_error}"
    return job


async def _read_rows(engine: Any, run_id: UUID) -> dict[str, Any]:
    """Read the durable rows of one run through a separate session."""
    async with _maker(engine)() as session:
        run = await session.get(RunModel, run_id)
        assert run is not None
        messages = (
            (
                await session.execute(
                    select(MessageModel)
                    .where(MessageModel.run_id == run_id)
                    .order_by(MessageModel.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        approvals = (
            (
                await session.execute(
                    select(ApprovalModel)
                    .where(ApprovalModel.run_id == run_id)
                    .order_by(ApprovalModel.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        artifacts = (
            (
                await session.execute(
                    select(ArtifactModel).where(ArtifactModel.run_id == run_id)
                )
            )
            .scalars()
            .all()
        )
        jobs = (
            (
                await session.execute(
                    select(JobModel)
                    .where(JobModel.payload["run_id"].astext == str(run_id))
                    .order_by(JobModel.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        return {
            "run": run,
            "messages": list(messages),
            "approvals": list(approvals),
            "artifacts": list(artifacts),
            "jobs": list(jobs),
        }


@pytest.mark.asyncio
async def test_run_with_approval_completes(phase4: Harness) -> None:
    """Drive one card run from queued to completed with an approval in the middle."""
    harness = phase4
    settings = harness.settings
    maker = harness.maker()
    model = harness.model
    connector = harness.connector

    # One card starts one run through the API.
    async with maker() as api_session:
        card_id, config_id = await _seed_card_and_config(api_session)
        runs_api = RunsService(session=api_session, settings=settings)
        approvals_api = ApprovalsService(session=api_session, settings=settings)
        app = _build_app(settings, runs_api, approvals_api)
        mounted = app.openapi()["paths"]
        assert "/api/v1/runs/{run_id}/events" in mounted
        assert "/api/v1/approvals/{approval_id}/decision" in mounted
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            created = await client.post(
                "/api/v1/runs",
                json={
                    "config_id": str(config_id),
                    "card_id": str(card_id),
                    "initial_prompt": "Send the Phase 4 report.",
                },
                headers=AUTH_HEADERS,
            )
    assert created.status_code == 201, created.text
    body = created.json()
    run_id = UUID(body["id"])
    assert body["status"] == "queued"
    assert UUID(body["card_id"]) == card_id

    # Worker A claims the execute job and pauses the run on the approval.
    observer_a = RecordingObserver()
    session_a = maker()
    try:
        job_a = await _claim_and_process(harness, session_a, "worker-a", observer_a)
        assert job_a.type == "execute_run"
    finally:
        await session_a.close()

    paused = await _read_rows(harness.engine, run_id)
    assert paused["run"].status == "awaiting_approval"
    assert [row.tool_name for row in paused["messages"] if row.role == "tool"] == [
        "bash",
        "deliver_file",
    ]
    assert len(paused["approvals"]) == 1
    approval = paused["approvals"][0]
    assert approval.status == "pending"
    assert approval.tool_name == EXTERNAL_TOOL
    assert approval.arguments == EXTERNAL_ARGUMENTS
    assert connector.calls == []
    assert observer_a.stages == list(FIRST_STAGES)

    # A new worker engine proves that the pause lives in the database.
    engine_b = create_async_engine(harness.database_url)
    try:
        fresh_session = _maker(engine_b)()
        try:
            fresh = RunsService(session=fresh_session, settings=settings)
            assert isinstance(fresh.journal, SqlRunJournal)
            assert not isinstance(fresh.journal, InMemoryRunJournal)
            assert fresh.session is fresh_session
            assert fresh_session is not session_a
            assert fresh_session.bind is not session_a.bind
            assert not hasattr(RunsService, "_runs")
            loaded = await fresh.get_run_by_id(run_id)
            assert loaded is not None
            assert loaded.status == "awaiting_approval"

            # The user approves through the API. It queues one resume job.
            approvals_b = ApprovalsService(session=fresh_session, settings=settings)
            app_b = _build_app(settings, fresh, approvals_b)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app_b), base_url="http://test"
            ) as client:
                decision = await client.post(
                    f"/api/v1/approvals/{approval.id}/decision",
                    json={"decision": "approved", "decision_note": "Send it"},
                    headers=AUTH_HEADERS,
                )
                pending_list = await client.get(
                    "/api/v1/approvals", headers=AUTH_HEADERS
                )
            assert decision.status_code == 202, decision.text
            assert decision.json()["status"] == "approved"
            assert pending_list.json() == []
        finally:
            await fresh_session.close()
    finally:
        await engine_b.dispose()

    decided = await _read_rows(harness.engine, run_id)
    assert decided["approvals"][0].status == "approved"
    resume_jobs = [job for job in decided["jobs"] if job.type == "resume_run"]
    assert len(resume_jobs) == 1
    assert resume_jobs[0].status == "pending"
    assert resume_jobs[0].priority == JOB_TYPE_PRIORITIES["resume_run"]
    assert resume_jobs[0].payload["run_id"] == str(run_id)
    assert connector.calls == []

    # Worker B rebuilds everything from rows and finishes the run.
    observer_b = RecordingObserver()
    engine_c = create_async_engine(harness.database_url)
    try:
        session_c = _maker(engine_c)()
        try:
            job_b = await _claim_and_process(harness, session_c, "worker-b", observer_b)
            assert job_b.type == "resume_run"
        finally:
            await session_c.close()
    finally:
        await engine_c.dispose()

    final = await _read_rows(harness.engine, run_id)
    assert final["run"].status == "completed"
    assert final["run"].ended_at is not None
    assert final["run"].token_usage["total"] == 380

    assert [row.tool_name for row in final["messages"] if row.role == "tool"] == [
        "bash",
        "deliver_file",
        EXTERNAL_TOOL,
    ]
    assert final["messages"][-1].role == "assistant"
    assert final["messages"][-1].content == (
        "The report is delivered and the email is approved."
    )

    assert [row.tool_name for row in final["approvals"]] == [EXTERNAL_TOOL]
    assert final["approvals"][0].status == "approved"
    assert [row for row in final["approvals"] if row.status == "pending"] == []

    assert len(final["artifacts"]) == 1, [
        row.content for row in final["messages"] if row.role == "tool"
    ]
    artifact = final["artifacts"][0]
    assert artifact.kind == "document"
    assert Path(artifact.path).name == REPORT_NAME
    assert Path(artifact.path).read_bytes() == REPORT_TEXT.encode("utf-8")
    assert artifact.size_bytes == len(REPORT_TEXT.encode("utf-8"))

    assert observer_a.stages == list(FIRST_STAGES)
    assert observer_b.stages == list(RESUME_STAGES)

    assert len(model.calls) == 2
    assert model.tool_result_order(1) == [
        BASH_CALL_ID,
        EXTERNAL_CALL_ID,
        DELIVER_CALL_ID,
    ]
    assert connector.tool_names() == [EXTERNAL_TOOL]
    assert connector.calls[0][2] == EXTERNAL_ARGUMENTS


@pytest.mark.asyncio
async def test_background_tool_resumes_the_run(phase4: Harness) -> None:
    """Run one background tool job, then resume the run to completion."""
    harness = phase4
    harness.model = FakeModel([background_response(), final_response()])
    settings = harness.settings
    maker = harness.maker()

    async with maker() as api_session:
        card_id, config_id = await _seed_card_and_config(
            api_session, approval_policy={"bash": "background"}
        )
        service = RunsService(session=api_session, settings=settings)
        run = await service.start_run(
            config_id=config_id, card_id=card_id, initial_prompt="Write the report."
        )
        run_id = run.id
    assert run.status == "queued"

    # Worker A runs the loop, which enqueues the background tool call.
    observer_a = RecordingObserver()
    session_a = maker()
    try:
        job_a = await _claim_and_process(harness, session_a, "worker-a", observer_a)
        assert job_a.type == "execute_run"
    finally:
        await session_a.close()

    paused = await _read_rows(harness.engine, run_id)
    assert paused["run"].status == "awaiting_background_job"
    assert paused["messages"][-1].tool_calls[0]["id"] == BASH_CALL_ID
    tool_jobs = [
        job
        for job in paused["jobs"]
        if job.payload.get("kind") == "tool_call"
    ]
    assert len(tool_jobs) == 1
    assert tool_jobs[0].status == "pending"
    assert tool_jobs[0].payload["tool_name"] == "bash"
    assert observer_a.stages == list(FIRST_STAGES)

    # Worker B claims the tool call job, stores the result and wakes the run.
    observer_b = RecordingObserver()
    session_b = maker()
    try:
        job_b = await _claim_and_process(harness, session_b, "worker-b", observer_b)
        assert job_b.type == "execute_run"
        assert job_b.payload["kind"] == "tool_call"
    finally:
        await session_b.close()

    after_tool = await _read_rows(harness.engine, run_id)
    tool_jobs = [
        job for job in after_tool["jobs"] if job.payload.get("kind") == "tool_call"
    ]
    assert [job.status for job in tool_jobs] == ["completed"]
    assert tool_jobs[0].payload["result"]["ok"] is True
    resume_jobs = [job for job in after_tool["jobs"] if job.type == "resume_run"]
    assert len(resume_jobs) == 1
    assert resume_jobs[0].status == "pending"
    assert observer_b.stages == []

    # Worker C rebuilds the loop from rows and records the background result.
    observer_c = RecordingObserver()
    session_c = maker()
    try:
        job_c = await _claim_and_process(harness, session_c, "worker-c", observer_c)
        assert job_c.type == "resume_run"
    finally:
        await session_c.close()

    final = await _read_rows(harness.engine, run_id)
    assert final["run"].status == "completed"
    assert [row.tool_name for row in final["messages"] if row.role == "tool"] == [
        "bash"
    ]
    assert observer_c.stages == list(FINAL_STAGES)
    assert len(harness.model.calls) == 2
    assert harness.connector.calls == []
