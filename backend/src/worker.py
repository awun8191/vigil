"""Background worker process executing asynchronous jobs via Postgres SKIP LOCKED queue."""
import asyncio
import os
import signal
import sys
from typing import Any, Awaitable, Callable
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.logging import configure_logging, get_logger
from src.config.settings import Settings, get_settings
from src.dependencies import get_db_session, get_memory_service
from src.modules.jobs.repository import JobDTO
from src.modules.jobs.service import JobsService


async def handle_index_memory(
    payload: dict[str, Any],
    session: AsyncSession,
    settings: Settings,
) -> None:
    """Turn the unindexed turns of one session into memory chunks."""
    memory_service = get_memory_service(session=session, settings=settings)
    await memory_service.run_index_job(payload)


JobHandler = Callable[[dict[str, Any], AsyncSession, Settings], Awaitable[None]]

# Job type to handler. A type that has no handler keeps the poll loop moving, so
# one unknown type cannot block the queue.
JOB_HANDLERS: dict[str, JobHandler] = {
    "index_memory": handle_index_memory,
}


async def dispatch_job(
    job: JobDTO,
    session: AsyncSession,
    settings: Settings,
) -> bool:
    """Run the handler of one claimed job. Return False when no handler exists."""
    handler = JOB_HANDLERS.get(job.type)
    if handler is None:
        return False
    await handler(dict(job.payload or {}), session, settings)
    return True


async def run_worker() -> None:
    """Run background job worker loop."""
    settings = get_settings()
    configure_logging(log_level=settings.LOG_LEVEL, log_format=settings.LOG_FORMAT)
    logger = get_logger("virgil.worker")

    worker_id = f"worker-{os.getpid()}-{uuid4().hex[:6]}"
    logger.info("Starting Virgil background worker", worker_id=worker_id, poll_interval=settings.JOB_POLL_INTERVAL_SECONDS)

    stop_event = asyncio.Event()

    def handle_signal(sig: int, frame: object) -> None:
        logger.info("Shutdown signal received", signal=sig)
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Main worker poll loop
    while not stop_event.is_set():
        try:
            async for session in get_db_session():
                jobs_service = JobsService(session=session, settings=settings)
                # 1. Reap any stale locks older than 5 minutes
                reaped = await jobs_service.reap_stale_locks(timeout_seconds=300)
                if reaped > 0:
                    logger.info("Reaped stale job locks", count=reaped)

                # 2. Claim next job
                job = await jobs_service.claim_next_job(worker_id=worker_id)
                if job:
                    logger.info("Claimed job", job_id=str(job.id), job_type=job.type, attempt=job.attempts)
                    try:
                        handled = await dispatch_job(job, session, settings)
                        if not handled:
                            # No handler is registered for this job type yet.
                            await asyncio.sleep(0.01)
                        await jobs_service.mark_completed(job.id)
                        logger.info("Completed job", job_id=str(job.id), job_type=job.type)
                    except Exception as err:
                        logger.error("Job execution failed", job_id=str(job.id), error=str(err))
                        await jobs_service.mark_failed(job.id, error=str(err), retry_delay_seconds=30)
                break
        except Exception as exc:
            logger.error("Worker loop exception", error=str(exc))

        # Wait before next poll or until stop_event is set
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.JOB_POLL_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass

    logger.info("Virgil background worker stopped", worker_id=worker_id)


def main() -> None:
    """CLI entrypoint for python -m src.worker."""
    try:
        asyncio.run(run_worker())
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)


if __name__ == "__main__":
    main()
