"""Integration tests for Postgres-backed atomic job queue and worker reaper."""
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from uuid import uuid4
import asyncio
import pytest

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.config.settings import Settings
from src.modules.jobs.repository import JobCreateDTO
from src.modules.jobs.service import (
    JOB_TYPE_PRIORITIES,
    STALE_LOCK_THRESHOLD_SECONDS,
    JobsService,
    priority_for_job_type,
)
from src.schemas.system import JobModel
from src.worker import (
    JobContext,
    UnknownJobTypeError,
    dispatch_job,
    process_job,
)


@pytest.mark.asyncio
async def test_job_enqueue_and_claim_lifecycle() -> None:
    """Verify job transitions: pending -> locked -> completed."""
    settings = Settings(APP_ENV="test")
    service = JobsService(session=None, settings=settings)  # type: ignore[arg-type]
    service._jobs.clear()

    # 1. Enqueue a job
    job = await service.enqueue(
        JobCreateDTO(
            type="index_memory",
            payload={"messages_count": 5},
            max_attempts=3,
        )
    )
    assert job.status == "pending"
    assert job.type == "index_memory"
    assert job.attempts == 0

    # 2. Worker 1 claims job
    claimed = await service.claim_next_job(worker_id="worker-node-1")
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == "locked"
    assert claimed.locked_by == "worker-node-1"
    assert claimed.attempts == 1

    # 3. Worker 2 attempts claim; queue is empty
    claimed_again = await service.claim_next_job(worker_id="worker-node-2")
    assert claimed_again is None

    # 4. Worker 1 marks completed
    await service.mark_completed(claimed.id)
    updated = await service.get_by_id(claimed.id)
    assert updated is not None
    assert updated.status == "completed"
    assert updated.locked_by is None


@pytest.mark.asyncio
async def test_job_retry_backoff_and_dead_letter_failure() -> None:
    """Verify job retry with exponential backoff and transition to failed on max_attempts."""
    settings = Settings(APP_ENV="test")
    service = JobsService(session=None, settings=settings)  # type: ignore[arg-type]

    job = await service.enqueue(
        JobCreateDTO(
            type="connector_sync",
            payload={"account_id": str(uuid4())},
            max_attempts=2,
        )
    )

    # Claim attempt 1
    claimed_1 = await service.claim_next_job(worker_id="worker-1")
    assert claimed_1 is not None
    assert claimed_1.attempts == 1

    # Mark failed with 60s delay
    await service.mark_failed(claimed_1.id, error="Transient network timeout", retry_delay_seconds=60)
    failed_1 = await service.get_by_id(claimed_1.id)
    assert failed_1 is not None
    assert failed_1.status == "pending"
    assert failed_1.last_error == "Transient network timeout"
    assert failed_1.run_at > datetime.now(timezone.utc)

    # Simulate run_at due now
    from dataclasses import replace
    failed_1_entry = service._jobs[failed_1.id]
    service._jobs[failed_1.id] = replace(failed_1_entry, run_at=datetime.now(timezone.utc) - timedelta(seconds=1))

    # Claim attempt 2
    claimed_2 = await service.claim_next_job(worker_id="worker-1")
    assert claimed_2 is not None
    assert claimed_2.attempts == 2

    # Second failure reaches max_attempts -> status='failed'
    await service.mark_failed(claimed_2.id, error="Permanent authentication rejection")
    failed_2 = await service.get_by_id(claimed_2.id)
    assert failed_2 is not None
    assert failed_2.status == "failed"
    assert failed_2.last_error == "Permanent authentication rejection"


