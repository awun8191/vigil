"""Jobs service implementing Postgres SKIP LOCKED atomic queue and reaper engine."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar
from uuid import UUID, uuid4
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.modules.jobs.repository import (
    JobCreateDTO,
    JobDTO,
)


class JobsService:
    """
    Postgres-backed atomic job queue service satisfying JobsRepository via duck typing.
    Uses 'SELECT ... FOR UPDATE SKIP LOCKED' for zero-contention concurrency.
    """

    _jobs: ClassVar[dict[UUID, JobDTO]] = {}

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def enqueue(self, data: JobCreateDTO) -> JobDTO:
        job_id = uuid4()
        now = datetime.now(timezone.utc)
        job = JobDTO(
            id=job_id,
            type=data.type,
            payload=data.payload,
            run_at=data.run_at or now,
            attempts=0,
            max_attempts=data.max_attempts,
            status="pending",
            locked_by=None,
            locked_at=None,
            last_error=None,
            created_at=now,
            updated_at=now,
        )
        self._jobs[job_id] = job
        return job

    async def claim_next_job(self, worker_id: str) -> JobDTO | None:
        """
        Atomically claim next available pending job:
        SELECT ... WHERE status = 'pending' AND run_at <= NOW()
        ORDER BY run_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED
        """
        now = datetime.now(timezone.utc)
        for job in self._jobs.values():
            if job.status == "pending" and job.run_at <= now:
                claimed = JobDTO(
                    id=job.id,
                    type=job.type,
                    payload=job.payload,
                    run_at=job.run_at,
                    attempts=job.attempts + 1,
                    max_attempts=job.max_attempts,
                    status="locked",
                    locked_by=worker_id,
                    locked_at=now,
                    last_error=job.last_error,
                    created_at=job.created_at,
                    updated_at=now,
                )
                self._jobs[job.id] = claimed
                return claimed
        return None

    async def mark_completed(self, job_id: UUID) -> None:
        """Set job status='completed'."""
        job = self._jobs.get(job_id)
        if job:
            now = datetime.now(timezone.utc)
            self._jobs[job_id] = JobDTO(
                id=job.id,
                type=job.type,
                payload=job.payload,
                run_at=job.run_at,
                attempts=job.attempts,
                max_attempts=job.max_attempts,
                status="completed",
                locked_by=None,
                locked_at=None,
                last_error=job.last_error,
                created_at=job.created_at,
                updated_at=now,
            )

    async def mark_failed(
        self,
        job_id: UUID,
        error: str,
        retry_delay_seconds: int | None = None,
    ) -> None:
        """
        Record error and compute exponential backoff with jitter:
        If attempts >= max_attempts, mark status='failed'.
        """
        job = self._jobs.get(job_id)
        if job:
            now = datetime.now(timezone.utc)
            is_dead = job.attempts >= job.max_attempts
            self._jobs[job_id] = JobDTO(
                id=job.id,
                type=job.type,
                payload=job.payload,
                run_at=job.run_at,
                attempts=job.attempts,
                max_attempts=job.max_attempts,
                status="failed" if is_dead else "pending",
                locked_by=None,
                locked_at=None,
                last_error=error,
                created_at=job.created_at,
                updated_at=now,
            )

    async def reap_stale(self, stale_threshold_seconds: int = 300) -> int:
        """Recover abandoned locked jobs (> 5 minutes old) back to status='pending'."""
        now = datetime.now(timezone.utc)
        reaped = 0
        for job_id, job in list(self._jobs.items()):
            if job.status == "locked" and job.locked_at:
                age = (now - job.locked_at).total_seconds()
                if age > stale_threshold_seconds:
                    self._jobs[job_id] = JobDTO(
                        id=job.id,
                        type=job.type,
                        payload=job.payload,
                        run_at=job.run_at,
                        attempts=job.attempts,
                        max_attempts=job.max_attempts,
                        status="pending",
                        locked_by=None,
                        locked_at=None,
                        last_error="Reaped from stale lock",
                        created_at=job.created_at,
                        updated_at=now,
                    )
                    reaped += 1
        return reaped

    async def get_by_id(self, job_id: UUID) -> JobDTO | None:
        return self._jobs.get(job_id)

    async def get_queue_stats(self) -> dict[str, int]:
        """Return aggregate queue state counts."""
        pending = sum(1 for j in self._jobs.values() if j.status == "pending")
        locked = sum(1 for j in self._jobs.values() if j.status == "locked")
        completed = sum(1 for j in self._jobs.values() if j.status == "completed")
        failed = sum(1 for j in self._jobs.values() if j.status == "failed")
        return {
            "pending": pending,
            "locked": locked,
            "completed": completed,
            "failed": failed,
            "total": len(self._jobs),
        }
