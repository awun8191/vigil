"""Integration tests for the approval queue and the decision endpoint.

A decision must be asynchronous. The endpoint writes the approval row and
enqueues one resume_run job. It never runs the tool, and a second decision of
the same approval conflicts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config.settings import Settings
from src.dependencies import get_approvals_service
from src.main import create_app
from src.modules.approvals.repository import ApprovalCreateDTO
from src.modules.approvals.service import REJECTION_MESSAGE, USER_REJECTED, ApprovalsService
from src.modules.jobs.service import JOB_TYPE_PRIORITIES, JobsService
from src.modules.runs.repository import MessageCreateDTO, RunCreateDTO
from src.modules.runs.service import RunsService
from src.schemas import Base
from src.schemas.runtime import ApprovalModel, MessageModel, RunModel
from src.schemas.system import JobModel


AUTH_HEADERS = {"Authorization": "Bearer vgt_testtoken"}
"""Bearer token that the test environment accepts."""

DECISION_RESPONSE_KEYS = {"approval_id", "status", "run_id", "resumed"}
"""The complete decision response. No key of this set carries a tool result."""

TOOL_OUTPUT_KEYS = {"result", "output", "tool_result", "tool_output", "content"}
"""Keys that would leak a tool result from the decision endpoint."""


@dataclass
class ApprovalHarness:
    """Approval service, queue, and run store over one storage backend."""

    label: str
    service: ApprovalsService
    jobs: JobsService
    runs: RunsService
    session: AsyncSession | None
    engine: Any | None = None

    def _fresh_session(self) -> AsyncSession:
        """Open a separate session, so a read proves the row was committed."""
        maker = async_sessionmaker(
            bind=self.engine, class_=AsyncSession, expire_on_commit=False
        )
        return maker()

    async def new_run(self) -> UUID:
        """Create one run in the awaiting_approval status."""
        now = datetime.now(timezone.utc)
        if self.session is not None:
            run_id = uuid4()
            self.session.add(
                RunModel(id=run_id, config_id=None, status="awaiting_approval", started_at=now)
            )
            await self.session.commit()
            return run_id
        run = await self.runs.create_run(
            RunCreateDTO(config_id=uuid4(), status="awaiting_approval")
        )
        return run.id

    async def queued_jobs(self) -> list[tuple[str, int, str, dict[str, Any]]]:
        """Return (type, priority, status, payload) of every queued job."""
        if self.engine is not None:
            async with self._fresh_session() as session:
                result = await session.execute(select(JobModel))
                return [
                    (row.type, row.priority, row.status, row.payload)
                    for row in result.scalars().all()
                ]
        return [
            (job.type, job.priority, job.status, job.payload)
            for job in JobsService._jobs.values()
        ]

    async def messages(self, run_id: UUID) -> list[dict[str, Any]]:
        """Return the stored messages of one run as plain dictionaries."""
        if self.engine is not None:
            async with self._fresh_session() as session:
                result = await session.execute(
                    select(MessageModel)
                    .where(MessageModel.run_id == run_id)
                    .order_by(MessageModel.created_at)
                )
                rows = result.scalars().all()
                return [
                    {
                        "role": row.role,
                        "tool_call_id": row.tool_call_id,
                        "tool_name": row.tool_name,
                        "content": row.content,
                        "tool_calls": row.tool_calls,
                    }
                    for row in rows
                ]
        stored = await self.runs.get_messages_for_run(run_id)
        return [
            {
                "role": message.role,
                "tool_call_id": message.tool_call_id,
                "tool_name": message.tool_name,
                "content": message.content,
                "tool_calls": message.tool_calls,
            }
            for message in stored
        ]

    async def stored_approval(self, approval_id: UUID) -> dict[str, Any] | None:
        """Read one approval in a separate session, so the read sees committed data."""
        if self.engine is not None:
            async with self._fresh_session() as session:
                result = await session.execute(
                    select(ApprovalModel).where(ApprovalModel.id == approval_id)
                )
                row = result.scalar_one_or_none()
                if row is None:
                    return None
                return {
                    "status": row.status,
                    "decided_at": row.decided_at,
                    "decision_note": row.decision_note,
                }
        approval = await self.service.get_approval_by_id(approval_id)
        if approval is None:
            return None
        return {
            "status": approval.status,
            "decided_at": approval.decided_at,
            "decision_note": approval.decision_note,
        }

    async def tool_messages(self, run_id: UUID) -> list[dict[str, Any]]:
        """Return the tool rows of one run."""
        return [m for m in await self.messages(run_id) if m["role"] == "tool"]

    async def save_assistant_proposal(
        self, run_id: UUID, tool_call_id: str, tool_name: str
    ) -> None:
        """Store the assistant turn that proposed one external tool call."""
        tool_calls = [
            {
                "id": tool_call_id,
                "type": "function",
                "function": {"name": tool_name, "arguments": "{}"},
            }
        ]
        if self.session is not None:
            self.session.add(
                MessageModel(
                    id=uuid4(),
                    run_id=run_id,
                    role="assistant",
                    content=None,
                    tool_calls=tool_calls,
                )
            )
            await self.session.commit()
            return
        await self.runs.save_message(
            MessageCreateDTO(
                run_id=run_id,
                role="assistant",
                content=None,
                tool_calls=tool_calls,
            )
        )


def _clear_stores() -> None:
    """Empty every in-memory store so one test never reads another test row."""
    ApprovalsService._approvals.clear()
    JobsService._jobs.clear()
    RunsService._runs.clear()
    RunsService._messages.clear()
    RunsService._steps.clear()
    RunsService._configs.clear()
    RunsService._configs_by_name.clear()


@pytest_asyncio.fixture(params=["memory", "postgres"])
async def harness(request: pytest.FixtureRequest) -> AsyncIterator[ApprovalHarness]:
    """
    Yield the approval stack over the in-memory store or a scratch Postgres.

    The Postgres case creates a throwaway database, so a shared development
    database never affects a result.
    """
    settings = Settings(APP_ENV="test")
    _clear_stores()

    if request.param == "memory":
        jobs = JobsService(session=None, settings=settings)  # type: ignore[arg-type]
        runs = RunsService(session=None, settings=settings)  # type: ignore[arg-type]
        service = ApprovalsService(
            session=None,  # type: ignore[arg-type]
            settings=settings,
            jobs_service=jobs,
            runs_service=runs,
        )
        yield ApprovalHarness("memory", service, jobs, runs, None)
        return

    base_url, _, _database = settings.DATABASE_URL.rpartition("/")
    admin_url = f"{base_url}/postgres"
    db_name = f"v07_approvals_{uuid4().hex[:10]}"
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
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)

        maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        session = maker()
        try:
            jobs = JobsService(session=session, settings=settings)
            runs = RunsService(session=session, settings=settings)  # type: ignore[arg-type]
            service = ApprovalsService(
                session=session,
                settings=settings,
                jobs_service=jobs,
                runs_service=runs,
            )
            yield ApprovalHarness("postgres", service, jobs, runs, session, engine)
        finally:
            await session.close()
    finally:
        await engine.dispose()
        try:
            async with admin.connect() as conn:
                await conn.execute(
                    sa.text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
                )
        except Exception:  # pragma: no cover - environment dependent
            pass
        await admin.dispose()


def _build_app(harness: ApprovalHarness):
    """Build a test app whose approvals dependency returns the harness service."""
    app = create_app(Settings(DEBUG=True, APP_ENV="test"))
    app.dependency_overrides[get_approvals_service] = lambda: harness.service
    return app


async def _create_pending_approval(harness: ApprovalHarness) -> tuple[UUID, UUID]:
    """Create one run and one pending approval of an external send tool."""
    run_id = await harness.new_run()
    approval = await harness.service.create_approval(
        ApprovalCreateDTO(
            run_id=run_id,
            tool_call_id="call_gmail_send_1",
            tool_name="gmail_send",
            arguments={"to": "customer@example.com", "subject": "Invoice"},
        )
    )
    return run_id, approval.id


@pytest.mark.asyncio
async def test_decision_is_async(harness: ApprovalHarness) -> None:
    """A decision returns 202, queues one resume_run job, and runs no tool."""
    run_id, approval_id = await _create_pending_approval(harness)
    app = _build_app(harness)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/v1/approvals/{approval_id}/decision",
            json={"decision": "approved", "decision_note": "Send it"},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 202
    body = response.json()
    assert set(body) == DECISION_RESPONSE_KEYS
    assert set(body).isdisjoint(TOOL_OUTPUT_KEYS)
    assert body["approval_id"] == str(approval_id)
    assert body["status"] == "approved"
    assert body["run_id"] == str(run_id)

    # Exactly one resume_run job waits for the worker, at the fixed priority.
    jobs = await harness.queued_jobs()
    assert len(jobs) == 1
    job_type, priority, job_status, payload = jobs[0]
    assert job_type == "resume_run"
    assert priority == 100
    assert JOB_TYPE_PRIORITIES["resume_run"] == 100
    assert job_status == "pending"
    assert payload["run_id"] == str(run_id)
    assert payload["approval_id"] == str(approval_id)

    # The API did not execute the tool: no tool row exists for this call.
    assert await harness.tool_messages(run_id) == []

    stored = await harness.stored_approval(approval_id)
    assert stored is not None
    assert stored["status"] == "approved"
    assert stored["decided_at"] is not None
    assert stored["decision_note"] == "Send it"


@pytest.mark.asyncio
async def test_decision_conflict(harness: ApprovalHarness) -> None:
    """A second decision of the same approval returns 409 and queues no job."""
    _run_id, approval_id = await _create_pending_approval(harness)
    app = _build_app(harness)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post(
            f"/api/v1/approvals/{approval_id}/decision",
            json={"decision": "approved"},
            headers=AUTH_HEADERS,
        )
        second = await client.post(
            f"/api/v1/approvals/{approval_id}/decision",
            json={"decision": "rejected"},
            headers=AUTH_HEADERS,
        )

    assert first.status_code == 202
    assert second.status_code == 409
    error = second.json()
    assert error["code"] == "CONFLICT"
    assert set(error) == {"code", "message", "request_id", "details"}

    # The first decision stands and only one job exists.
    jobs = await harness.queued_jobs()
    assert len(jobs) == 1
    assert jobs[0][0] == "resume_run"

    stored = await harness.stored_approval(approval_id)
    assert stored is not None
    assert stored["status"] == "approved"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        still_pending = await client.get("/api/v1/approvals", headers=AUTH_HEADERS)
    assert still_pending.status_code == 200
    listed = still_pending.json()
    assert str(approval_id) not in {item["id"] for item in listed}


@pytest.mark.asyncio
async def test_rejection_satisfies_barrier(harness: ApprovalHarness) -> None:
    """A rejected tool writes a USER_REJECTED tool row that closes the barrier."""
    run_id, approval_id = await _create_pending_approval(harness)

    # The assistant turn that proposed the tool call.
    await harness.save_assistant_proposal(run_id, "call_gmail_send_1", "gmail_send")

    app = _build_app(harness)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/v1/approvals/{approval_id}/decision",
            json={"decision": "rejected", "decision_note": "Wrong recipient"},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 202
    assert response.json()["status"] == "rejected"

    tool_rows = await harness.tool_messages(run_id)
    assert len(tool_rows) == 1
    tool_row = tool_rows[0]
    assert tool_row["tool_call_id"] == "call_gmail_send_1"
    assert tool_row["tool_name"] == "gmail_send"

    content = json.loads(tool_row["content"])
    assert content["error"] == USER_REJECTED
    assert content["error"] == "USER_REJECTED"
    assert content["decision_note"] == "Wrong recipient"
    assert content["message"] == REJECTION_MESSAGE

    # Barrier invariant: every proposed tool call has a tool result.
    messages = await harness.messages(run_id)
    proposed = {
        call["id"]
        for message in messages
        if message["role"] == "assistant" and message["tool_calls"]
        for call in message["tool_calls"]
    }
    answered = {
        message["tool_call_id"] for message in messages if message["role"] == "tool"
    }
    assert proposed == answered == {"call_gmail_send_1"}

    # The rejection also wakes the run through one queue job.
    jobs = await harness.queued_jobs()
    assert len(jobs) == 1
    assert jobs[0][0] == "resume_run"
    assert jobs[0][1] == 100


@pytest.mark.asyncio
async def test_decision_of_unknown_approval_returns_404(harness: ApprovalHarness) -> None:
    """A decision on an absent approval returns the standard 404 envelope."""
    app = _build_app(harness)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/v1/approvals/{uuid4()}/decision",
            json={"decision": "approved"},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
    assert await harness.queued_jobs() == []


@pytest.mark.asyncio
async def test_pending_approval_is_listed(harness: ApprovalHarness) -> None:
    """The list endpoint returns the pending approval and its proposed arguments."""
    _run_id, approval_id = await _create_pending_approval(harness)
    app = _build_app(harness)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/approvals", headers=AUTH_HEADERS)

    assert response.status_code == 200
    listed = response.json()
    assert len(listed) == 1
    assert listed[0]["id"] == str(approval_id)
    assert listed[0]["tool_name"] == "gmail_send"
    assert listed[0]["status"] == "pending"
    assert listed[0]["arguments"]["to"] == "customer@example.com"