@pytest.mark.asyncio
async def test_stale_lock_reaper() -> None:
    """Verify stale locked jobs exceeding timeout are reaped back to pending."""
    settings = Settings(APP_ENV="test")
    service = JobsService(session=None, settings=settings)  # type: ignore[arg-type]

    job = await service.enqueue(
        JobCreateDTO(
            type="compaction",
            payload={"thread_id": "thread-123"},
            max_attempts=3,
        )
    )

    claimed = await service.claim_next_job(worker_id="crashed-worker")
    assert claimed is not None

    # Simulate lock 10 minutes ago
    from dataclasses import replace
    ten_min_ago = datetime.now(timezone.utc) - timedelta(minutes=10)
    service._jobs[claimed.id] = replace(service._jobs[claimed.id], locked_at=ten_min_ago)

    # Reaper runs with 5-minute timeout
    reaped_count = await service.reap_stale_locks(timeout_seconds=300)
    assert reaped_count >= 1

    reaped_job = await service.get_by_id(claimed.id)
    assert reaped_job is not None
    assert reaped_job.status == "pending"
    assert reaped_job.locked_by is None
    assert reaped_job.last_error == "Reaped from stale lock"


@pytest.mark.asyncio
async def test_concurrent_worker_claim_no_collisions() -> None:
    """Verify concurrent worker claiming claims distinct jobs without duplicates."""
    settings = Settings(APP_ENV="test")
    service = JobsService(session=None, settings=settings)  # type: ignore[arg-type]
    service._jobs.clear()

    # Enqueue 10 jobs
    job_ids = set()
    for i in range(10):
        j = await service.enqueue(
            JobCreateDTO(type=f"task_{i}", payload={"index": i}, max_attempts=1)
        )
        job_ids.add(j.id)

    # 10 workers claim concurrently
    async def worker_claim(wid: int):
        return await service.claim_next_job(worker_id=f"worker-{wid}")

    results = await asyncio.gather(*[worker_claim(i) for i in range(10)])
    claimed_jobs = [r for r in results if r is not None]

    # Every job must be claimed exactly once
    claimed_ids = [c.id for c in claimed_jobs]
    assert len(claimed_ids) == len(set(claimed_ids))
    assert set(claimed_ids) == job_ids


# ---------------------------------------------------------------------------
# V-02 queue semantics: priority and aging, heartbeat reaper, dispatch failure.
# Every case below runs on the in-memory store and on a scratch Postgres
# database, so the SQL claim path is proven as well as the fallback path.
# ---------------------------------------------------------------------------


@pytest.fixture(params=["memory", "postgres"])
async def jobs_service(request: pytest.FixtureRequest):
    """
    Yield a JobsService over the in-memory store or a scratch Postgres database.

    The Postgres case creates a throwaway database with only the jobs table, so
    a shared development database never affects a result.
    """
    settings = Settings(APP_ENV="test")

    if request.param == "memory":
        service = JobsService(session=None, settings=settings)  # type: ignore[arg-type]
        service._jobs.clear()
        try:
            yield service
        finally:
            service._jobs.clear()
        return

    admin_url = settings.DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
    db_name = f"v02_queue_{uuid4().hex[:12]}"
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(sa.text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        await admin.dispose()
        pytest.skip(f"PostgreSQL test database not available: {exc}")

    async with admin.connect() as conn:
        await conn.execute(sa.text(f'CREATE DATABASE "{db_name}"'))

    engine = create_async_engine(settings.DATABASE_URL.rsplit("/", 1)[0] + f"/{db_name}")
    async with engine.begin() as conn:
        await conn.run_sync(JobModel.__table__.create)
    session = AsyncSession(engine, expire_on_commit=False)
    service = JobsService(session=session, settings=settings)
    try:
        yield service
    finally:
        await session.close()
        await engine.dispose()
        async with admin.connect() as conn:
            await conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        await admin.dispose()


def _due_now() -> datetime:
    """Return a run_at value that is due, with a small guard against clock skew."""
    return datetime.now(timezone.utc) - timedelta(seconds=1)


async def _set_heartbeat(service: JobsService, job_id, value: datetime | None) -> None:
    """Write heartbeat_at directly, so a test can set the age of the heartbeat."""
    if service._is_db_session_active():
        await service.session.execute(
            sa.update(JobModel).where(JobModel.id == job_id).values(heartbeat_at=value)
        )
        await service.session.commit()
        return
    service._jobs[job_id] = replace(service._jobs[job_id], heartbeat_at=value)


def _context() -> JobContext:
    """Return a worker context for dispatch tests."""
    return JobContext(settings=Settings(APP_ENV="test"), session=None)


def test_claim_query_is_one_statement() -> None:
    """Verify the claim is a single UPDATE with the aged priority order and SKIP LOCKED."""
    from sqlalchemy.dialects import postgresql

    service = JobsService(session=None, settings=Settings(APP_ENV="test"))  # type: ignore[arg-type]
    stmt = service.build_claim_statement("worker-sql")
    sql = str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True, "render_postcompile": True},
        )
    ).strip()

    # One statement only: no separate read, no separate write, no INSERT.
    assert sql.count("UPDATE jobs") == 1
    assert "INSERT INTO jobs" not in sql
    assert ";" not in sql

    # The claim takes one row under a row lock and skips rows held by peers.
    assert sql.count("FOR UPDATE SKIP LOCKED") == 1
    assert "LIMIT 1" in sql

    # The order is the aged priority, then the oldest run_at.
    assert "ORDER BY jobs.priority + least(" in sql
    assert "3600" in sql
    assert "EXTRACT(epoch FROM now() - jobs.run_at)" in sql
    assert "DESC, jobs.run_at ASC" in sql

    # The claim writes the lock and returns the claimed row.
    assert "status=" in sql and "locked_by=" in sql and "locked_at=" in sql
    assert "RETURNING" in sql


