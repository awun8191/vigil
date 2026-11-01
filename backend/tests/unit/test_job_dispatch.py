"""Unit tests for the worker dispatch table, the heartbeat runner, and the reaper clock."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from src.config.settings import Settings
from src.modules.jobs.repository import JobDTO
from src.modules.jobs.service import (
    HEARTBEAT_INTERVAL_SECONDS,
    JOB_TYPE_PRIORITIES,
    PRIORITY_AGING_CAP_SECONDS,
    REAP_INTERVAL_SECONDS,
    STALE_LOCK_THRESHOLD_SECONDS,
    UNKNOWN_JOB_TYPE_PRIORITY,
    priority_for_job_type,
)
from src.worker import (
    JOB_HANDLERS,
    UNWIRED_JOB_TYPES,
    JobContext,
    JobHandlerUnavailableError,
    UnknownJobTypeError,
    dispatch_job,
    process_job,
    reap_is_due,
    resolve_handler,
    run_with_heartbeat,
)


def make_job(job_type: str = "poll_connector", **overrides: Any) -> JobDTO:
    """Build a locked JobDTO for dispatch tests."""
    now = datetime.now(timezone.utc)
    values: dict[str, Any] = {
        "id": uuid4(),
        "type": job_type,
        "payload": {},
        "run_at": now,
        "attempts": 1,
        "max_attempts": 5,
        "status": "locked",
        "locked_by": "worker-test",
        "locked_at": now,
        "last_error": None,
        "created_at": now,
        "updated_at": now,
        "priority": priority_for_job_type(job_type),
        "heartbeat_at": None,
    }
    values.update(overrides)
    return JobDTO(**values)


def make_context() -> JobContext:
    """Build a JobContext for dispatch tests."""
    return JobContext(settings=Settings(APP_ENV="test"))


class RecordingJobsService:
    """Records the outcome calls that process_job makes."""

    def __init__(self) -> None:
        self.completed: list[Any] = []
        self.failed: list[tuple[Any, str, int | None, bool]] = []

    async def mark_completed(self, job_id: Any) -> None:
        self.completed.append(job_id)

    async def mark_failed(
        self,
        job_id: Any,
        error: str,
        retry_delay_seconds: int | None = None,
        permanent: bool = False,
    ) -> None:
        self.failed.append((job_id, error, retry_delay_seconds, permanent))


# ---------------------------------------------------------------------------
# Fixed policy values
# ---------------------------------------------------------------------------

def test_priority_table_is_fixed() -> None:
    """Verify the fixed priority value of every known job type."""
    assert JOB_TYPE_PRIORITIES == {
        "resume_run": 100,
        "execute_run": 90,
        "triage_item": 60,
        "index_memory": 50,
        "poll_connector": 20,
        "cleanup_sandbox": 10,
        "reindex_memory": 5,
    }
    for job_type, expected in JOB_TYPE_PRIORITIES.items():
        assert priority_for_job_type(job_type) == expected


def test_priority_table_orders_resume_run_first() -> None:
    """Verify the claim order of the priority table is strict, with no tie."""
    ordered = sorted(JOB_TYPE_PRIORITIES.items(), key=lambda item: item[1], reverse=True)
    assert [name for name, _ in ordered] == [
        "resume_run",
        "execute_run",
        "triage_item",
        "index_memory",
        "poll_connector",
        "cleanup_sandbox",
        "reindex_memory",
    ]


def test_unknown_type_priority_is_zero() -> None:
    """Verify an unknown job type gets the lowest fixed priority."""
    assert UNKNOWN_JOB_TYPE_PRIORITY == 0
    assert priority_for_job_type("not_a_real_type") == 0
    assert priority_for_job_type("") == 0


def test_queue_policy_constants() -> None:
    """Verify the heartbeat, reaper, aging, and reaper interval values."""
    assert HEARTBEAT_INTERVAL_SECONDS == 30
    assert STALE_LOCK_THRESHOLD_SECONDS == 90
    assert PRIORITY_AGING_CAP_SECONDS == 3600
    assert REAP_INTERVAL_SECONDS == 60


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

def test_dispatch_table_covers_every_priority_type() -> None:
    """Verify the dispatch table has one handler for every known job type."""
    assert set(JOB_HANDLERS) == set(JOB_TYPE_PRIORITIES)
    for handler in JOB_HANDLERS.values():
        assert callable(handler)


@pytest.mark.asyncio
async def test_dispatch_routes_to_the_registered_handler() -> None:
    """Verify dispatch_job calls the handler that the job type selects."""
    calls: list[str] = []

    async def resume_handler(job: JobDTO, context: JobContext) -> None:
        calls.append("resume_run")

    async def poll_handler(job: JobDTO, context: JobContext) -> None:
        calls.append("poll_connector")

    handlers = {"resume_run": resume_handler, "poll_connector": poll_handler}
    await dispatch_job(make_job("resume_run"), make_context(), handlers)
    assert calls == ["resume_run"]


@pytest.mark.asyncio
async def test_dispatch_passes_the_job_and_the_context() -> None:
    """Verify the handler receives the claimed job and the job context."""
    seen: list[tuple[Any, Any]] = []

    async def handler(job: JobDTO, context: JobContext) -> None:
        seen.append((job, context))

    job = make_job("index_memory")
    context = make_context()
    await dispatch_job(job, context, {"index_memory": handler})
    assert seen == [(job, context)]


def test_resolve_handler_returns_the_default_entry() -> None:
    """Verify resolve_handler reads the shipped dispatch table by default."""
    assert resolve_handler("triage_item") is JOB_HANDLERS["triage_item"]


def test_unknown_type_raises() -> None:
    """Verify an unknown job type raises instead of reporting success."""
    with pytest.raises(UnknownJobTypeError) as exc_info:
        resolve_handler("not_a_real_type")
    assert exc_info.value.job_type == "not_a_real_type"
    assert "not_a_real_type" in str(exc_info.value)


@pytest.mark.asyncio
async def test_dispatch_raises_for_unknown_type() -> None:
    """Verify dispatch_job raises UnknownJobTypeError and runs no handler."""
    calls: list[str] = []

    async def handler(job: JobDTO, context: JobContext) -> None:
        calls.append(job.type)

    with pytest.raises(UnknownJobTypeError):
        await dispatch_job(make_job("not_a_real_type"), make_context(), {"resume_run": handler})
    assert calls == []


@pytest.mark.asyncio
async def test_handler_error_propagates() -> None:
    """Verify an error in a registered handler reaches the caller."""

    async def broken(job: JobDTO, context: JobContext) -> None:
        raise RuntimeError("handler exploded")

    with pytest.raises(RuntimeError, match="handler exploded"):
        await dispatch_job(make_job("execute_run"), make_context(), {"execute_run": broken})


@pytest.mark.asyncio
@pytest.mark.parametrize("job_type", sorted(set(JOB_TYPE_PRIORITIES) & UNWIRED_JOB_TYPES))
async def test_default_handlers_never_report_success(job_type: str) -> None:
    """Verify a handler without an executor raises instead of returning.

    The set comes from the worker, not from this file, so a handler that gains
    an executor leaves the set and a handler that loses one enters it. The
    coverage test above keeps the table and the priority list equal.
    """
    with pytest.raises(JobHandlerUnavailableError) as exc_info:
        await JOB_HANDLERS[job_type](make_job(job_type), make_context())
    assert exc_info.value.job_type == job_type


@pytest.mark.asyncio
async def test_index_memory_handler_runs_the_memory_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify index_memory hands the job payload to the memory service."""
    seen: list[dict[str, Any]] = []

    class RecordingMemoryService:
        """Memory service double that records the payload it receives."""

        async def run_index_job(self, payload: dict[str, Any]) -> None:
            seen.append(dict(payload))

    monkeypatch.setattr(
        "src.worker.get_memory_service", lambda **kwargs: RecordingMemoryService()
    )
    session_id = str(uuid4())
    job = make_job(
        "index_memory", payload={"session_id": session_id, "scope": "global"}
    )

    await JOB_HANDLERS["index_memory"](job, make_context())

    assert seen == [{"session_id": session_id, "scope": "global"}]


