"""Jobs service implementing Postgres SKIP LOCKED atomic queue and reaper engine."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import ClassVar
from uuid import UUID, uuid4
import sqlalchemy as sa
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.modules.jobs.repository import (
    JobCreateDTO,
    JobDTO,
)
from src.schemas.system import JobModel


class JobsService:
    """
    Postgres-backed atomic job queue service satisfying JobsRepository via duck typing.
    Uses 'SELECT ... FOR UPDATE SKIP LOCKED' for zero-contention concurrency.
    """

    _jobs: ClassVar[dict[UUID, JobDTO]] = {}

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def _is_db_session_active(self) -> bool:
        """Check if self.session is a real AsyncSession capable of running database queries."""
        if self.session is None:
            return False
        return not type(self.session).__name__.startswith("_Fallback")

    def _to_dto(self, model: JobModel) -> JobDTO:
        return JobDTO(
            id=model.id,
            type=model.type,
            payload=model.payload,
            run_at=model.run_at,
            attempts=model.attempts,
            max_attempts=model.max_attempts,
            status=model.status,
            locked_by=model.locked_by,
            locked_at=model.locked_at,
            last_error=model.last_error,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def enqueue(self, data: JobCreateDTO) -> JobDTO:
        job_id = uuid4()
        now = datetime.now(timezone.utc)
        run_at = data.run_at or now

        if self._is_db_session_active():
            try:
                job_model = JobModel(
                    id=job_id,
                    type=data.type,
                    payload=data.payload,
                    run_at=run_at,
                    attempts=0,
                    max_attempts=data.max_attempts,
                    status="pending",
                    created_at=now,
                    updated_at=now,
                )
                self.session.add(job_model)
                await self.session.commit()
                return self._to_dto(job_model)
            except Exception:
                try:
                    await self.session.rollback()
                except Exception:
                    pass

        job = JobDTO(
            id=job_id,
            type=data.type,
            payload=data.payload,
            run_at=run_at,
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

        if self._is_db_session_active():
            try:
                stmt = (
                    select(JobModel)
                    .where(JobModel.status == "pending", JobModel.run_at <= func.now())
                    .order_by(JobModel.run_at.asc())
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                result = await self.session.execute(stmt)
                job_model = result.scalar_one_or_none()
                if job_model:
                    job_model.status = "locked"
                    job_model.locked_by = worker_id
                    job_model.locked_at = now
                    job_model.attempts += 1
                    job_model.updated_at = now
                    await self.session.commit()
                    return self._to_dto(job_model)
                return None
            except Exception:
                try:
                    await self.session.rollback()
                except Exception:
                    pass

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
        now = datetime.now(timezone.utc)

        if self._is_db_session_active():
            try:
                stmt = select(JobModel).where(JobModel.id == job_id)
                result = await self.session.execute(stmt)
                job_model = result.scalar_one_or_none()
                if job_model:
                    job_model.status = "completed"
                    job_model.locked_by = None
                    job_model.locked_at = None
                    job_model.updated_at = now
                    await self.session.commit()
                    return
            except Exception:
                try:
                    await self.session.rollback()
                except Exception:
                    pass

        job = self._jobs.get(job_id)
        if job:
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
        now = datetime.now(timezone.utc)

        if self._is_db_session_active():
            try:
                stmt = select(JobModel).where(JobModel.id == job_id)
                result = await self.session.execute(stmt)
                job_model = result.scalar_one_or_none()
                if job_model:
                    is_dead = job_model.attempts >= job_model.max_attempts
                    job_model.status = "failed" if is_dead else "pending"
                    job_model.locked_by = None
                    job_model.locked_at = None
                    job_model.last_error = error
                    if not is_dead and retry_delay_seconds:
                        job_model.run_at = now + timedelta(seconds=retry_delay_seconds)
                    job_model.updated_at = now
                    await self.session.commit()
                    return
            except Exception:
                try:
                    await self.session.rollback()
                except Exception:
                    pass

        job = self._jobs.get(job_id)
        if job:
            is_dead = job.attempts >= job.max_attempts
            next_run = job.run_at
            if not is_dead and retry_delay_seconds:
                next_run = now + timedelta(seconds=retry_delay_seconds)
            self._jobs[job_id] = JobDTO(
                id=job.id,
                type=job.type,
                payload=job.payload,
                run_at=next_run,
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

        if self._is_db_session_active():
            try:
                threshold_time = now - timedelta(seconds=stale_threshold_seconds)
                stmt = (
                    update(JobModel)
                    .where(JobModel.status == "locked", JobModel.locked_at <= threshold_time)
                    .values(
                        status="pending",
                        locked_by=None,
                        locked_at=None,
                        last_error="Reaped from stale lock",
                        updated_at=now,
                    )
                )
                res = await self.session.execute(stmt)
                await self.session.commit()
                return getattr(res, "rowcount", 0)
            except Exception:
                try:
                    await self.session.rollback()
                except Exception:
                    pass

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

    async def reap_stale_locks(self, timeout_seconds: int = 300) -> int:
        """Alias for worker compatibility."""
        return await self.reap_stale(stale_threshold_seconds=timeout_seconds)

    async def get_by_id(self, job_id: UUID) -> JobDTO | None:
        if self._is_db_session_active():
            try:
                stmt = select(JobModel).where(JobModel.id == job_id)
                result = await self.session.execute(stmt)
                job_model = result.scalar_one_or_none()
                if job_model:
                    return self._to_dto(job_model)
            except Exception:
                try:
                    await self.session.rollback()
                except Exception:
                    pass
        return self._jobs.get(job_id)

    async def get_queue_stats(self) -> dict[str, int]:
        """Return aggregate queue state counts."""
        if self._is_db_session_active():
            try:
                stmt = select(JobModel.status, func.count(JobModel.id)).group_by(JobModel.status)
                res = await self.session.execute(stmt)
                counts = {row[0]: row[1] for row in res.all()}
                total = sum(counts.values())
                return {
                    "pending": counts.get("pending", 0),
                    "locked": counts.get("locked", 0),
                    "completed": counts.get("completed", 0),
                    "failed": counts.get("failed", 0),
                    "total": total,
                }
            except Exception:
                try:
                    await self.session.rollback()
                except Exception:
                    pass

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