@pytest.mark.asyncio
async def test_priority_beats_fifo(jobs_service: JobsService) -> None:
    """Verify one resume_run job is claimed before 1000 older poll_connector jobs."""
    for index in range(1000):
        await jobs_service.enqueue(
            JobCreateDTO(type="poll_connector", payload={"index": index}, run_at=_due_now())
        )
    urgent = await jobs_service.enqueue(
        JobCreateDTO(type="resume_run", payload={"run_id": str(uuid4())}, run_at=_due_now())
    )

    claimed = await jobs_service.claim_next_job(worker_id="worker-priority")

    assert claimed is not None
    assert claimed.id == urgent.id
    assert claimed.type == "resume_run"
    assert claimed.priority == JOB_TYPE_PRIORITIES["resume_run"]
    assert claimed.status == "locked"

    second = await jobs_service.claim_next_job(worker_id="worker-priority")
    assert second is not None
    assert second.type == "poll_connector"


@pytest.mark.asyncio
async def test_priority_aging_caps_at_one_hour(jobs_service: JobsService) -> None:
    """Verify the age credit stops at 3600 seconds and keeps FIFO order beyond it."""
    now = datetime.now(timezone.utc)
    oldest = await jobs_service.enqueue(
        JobCreateDTO(
            type="poll_connector",
            payload={"tag": "oldest"},
            run_at=now - timedelta(seconds=7200),
        )
    )
    newer = await jobs_service.enqueue(
        JobCreateDTO(
            type="reindex_memory",
            payload={"tag": "newer"},
            run_at=now - timedelta(seconds=4000),
        )
    )
    assert oldest.priority == 20
    assert newer.priority == 5

    # Both jobs are past the one hour cap, so the older run_at wins: 3620 > 3605.
    first = await jobs_service.claim_next_job(worker_id="worker-aging")
    assert first is not None
    assert first.id == oldest.id

    second = await jobs_service.claim_next_job(worker_id="worker-aging")
    assert second is not None
    assert second.id == newer.id


@pytest.mark.asyncio
async def test_reaper_uses_heartbeat(jobs_service: JobsService) -> None:
    """Verify a job whose heartbeat is 91 seconds stale returns to pending once."""
    assert STALE_LOCK_THRESHOLD_SECONDS == 90

    job = await jobs_service.enqueue(
        JobCreateDTO(type="index_memory", payload={"messages": 3}, run_at=_due_now())
    )
    claimed = await jobs_service.claim_next_job(worker_id="worker-reaper")
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == "locked"

    await _set_heartbeat(
        jobs_service, claimed.id, datetime.now(timezone.utc) - timedelta(seconds=91)
    )

    assert await jobs_service.reap_stale() == 1

    reaped = await jobs_service.get_by_id(claimed.id)
    assert reaped is not None
    assert reaped.status == "pending"
    assert reaped.locked_by is None
    assert reaped.locked_at is None
    assert reaped.heartbeat_at is None
    assert reaped.last_error == "Reaped from stale lock"

    # Exactly once: the second pass finds the job pending and does nothing.
    assert await jobs_service.reap_stale() == 0
    still_pending = await jobs_service.get_by_id(claimed.id)
    assert still_pending is not None
    assert still_pending.status == "pending"


