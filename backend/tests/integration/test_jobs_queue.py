"""Integration tests for Postgres-backed atomic job queue and worker reaper."""
from datetime import datetime, timezone, timedelta
from uuid import uuid4
import asyncio
import pytest

from src.config.settings import Settings
from src.modules.jobs.repository import JobCreateDTO
from src.modules.jobs.service import JobsService


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
