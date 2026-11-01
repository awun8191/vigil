"""Jobs repository protocol and data transfer objects."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID


@dataclass(frozen=True)
class JobDTO:
    """Represents an asynchronous background job record."""
    id: UUID
    type: str  # poll_connector, triage_item, execute_run, resume_run, index_memory, cleanup_sandbox
    payload: dict[str, Any]
    run_at: datetime
    attempts: int
    max_attempts: int
    status: str  # pending, locked, completed, failed
    locked_by: str | None
    locked_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    priority: int = 0
    heartbeat_at: datetime | None = None


@dataclass(frozen=True)
class JobCreateDTO:
    """Payload for enqueuing a new background job."""
    type: str
    payload: dict[str, Any]
    run_at: datetime | None = None
    max_attempts: int = 5


@runtime_checkable
class JobsRepository(Protocol):
    """Protocol contract for atomic queue claiming, failure retries, and stale job reaping."""

    async def enqueue(self, data: JobCreateDTO) -> JobDTO:
        """
        Enqueue a new job for background processing.

        Args:
            data: Job creation DTO.

        Returns:
            The enqueued JobDTO.
        """
        ...

    async def claim_next_job(self, worker_id: str) -> JobDTO | None:
        """
        Atomically claim the next runnable job using SELECT FOR UPDATE SKIP LOCKED.

        Args:
            worker_id: String identifying the claiming worker process.

        Returns:
            Claimed JobDTO locked by worker, or None if no eligible jobs exist.
        """
        ...

    async def mark_completed(self, job_id: UUID) -> None:
        """
        Mark a claimed job as successfully completed.

        Args:
            job_id: UUID of the job.
        """
        ...

    async def mark_failed(
        self,
        job_id: UUID,
        error: str,
        retry_delay_seconds: int | None = None,
    ) -> None:
        """
        Record job execution failure; reschedule with backoff or mark failed if attempts exceeded.

        Args:
            job_id: UUID of the job.
            error: Error message string or traceback snippet.
            retry_delay_seconds: Optional delay before next attempt; if None, uses exponential backoff.
        """
        ...

    async def reap_stale(self, stale_threshold_seconds: int = 300) -> int:
        """
        Recover jobs abandoned in locked status by crashed workers.

        Args:
            stale_threshold_seconds: Age in seconds after which locked jobs are considered abandoned.

        Returns:
            Count of recovered jobs reset to 'pending' status.
        """
        ...

    async def get_by_id(self, job_id: UUID) -> JobDTO | None:
        """
        Retrieve a job by its primary key.

        Args:
            job_id: UUID of the job.

        Returns:
            JobDTO if found, None otherwise.
        """
        ...

    async def get_queue_stats(self) -> dict[str, int]:
        """
        Retrieve aggregate counts of jobs by status for telemetry and monitoring.

        Returns:
            Dictionary mapping status ('pending', 'locked', 'completed', 'failed') to counts.
        """
        ...