@pytest.mark.asyncio
async def test_long_job_is_not_reaped(jobs_service: JobsService) -> None:
    """Verify a job that sends heartbeats for six minutes is never reaped."""
    clock = [datetime.now(timezone.utc)]
    jobs_service.clock = lambda: clock[0]

    job = await jobs_service.enqueue(
        JobCreateDTO(
            type="execute_run",
            payload={"run_id": str(uuid4())},
            run_at=clock[0] - timedelta(seconds=1),
        )
    )
    claimed = await jobs_service.claim_next_job(worker_id="worker-long")
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == "locked"

    reaps: list[int] = []
    for step in range(1, 13):
        # 12 steps of 30 seconds is six minutes of simulated time.
        clock[0] = clock[0] + timedelta(seconds=30)
        assert await jobs_service.heartbeat(claimed.id, worker_id="worker-long") is True
        if step % 2 == 0:
            # The reaper runs once per minute of simulated time.
            reaps.append(await jobs_service.reap_stale())

    assert reaps == [0, 0, 0, 0, 0, 0]

    final = await jobs_service.get_by_id(claimed.id)
    assert final is not None
    assert final.status == "locked"
    assert final.locked_by == "worker-long"
    assert final.attempts == 1
    assert final.heartbeat_at is not None


@pytest.mark.asyncio
async def test_heartbeat_rejects_another_worker(jobs_service: JobsService) -> None:
    """Verify only the worker that holds the lock can refresh the heartbeat."""
    job = await jobs_service.enqueue(
        JobCreateDTO(type="cleanup_sandbox", payload={}, run_at=_due_now())
    )
    claimed = await jobs_service.claim_next_job(worker_id="worker-owner")
    assert claimed is not None
    assert claimed.id == job.id

    assert await jobs_service.heartbeat(claimed.id, worker_id="worker-other") is False
    assert await jobs_service.heartbeat(uuid4(), worker_id="worker-owner") is False
    assert await jobs_service.heartbeat(claimed.id, worker_id="worker-owner") is True


@pytest.mark.asyncio
async def test_completed_job_is_not_reaped(jobs_service: JobsService) -> None:
    """Verify the reaper ignores a job that already completed."""
    job = await jobs_service.enqueue(
        JobCreateDTO(type="triage_item", payload={}, run_at=_due_now())
    )
    claimed = await jobs_service.claim_next_job(worker_id="worker-done")
    assert claimed is not None
    await jobs_service.mark_completed(claimed.id)

    assert await jobs_service.reap_stale() == 0
    done = await jobs_service.get_by_id(job.id)
    assert done is not None
    assert done.status == "completed"


@pytest.mark.asyncio
async def test_unknown_job_type_fails(jobs_service: JobsService) -> None:
    """Verify dispatch raises for an unknown type and the row ends failed, never completed."""
    job = await jobs_service.enqueue(
        JobCreateDTO(type="not_a_real_type", payload={"x": 1}, run_at=_due_now())
    )
    claimed = await jobs_service.claim_next_job(worker_id="worker-unknown")
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.type == "not_a_real_type"
    assert claimed.priority == 0
    assert priority_for_job_type("not_a_real_type") == 0

    # The dispatch itself raises.
    with pytest.raises(UnknownJobTypeError) as exc_info:
        await dispatch_job(claimed, _context())
    assert exc_info.value.job_type == "not_a_real_type"

    # The worker failure path records the error and never completes the job.
    await process_job(claimed, jobs_service, _context())

    row = await jobs_service.get_by_id(claimed.id)
    assert row is not None
    assert row.status == "failed"
    assert row.status != "completed"
    assert row.last_error is not None
    assert "not_a_real_type" in row.last_error
    assert row.locked_by is None
    assert row.heartbeat_at is None
    assert row.attempts == 1