# ---------------------------------------------------------------------------
# Heartbeat runner
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_heartbeat_sent_while_handler_runs() -> None:
    """Verify the heartbeat runs on the interval while a slow handler works."""
    beats: list[float] = []

    async def heartbeat() -> None:
        beats.append(1.0)

    async def slow_job(job: JobDTO, context: JobContext) -> None:
        await asyncio.sleep(0.12)

    await run_with_heartbeat(
        make_job("execute_run"), slow_job, make_context(), heartbeat, interval_seconds=0.02
    )
    assert len(beats) >= 2


@pytest.mark.asyncio
async def test_no_heartbeat_for_short_handler() -> None:
    """Verify a handler that returns at once sends no heartbeat."""
    beats: list[float] = []

    async def heartbeat() -> None:
        beats.append(1.0)

    async def quick_job(job: JobDTO, context: JobContext) -> None:
        return None

    await run_with_heartbeat(
        make_job("poll_connector"), quick_job, make_context(), heartbeat, interval_seconds=0.05
    )
    assert beats == []


@pytest.mark.asyncio
async def test_heartbeat_stops_after_handler_completes() -> None:
    """Verify the heartbeat loop ends with the handler."""
    beats: list[float] = []

    async def heartbeat() -> None:
        beats.append(1.0)

    async def slow_job(job: JobDTO, context: JobContext) -> None:
        await asyncio.sleep(0.06)

    await run_with_heartbeat(
        make_job("execute_run"), slow_job, make_context(), heartbeat, interval_seconds=0.01
    )
    count = len(beats)
    assert count >= 1
    await asyncio.sleep(0.06)
    assert len(beats) == count


