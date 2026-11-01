"""Background worker process executing asynchronous jobs from the Postgres queue.

The worker claims one job at a time, runs the registered handler for the job
type, and refreshes the job heartbeat while the handler runs. A handler that
cannot do its work raises. The worker then records a failure. The worker never
reports a success that did not happen.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
import signal
import sys
import time
from typing import Any, Awaitable, Callable, Mapping
from uuid import UUID, uuid4

from src.config.logging import configure_logging, get_logger
from src.config.settings import Settings, get_settings
from src.dependencies import build_runs_service, build_tool_executor, get_db_session
from src.modules.jobs.repository import JobCreateDTO, JobDTO
from src.modules.jobs.service import (
    HEARTBEAT_INTERVAL_SECONDS,
    REAP_INTERVAL_SECONDS,
    STALE_LOCK_THRESHOLD_SECONDS,
    JobsService,
)
from src.modules.runs.state import SqlRunJournal


JOB_RETRY_DELAY_SECONDS = 30
"""Delay before the queue retries a job that raised a transient error."""


class UnknownJobTypeError(RuntimeError):
    """Raised when the queue holds a job type with no registered handler."""

    def __init__(self, job_type: str) -> None:
        self.job_type = job_type
        super().__init__(f"Unknown job type: {job_type!r}")


class JobHandlerUnavailableError(RuntimeError):
    """Raised when a job handler has no executor in this worker build."""

    def __init__(self, job_type: str, reason: str) -> None:
        self.job_type = job_type
        super().__init__(f"Handler for job type {job_type!r} cannot run: {reason}")


@dataclass(frozen=True)
class JobContext:
    """Dependencies that a job handler receives.

    ``llm``, ``sandbox`` and ``mcp_host`` are optional. The worker leaves them
    empty and takes the real adapters from the composition root. A test passes
    a fake model or a fake connector here.
    """

    settings: Settings
    session: Any | None = None
    llm: Any | None = None
    sandbox: Any | None = None
    mcp_host: Any | None = None
    observer: Any | None = None


JobHandler = Callable[[JobDTO, JobContext], Awaitable[None]]


# ---------------------------------------------------------------------------
# Job handlers, one per known type.
#
# A handler runs the real work of its job type or it raises. The Phase 4 run
# handlers drive the stateless loop. A subsystem that is not built yet has no
# executor, so its handler raises JobHandlerUnavailableError and the failure
# path records the job as failed. Phase 5 replaces the remaining entries when
# its subsystems land.
# ---------------------------------------------------------------------------

def _payload(job: JobDTO) -> dict[str, Any]:
    """Return the job payload as a dictionary."""
    payload = job.payload
    return dict(payload) if isinstance(payload, Mapping) else {}


def _payload_run_id(job: JobDTO) -> UUID:
    """Return the run id of a job payload.

    Raises:
        JobHandlerUnavailableError: The payload has no run id.
    """
    raw = _payload(job).get("run_id")
    if not raw:
        raise JobHandlerUnavailableError(job.type, "the job payload has no run_id")
    return UUID(str(raw))


async def _drive_run(job: JobDTO, context: JobContext, run_id: UUID) -> None:
    """Drive one run through the loop with the real tools of that run."""
    executor = build_tool_executor(
        context.session,
        context.settings,
        run_id,
        sandbox=context.sandbox,
        mcp_host=context.mcp_host,
    )
    service = build_runs_service(
        context.session,
        context.settings,
        run_id=run_id,
        llm=context.llm,
        sandbox=context.sandbox,
        mcp_host=context.mcp_host,
        tools=executor,
    )
    try:
        await service.process(run_id, observer=context.observer)
    finally:
        await executor.close()


async def _run_background_tool(
    job: JobDTO, context: JobContext, run_id: UUID
) -> None:
    """Run one background tool call, store its result, and wake the run."""
    payload = _payload(job)
    tool_name = str(payload.get("tool_name") or "")
    tool_call_id = str(payload.get("tool_call_id") or "")
    arguments = payload.get("arguments")
    if not tool_name or not tool_call_id:
        raise JobHandlerUnavailableError(
            job.type, "the tool_call payload has no tool_name or tool_call_id"
        )
    executor = build_tool_executor(
        context.session,
        context.settings,
        run_id,
        sandbox=context.sandbox,
        mcp_host=context.mcp_host,
    )
    try:
        result = await executor.execute(
            tool_name, arguments if isinstance(arguments, Mapping) else {}
        )
    finally:
        await executor.close()
    await SqlRunJournal(context.session).complete_job(job.id, result)
    # The resume payload holds the run id only. A tool_call_id here would
    # collide with the completed tool job in the loop job index.
    await JobsService(session=context.session, settings=context.settings).enqueue(
        JobCreateDTO(
            type="resume_run",
            payload={"run_id": str(run_id), "kind": "run"},
        )
    )


async def handle_resume_run(job: JobDTO, context: JobContext) -> None:
    """Resume a run that waited for an approval or a background tool."""
    await _drive_run(job, context, _payload_run_id(job))


async def handle_execute_run(job: JobDTO, context: JobContext) -> None:
    """Execute a queued run, or run one background tool call of a run."""
    run_id = _payload_run_id(job)
    if _payload(job).get("kind") == "tool_call":
        await _run_background_tool(job, context, run_id)
        return
    await _drive_run(job, context, run_id)


async def handle_triage_item(job: JobDTO, context: JobContext) -> None:
    """Run the two-stage triage pipeline on one connector item."""
    raise JobHandlerUnavailableError(
        job.type, "the triage pipeline is not wired to the worker"
    )


async def handle_index_memory(job: JobDTO, context: JobContext) -> None:
    """Index unindexed messages into long-term memory chunks."""
    raise JobHandlerUnavailableError(
        job.type, "the memory indexer is not wired to the worker"
    )


async def handle_poll_connector(job: JobDTO, context: JobContext) -> None:
    """Poll one connector account for new items."""
    raise JobHandlerUnavailableError(
        job.type, "the connector poller is not wired to the worker"
    )


async def handle_cleanup_sandbox(job: JobDTO, context: JobContext) -> None:
    """Stop and remove a sandbox container after its run ends."""
    raise JobHandlerUnavailableError(
        job.type, "the sandbox runner is not wired to the worker"
    )


async def handle_reindex_memory(job: JobDTO, context: JobContext) -> None:
    """Re-embed memory chunks after an embedding model change."""
    raise JobHandlerUnavailableError(
        job.type, "the memory indexer is not wired to the worker"
    )


JOB_HANDLERS: dict[str, JobHandler] = {
    "resume_run": handle_resume_run,
    "execute_run": handle_execute_run,
    "triage_item": handle_triage_item,
    "index_memory": handle_index_memory,
    "poll_connector": handle_poll_connector,
    "cleanup_sandbox": handle_cleanup_sandbox,
    "reindex_memory": handle_reindex_memory,
}
"""The dispatch table. It holds one handler for every type in JOB_TYPE_PRIORITIES."""


def resolve_handler(
    job_type: str, handlers: Mapping[str, JobHandler] | None = None
) -> JobHandler:
    """
    Return the handler for a job type.

    Args:
        job_type: Job type string from the claimed job.
        handlers: Optional table override. Defaults to JOB_HANDLERS.

    Returns:
        The registered handler.

    Raises:
        UnknownJobTypeError: The type has no handler.
    """
    table = JOB_HANDLERS if handlers is None else handlers
    try:
        return table[job_type]
    except KeyError:
        raise UnknownJobTypeError(job_type) from None


async def dispatch_job(
    job: JobDTO,
    context: JobContext,
    handlers: Mapping[str, JobHandler] | None = None,
) -> None:
    """
    Run the handler that the job type selects.

    Args:
        job: The claimed job.
        context: Dependencies for the handler.
        handlers: Optional table override.

    Raises:
        UnknownJobTypeError: The job type has no handler.
    """
    handler = resolve_handler(job.type, handlers)
    await handler(job, context)


async def run_with_heartbeat(
    job: JobDTO,
    handler: JobHandler,
    context: JobContext,
    heartbeat: Callable[[], Awaitable[None]],
    interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
) -> None:
    """
    Run one job handler and refresh the job heartbeat while it runs.

    A short handler sends no heartbeat. A handler that runs longer than the
    interval sends one heartbeat per interval, so the reaper never steals the
    job.

    Args:
        job: The claimed job.
        handler: The handler to run.
        context: Dependencies for the handler.
        heartbeat: Coroutine function that refreshes the job heartbeat.
        interval_seconds: Heartbeat interval in seconds.

    Raises:
        Exception: Any error that the handler raises.
    """
    task = asyncio.ensure_future(handler(job, context))
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=interval_seconds)
            if done:
                task.result()
                return
            await heartbeat()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def reap_is_due(
    now_monotonic: float,
    last_reap: float,
    interval_seconds: float = REAP_INTERVAL_SECONDS,
) -> bool:
    """
    Return True when the fixed reaper interval has elapsed.

    The first poll always reaps because last_reap starts at zero.
    """
    return (now_monotonic - last_reap) >= interval_seconds


async def process_job(
    job: JobDTO,
    jobs_service: JobsService,
    context: JobContext,
    heartbeat: Callable[[], Awaitable[None]] | None = None,
    handlers: Mapping[str, JobHandler] | None = None,
    logger: Any | None = None,
) -> None:
    """
    Dispatch one claimed job and record the outcome.

    An unknown job type and a handler error both go to the failure path. Only a
    handler that returns without an error marks the job completed.

    Args:
        job: The claimed job.
        jobs_service: Queue service for the session that holds the lock.
        context: Dependencies for the handler.
        heartbeat: Optional heartbeat coroutine function.
        handlers: Optional table override.
        logger: Optional logger.
    """
    log = logger if logger is not None else get_logger("virgil.worker")

    async def _no_heartbeat() -> None:
        return None

    try:
        await run_with_heartbeat(
            job,
            resolve_handler(job.type, handlers),
            context,
            heartbeat or _no_heartbeat,
        )
    except UnknownJobTypeError as err:
        log.error("Unknown job type", job_id=str(job.id), job_type=job.type)
        await jobs_service.mark_failed(job.id, error=str(err), permanent=True)
        return
    except Exception as err:
        log.error(
            "Job execution failed",
            job_id=str(job.id),
            job_type=job.type,
            error=str(err),
        )
        await jobs_service.mark_failed(
            job.id, error=str(err), retry_delay_seconds=JOB_RETRY_DELAY_SECONDS
        )
        return

    await jobs_service.mark_completed(job.id)
    log.info("Completed job", job_id=str(job.id), job_type=job.type)


async def run_worker() -> None:
    """Run background job worker loop."""
    settings = get_settings()
    configure_logging(log_level=settings.LOG_LEVEL, log_format=settings.LOG_FORMAT)
    logger = get_logger("virgil.worker")

    worker_id = f"worker-{os.getpid()}-{uuid4().hex[:6]}"
    logger.info(
        "Starting Virgil background worker",
        worker_id=worker_id,
        poll_interval=settings.JOB_POLL_INTERVAL_SECONDS,
        heartbeat_interval=HEARTBEAT_INTERVAL_SECONDS,
        reap_interval=REAP_INTERVAL_SECONDS,
    )

    stop_event = asyncio.Event()

    def handle_signal(sig: int, frame: object) -> None:
        logger.info("Shutdown signal received", signal=sig)
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    last_reap = 0.0

    # Main worker poll loop
    while not stop_event.is_set():
        now_monotonic = time.monotonic()
        should_reap = reap_is_due(now_monotonic, last_reap)
        if should_reap:
            last_reap = now_monotonic

        try:
            async for session in get_db_session(settings):
                jobs_service = JobsService(session=session, settings=settings)
                context = JobContext(settings=settings, session=session)

                # 1. Reap stale locks on the fixed interval, not on every poll.
                if should_reap:
                    reaped = await jobs_service.reap_stale(
                        stale_threshold_seconds=STALE_LOCK_THRESHOLD_SECONDS
                    )
                    if reaped > 0:
                        logger.info("Reaped stale job locks", count=reaped)

                # 2. Claim the next job.
                job = await jobs_service.claim_next_job(worker_id=worker_id)
                if job is None:
                    break

                logger.info(
                    "Claimed job",
                    job_id=str(job.id),
                    job_type=job.type,
                    priority=job.priority,
                    attempt=job.attempts,
                )

                async def heartbeat() -> None:
                    """Refresh the heartbeat on its own short session."""
                    async for hb_session in get_db_session(settings):
                        await JobsService(
                            session=hb_session, settings=settings
                        ).heartbeat(job.id, worker_id=worker_id)
                        break

                # 3. Dispatch the job and record the outcome.
                await process_job(
                    job,
                    jobs_service,
                    context,
                    heartbeat=heartbeat,
                    logger=logger,
                )
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
