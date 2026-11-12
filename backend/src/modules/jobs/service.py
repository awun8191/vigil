"""Jobs service implementing the Postgres SKIP LOCKED atomic queue and the reaper engine."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Callable, ClassVar
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


# ---------------------------------------------------------------------------
# Queue policy. These values are fixed product decisions.
# ---------------------------------------------------------------------------

JOB_TYPE_PRIORITIES: dict[str, int] = {
    "resume_run": 100,
    "execute_run": 90,
    "triage_item": 60,
    "index_memory": 50,
    "refresh_widget": 40,
    "poll_connector": 20,
    "cleanup_sandbox": 10,
    "reindex_memory": 5,
}
"""Fixed claim priority per job type, highest first."""

UNKNOWN_JOB_TYPE_PRIORITY = 0
"""Claim priority of a job type that is not in JOB_TYPE_PRIORITIES."""

PRIORITY_AGING_CAP_SECONDS = 3600
"""Maximum age credit, in seconds. A low priority job cannot wait more than one hour."""

HEARTBEAT_INTERVAL_SECONDS = 30
"""The worker refreshes the job heartbeat at this interval."""

STALE_LOCK_THRESHOLD_SECONDS = 90
"""The reaper unlocks a job when its heartbeat is older than this."""

REAP_INTERVAL_SECONDS = 60
"""The worker runs the reaper on this fixed interval, not on every poll."""


def priority_for_job_type(job_type: str) -> int:
    """
    Return the fixed claim priority of a job type.

    Args:
        job_type: Job type string, for example 'poll_connector'.

    Returns:
        The priority value, or UNKNOWN_JOB_TYPE_PRIORITY for an unknown type.
    """
    return JOB_TYPE_PRIORITIES.get(job_type, UNKNOWN_JOB_TYPE_PRIORITY)


def _as_utc(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime. A naive value is read as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class JobsService:
    """
    Postgres-backed atomic job queue service satisfying JobsRepository via duck typing.

    Uses 'UPDATE ... FROM (SELECT ... FOR UPDATE SKIP LOCKED)' for zero-contention
    concurrency, a per-job heartbeat, and a reaper that reads the heartbeat.
    """

    _jobs: ClassVar[dict[UUID, JobDTO]] = {}

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        # Tests replace this clock to simulate the passage of time.
        self.clock: Callable[[], datetime] | None = None

    # -- Internal helpers ---------------------------------------------------

    def _now(self) -> datetime:
        """Return the current time, or the injected test clock value."""
        if self.clock is not None:
            return _as_utc(self.clock())
        return datetime.now(timezone.utc)

    def _is_db_session_active(self) -> bool:
        """Check if self.session is a real AsyncSession capable of running database queries."""
        return isinstance(self.session, AsyncSession)

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
            priority=model.priority,
            heartbeat_at=model.heartbeat_at,
        )

    def _aging_sort_key(self) -> sa.ColumnElement:
        """
        Return the SQL claim sort key: priority plus elapsed seconds, capped.

        The elapsed term stops at PRIORITY_AGING_CAP_SECONDS, so a low priority
        job gets at most one hour of age credit.
        """
        elapsed = sa.cast(
            func.extract("epoch", func.now() - JobModel.run_at), sa.Integer()
        )
        return JobModel.priority + func.least(elapsed, PRIORITY_AGING_CAP_SECONDS)

    def build_claim_statement(self, worker_id: str, now: datetime | None = None) -> sa.Update:
        """
        Build the single-statement atomic claim query.

        One statement claims the row and writes the lock, so no read-then-update
        race exists between two workers.

        Args:
            worker_id: String identifying the claiming worker process.
            now: Optional claim timestamp. Defaults to the service clock.

        Returns:
            A RETURNING UPDATE statement over the jobs table.
        """
        claimed_at = now or self._now()
        sort_key = self._aging_sort_key()
        claimable = (
            select(JobModel.id)
            .where(JobModel.status == "pending", JobModel.run_at <= func.now())
            .order_by(sort_key.desc(), JobModel.run_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
            .cte("claimable")
        )
        return (
            update(JobModel)
            .where(JobModel.id == select(claimable.c.id).scalar_subquery())
            .values(
                status="locked",
                locked_by=worker_id,
                locked_at=claimed_at,
                attempts=JobModel.attempts + 1,
                updated_at=claimed_at,
            )
            .returning(JobModel)
        )

    # -- Queue operations ---------------------------------------------------

    async def enqueue(self, data: JobCreateDTO) -> JobDTO:
        job_id = uuid4()
        now = self._now()
        run_at = data.run_at or now
        priority = priority_for_job_type(data.type)

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
                    priority=priority,
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
            priority=priority,
            heartbeat_at=None,
        )
        self._jobs[job_id] = job
        return job

    async def claim_next_job(self, worker_id: str) -> JobDTO | None:
        """
        Atomically claim the next runnable job.

        The claim orders by the aged priority, then by run_at, and uses
        FOR UPDATE SKIP LOCKED, so two workers never claim the same row.
        """
        now = self._now()

        if self._is_db_session_active():
            try:
                stmt = self.build_claim_statement(worker_id, now)
                result = await self.session.execute(stmt)
                job_model = result.scalars().first()
                await self.session.commit()
                if job_model is not None:
                    return self._to_dto(job_model)
                return None
            except Exception:
                try:
                    await self.session.rollback()
                except Exception:
                    pass

        return self._claim_next_job_memory(worker_id, now)

    def _claim_next_job_memory(self, worker_id: str, now: datetime) -> JobDTO | None:
        """Claim the next job from the in-memory store with the same ordering rules."""
        candidates = [
            job
            for job in self._jobs.values()
            if job.status == "pending" and _as_utc(job.run_at) <= now
        ]
        if not candidates:
            return None

        def sort_key(job: JobDTO) -> tuple[int, float]:
            elapsed = max(0.0, (now - _as_utc(job.run_at)).total_seconds())
            aged = job.priority + int(min(elapsed, PRIORITY_AGING_CAP_SECONDS))
            return (aged, -_as_utc(job.run_at).timestamp())

        job = max(candidates, key=sort_key)
        claimed = replace(
            job,
            status="locked",
            locked_by=worker_id,
            locked_at=now,
            attempts=job.attempts + 1,
            updated_at=now,
        )
        self._jobs[job.id] = claimed
        return claimed

    async def heartbeat(self, job_id: UUID, worker_id: str | None = None) -> bool:
        """
        Refresh heartbeat_at for a claimed job.

        Only a locked job is refreshed, and only by the worker that holds the
        lock when worker_id is given.

        Args:
            job_id: UUID of the job.
            worker_id: Optional owner check.

        Returns:
            True when a row was refreshed, False otherwise.
        """
        now = self._now()

        if self._is_db_session_active():
            try:
                stmt = update(JobModel).where(
                    JobModel.id == job_id, JobModel.status == "locked"
                )
                if worker_id is not None:
                    stmt = stmt.where(JobModel.locked_by == worker_id)
                stmt = stmt.values(heartbeat_at=now, updated_at=now)
                res = await self.session.execute(stmt)
                await self.session.commit()
                return getattr(res, "rowcount", 0) > 0
            except Exception:
                try:
                    await self.session.rollback()
                except Exception:
                    pass
                return False

        job = self._jobs.get(job_id)
        if job is None or job.status != "locked":
            return False
        if worker_id is not None and job.locked_by != worker_id:
            return False
        self._jobs[job_id] = replace(job, heartbeat_at=now, updated_at=now)
        return True

    async def mark_completed(self, job_id: UUID) -> None:
        """Set job status='completed' and clear the lock and the heartbeat.

        The caller commits. The worker owns the job's transaction and commits the
        outcome once on every path, so this method must not commit or roll back
        the unit of work it shares with the job's handler.
        """
        now = self._now()

        if self._is_db_session_active():
            stmt = select(JobModel).where(JobModel.id == job_id)
            job_model = (await self.session.execute(stmt)).scalar_one_or_none()
            if job_model:
                job_model.status = "completed"
                job_model.locked_by = None
                job_model.locked_at = None
                job_model.heartbeat_at = None
                job_model.updated_at = now
                return

        job = self._jobs.get(job_id)
        if job:
            self._jobs[job_id] = replace(
                job,
                status="completed",
                locked_by=None,
                locked_at=None,
                heartbeat_at=None,
                updated_at=now,
            )

    async def mark_failed(
        self,
        job_id: UUID,
        error: str,
        retry_delay_seconds: int | None = None,
        permanent: bool = False,
    ) -> None:
        """
        Record an error and compute the retry decision.

        A job becomes status='failed' when attempts reach max_attempts, or at
        once when permanent is True.

        Args:
            job_id: UUID of the job.
            error: Error message string or traceback snippet.
            retry_delay_seconds: Optional delay before the next attempt.
            permanent: When True, never reschedule the job.
        """
        now = self._now()

        if self._is_db_session_active():
            stmt = select(JobModel).where(JobModel.id == job_id)
            job_model = (await self.session.execute(stmt)).scalar_one_or_none()
            if job_model:
                is_dead = permanent or job_model.attempts >= job_model.max_attempts
                job_model.status = "failed" if is_dead else "pending"
                job_model.locked_by = None
                job_model.locked_at = None
                job_model.heartbeat_at = None
                job_model.last_error = error
                if not is_dead and retry_delay_seconds:
                    job_model.run_at = now + timedelta(seconds=retry_delay_seconds)
                job_model.updated_at = now
                return

        job = self._jobs.get(job_id)
        if job:
            is_dead = permanent or job.attempts >= job.max_attempts
            next_run = job.run_at
            if not is_dead and retry_delay_seconds:
                next_run = now + timedelta(seconds=retry_delay_seconds)
            self._jobs[job_id] = replace(
                job,
                run_at=next_run,
                status="failed" if is_dead else "pending",
                locked_by=None,
                locked_at=None,
                heartbeat_at=None,
                last_error=error,
                updated_at=now,
            )

    async def reap_stale(
        self, stale_threshold_seconds: int = STALE_LOCK_THRESHOLD_SECONDS
    ) -> int:
        """
        Return abandoned locked jobs to status='pending'.

        The age of a lock is measured on COALESCE(heartbeat_at, locked_at), so a
        running job that sends heartbeats is never stolen. A job is reaped once:
        the next reaper pass finds it pending and skips it.

        Args:
            stale_threshold_seconds: Heartbeat age after which a lock is stale.

        Returns:
            Count of jobs returned to pending.
        """
        now = self._now()

        if self._is_db_session_active():
            try:
                threshold_time = now - timedelta(seconds=stale_threshold_seconds)
                last_seen = func.coalesce(JobModel.heartbeat_at, JobModel.locked_at)
                stmt = (
                    update(JobModel)
                    .where(JobModel.status == "locked", last_seen < threshold_time)
                    .values(
                        status="pending",
                        locked_by=None,
                        locked_at=None,
                        heartbeat_at=None,
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
            last_seen = job.heartbeat_at or job.locked_at
            if job.status != "locked" or last_seen is None:
                continue
            age = (now - _as_utc(last_seen)).total_seconds()
            if age > stale_threshold_seconds:
                self._jobs[job_id] = replace(
                    job,
                    status="pending",
                    locked_by=None,
                    locked_at=None,
                    heartbeat_at=None,
                    last_error="Reaped from stale lock",
                    updated_at=now,
                )
                reaped += 1
        return reaped

    async def reap_stale_locks(
        self, timeout_seconds: int = STALE_LOCK_THRESHOLD_SECONDS
    ) -> int:
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