@pytest.mark.asyncio
async def test_handler_error_propagates_through_heartbeat_runner() -> None:
    """Verify a handler error is not hidden by the heartbeat runner."""

    async def heartbeat() -> None:
        return None

    async def broken(job: JobDTO, context: JobContext) -> None:
        await asyncio.sleep(0.01)
        raise ValueError("no executor")

    with pytest.raises(ValueError, match="no executor"):
        await run_with_heartbeat(
            make_job("triage_item"), broken, make_context(), heartbeat, interval_seconds=0.01
        )


# ---------------------------------------------------------------------------
# Reaper clock
# ---------------------------------------------------------------------------

def test_reaper_is_due_on_the_first_poll() -> None:
    """Verify the reaper runs on the first poll because last_reap starts at zero."""
    assert reap_is_due(1_000.0, 0.0) is True


def test_reaper_is_not_due_before_the_interval() -> None:
    """Verify the reaper stays idle between fixed intervals."""
    assert reap_is_due(1_000.0, 1_000.0) is False
    assert reap_is_due(1_059.0, 1_000.0) is False
    assert reap_is_due(1_060.0, 1_000.0) is True


# ---------------------------------------------------------------------------
# process_job outcome recording
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_job_completes_only_on_success() -> None:
    """Verify a handler that returns marks the job completed."""
    service = RecordingJobsService()

    async def handler(job: JobDTO, context: JobContext) -> None:
        return None

    job = make_job("cleanup_sandbox")
    await process_job(job, service, make_context(), handlers={"cleanup_sandbox": handler})  # type: ignore[arg-type]
    assert service.completed == [job.id]
    assert service.failed == []


@pytest.mark.asyncio
async def test_process_job_unknown_type_fails_permanently() -> None:
    """Verify an unknown type goes to the failure path and is never completed."""
    service = RecordingJobsService()
    job = make_job("not_a_real_type")
    await process_job(job, service, make_context())  # type: ignore[arg-type]
    assert service.completed == []
    assert len(service.failed) == 1
    failed_id, error, retry_delay, permanent = service.failed[0]
    assert failed_id == job.id
    assert "not_a_real_type" in error
    assert retry_delay is None
    assert permanent is True


@pytest.mark.asyncio
async def test_process_job_handler_error_fails_with_retry() -> None:
    """Verify a handler error goes to the failure path with a retry delay."""
    service = RecordingJobsService()

    async def broken(job: JobDTO, context: JobContext) -> None:
        raise OSError("connector timeout")

    job = make_job("poll_connector")
    await process_job(job, service, make_context(), handlers={"poll_connector": broken})  # type: ignore[arg-type]
    assert service.completed == []
    assert len(service.failed) == 1
    failed_id, error, retry_delay, permanent = service.failed[0]
    assert failed_id == job.id
    assert "connector timeout" in error
    assert retry_delay is not None and retry_delay > 0
    assert permanent is False
