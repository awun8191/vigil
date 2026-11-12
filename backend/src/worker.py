"""Background worker process executing asynchronous jobs from the Postgres queue.

The worker claims one job at a time, runs the registered handler for the job
type, and refreshes the job heartbeat while the handler runs. A handler that
cannot do its work raises. The worker then records a failure. The worker never
reports a success that did not happen.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
import signal
import sys
import time
from typing import Any, Awaitable, Callable, Mapping
from uuid import UUID, uuid4

from src.config.logging import configure_logging, get_logger
from src.config.settings import Settings, get_settings
from src.dependencies import (
    build_runs_service,
    build_tool_executor,
    get_db_session,
    get_llm_client,
    get_memory_service,
)
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

IDLE_SWEEP_INTERVAL_SECONDS = 300
"""The worker runs the idle session index sweep on this fixed interval."""

WIDGET_SWEEP_INTERVAL_SECONDS = 60
"""The worker sweeps due widget refreshes on this fixed interval."""


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


class PermanentJobError(RuntimeError):
    """Raised when a job must fail at once instead of retrying.

    A handler raises this for a poison payload: a missing row, a validation
    error, or an unknown refresh kind. The worker records the job as failed
    without another attempt.
    """


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
    """Index unindexed messages into long-term memory chunks.

    The Phase 5 memory service does the work. The handler puts no embedding on
    the request path, because the queue runs it in this worker.
    """
    memory_service = get_memory_service(
        session=context.session, settings=context.settings
    )
    await memory_service.run_index_job(_payload(job))


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


async def handle_discover_mcp_tools(job: JobDTO, context: JobContext) -> None:
    """Run MCP handshake and tool discovery for a third party MCP server."""
    from src.modules.connectors.service import ConnectorsService

    payload = _payload(job)
    name = str(payload.get("name") or "")
    transport = str(payload.get("transport") or "")
    command = payload.get("command")
    url = payload.get("url")
    service = ConnectorsService(session=context.session, settings=context.settings)  # type: ignore[arg-type]
    if await service.get_mcp_server(name) is not None:
        await service.refresh_mcp_tools(name)
    else:
        await service.register_mcp_server(
            name=name,
            transport=transport,
            command=str(command) if command is not None else None,
            url=str(url) if url is not None else None,
        )


#: Cap of table rows kept from one connector refresh.
WIDGET_TABLE_MAX_ROWS = 50

#: Cap of feed entries kept from one connector refresh.
WIDGET_FEED_MAX_ENTRIES = 30

#: Cap of task items kept from one connector refresh.
WIDGET_TASK_MAX_ITEMS = 50

#: Connector query executor for widget refreshes, or None in this build.
#:
#: The signature is ``(connector_id, query, user_id) -> raw payload dict``.
#: A test installs a fake here. Production has no connector query executor
#: yet, so a connector_query refresh fails permanently with that message
#: instead of retrying a call that cannot succeed.
WIDGET_CONNECTOR_QUERY: (
    Callable[[str, str, UUID], Awaitable[dict[str, Any]]] | None
) = None


def _widget_next_refresh_at(interval_minutes: int | None) -> datetime | None:
    """Compute the next refresh time of one widget row."""
    if interval_minutes is None:
        return None
    return datetime.now(timezone.utc) + timedelta(minutes=interval_minutes)


def _truncate_refresh_payload(
    widget_type: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Cap one refreshed payload at the schema limits."""
    trimmed = dict(payload)
    if widget_type == "data_table" and isinstance(trimmed.get("rows"), list):
        trimmed["rows"] = trimmed["rows"][:WIDGET_TABLE_MAX_ROWS]
    elif widget_type == "status_feed" and isinstance(trimmed.get("entries"), list):
        trimmed["entries"] = trimmed["entries"][:WIDGET_FEED_MAX_ENTRIES]
    elif widget_type == "task_list" and isinstance(trimmed.get("items"), list):
        trimmed["items"] = trimmed["items"][:WIDGET_TASK_MAX_ITEMS]
    return trimmed


async def handle_refresh_widget(job: JobDTO, context: JobContext) -> None:
    """Refresh one dashboard widget row and reschedule its next run.

    The handler only commits the row plus the version bump. There is no event
    bus to publish on: the dashboard sees the change on the next cursor poll
    of ``GET /api/v1/widgets/stream``.
    """
    from pydantic import ValidationError as PydanticValidationError

    from src.modules.widgets.data import validate_widget_payload
    from src.modules.widgets.service import WidgetsService

    payload = _payload(job)
    raw_widget_id = payload.get("widget_id")
    raw_user_id = payload.get("user_id")
    if not raw_widget_id or not raw_user_id:
        raise JobHandlerUnavailableError(
            job.type, "the refresh_widget payload has no widget_id or user_id"
        )
    try:
        widget_id = UUID(str(raw_widget_id))
        user_id = UUID(str(raw_user_id))
    except ValueError as exc:
        raise JobHandlerUnavailableError(
            job.type, f"the refresh_widget payload ids are not UUIDs: {exc}"
        ) from exc

    service = WidgetsService(session=context.session, settings=context.settings)
    row = await service.get_widget(widget_id, user_id)
    if row is None:
        raise PermanentJobError(f"widget {widget_id} not found")
    config = row.refresh_config or {}
    kind = config.get("kind", "static")
    next_at = _widget_next_refresh_at(row.refresh_interval_minutes)

    if kind == "static" or row.refresh_config is None:
        await service.touch_refresh(widget_id, user_id, next_at)
        return
    if kind != "connector_query":
        await service.touch_refresh(widget_id, user_id, next_at)
        raise PermanentJobError(f"unknown widget refresh kind: {kind!r}")
    if WIDGET_CONNECTOR_QUERY is None:
        await service.touch_refresh(widget_id, user_id, next_at)
        raise PermanentJobError("no connector query executor in this build")
    connector_id = str(config.get("connector_id") or "")
    query = str(config.get("query") or "")
    try:
        raw_payload = await WIDGET_CONNECTOR_QUERY(connector_id, query, user_id)
    except PermanentJobError:
        raise
    except Exception as exc:  # noqa: BLE001 - a connector failure retries
        raise RuntimeError(f"connector query failed: {exc}") from exc
    try:
        parsed = validate_widget_payload(
            row.widget_type, _truncate_refresh_payload(row.widget_type, raw_payload)
        )
    except (ValueError, PydanticValidationError) as exc:
        await service.touch_refresh(widget_id, user_id, next_at)
        raise PermanentJobError(f"refreshed payload is invalid: {exc}") from exc
    await service.apply_refresh(
        widget_id, user_id, parsed.model_dump(), _widget_next_refresh_at(
            row.refresh_interval_minutes
        ),
    )


async def _has_pending_widget_refresh(
    jobs_service: Any, widget_id: UUID
) -> bool:
    """Return True when a pending refresh_widget job already covers this row."""
    seen: list[Any] = []
    is_db_active = False
    is_active = getattr(jobs_service, "_is_db_session_active", None)
    if callable(is_active):
        try:
            is_db_active = bool(is_active())
        except Exception:  # noqa: BLE001 - a failed probe means memory store
            is_db_active = False
    if not is_db_active:
        store = getattr(jobs_service, "_jobs", None)
        if isinstance(store, dict):
            seen = list(store.values())
    else:
        session = getattr(jobs_service, "session", None)
        if session is None:
            return False
        try:
            from sqlalchemy import select

            from src.schemas.system import JobModel

            rows = (
                await session.execute(
                    select(JobModel).where(
                        JobModel.type == "refresh_widget",
                        JobModel.status == "pending",
                    )
                )
            ).scalars().all()
            seen = list(rows)
        except Exception:  # noqa: BLE001 - a failed probe must not stop the sweep
            return False
    for job in seen:
        payload = job.payload if isinstance(job.payload, dict) else {}
        if job.type == "refresh_widget" and payload.get("widget_id") == str(widget_id):
            status = getattr(job, "status", None)
            if status == "pending":
                return True
    return False


async def sweep_due_widget_refreshes(
    session: Any,
    settings: Any,
    limit: int = 50,
) -> int:
    """Enqueue one refresh_widget job per due widget row.

    Rows that already have a pending refresh job are skipped. Returns the
    number of jobs enqueued.
    """
    from src.modules.jobs.repository import JobCreateDTO
    from src.modules.jobs.service import JobsService
    from src.modules.widgets.service import WidgetsService

    jobs_service = JobsService(session=session, settings=settings)
    widgets_service = WidgetsService(session=session, settings=settings)
    due = await widgets_service.claim_due_refresh(limit=limit)
    enqueued = 0
    for row in due:
        if await _has_pending_widget_refresh(jobs_service, row.id):
            continue
        await jobs_service.enqueue(
            JobCreateDTO(
                type="refresh_widget",
                payload={"widget_id": str(row.id), "user_id": str(row.user_id)},
                run_at=row.next_refresh_at,
            )
        )
        enqueued += 1
    return enqueued


JOB_HANDLERS: dict[str, JobHandler] = {
    "resume_run": handle_resume_run,
    "execute_run": handle_execute_run,
    "triage_item": handle_triage_item,
    "index_memory": handle_index_memory,
    "poll_connector": handle_poll_connector,
    "cleanup_sandbox": handle_cleanup_sandbox,
    "reindex_memory": handle_reindex_memory,
    "refresh_widget": handle_refresh_widget,
}
"""The dispatch table. It holds one handler for every type in JOB_TYPE_PRIORITIES."""

EXTRA_JOB_HANDLERS: dict[str, JobHandler] = {
    "discover_mcp_tools": handle_discover_mcp_tools,
}

UNWIRED_JOB_TYPES: frozenset[str] = frozenset(
    {"triage_item", "poll_connector", "cleanup_sandbox", "reindex_memory"}
)
"""Job types whose handler has no executor in this worker build.

Each handler here raises JobHandlerUnavailableError, so the queue records a
failure and never a success. Remove a type from this set when its executor
lands. The test suite reads this set, so the two cannot drift apart.
"""


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
        if handlers is None and job_type in EXTRA_JOB_HANDLERS:
            return EXTRA_JOB_HANDLERS[job_type]
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


async def commit_job_outcome(session: Any | None, log: Any) -> None:
    """Commit the outcome of one job on the session that owns its transaction.

    The stores flush instead of committing, so this is the only commit that
    makes the rows a handler wrote, and the outcome the queue recorded, durable.
    A sessionless context (the dispatch unit tests) has no transaction to commit.

    Raises:
        Exception: The commit failed, so the outcome is not durable and the
            caller must not report the job as done.
    """
    if session is None:
        return
    try:
        await session.commit()
    except Exception as exc:  # noqa: BLE001 - the outcome is not durable; report it
        log.error("Cannot commit the job outcome", error=str(exc))
        raise


async def rollback_failed_job(session: Any | None, log: Any) -> None:
    """Discard the uncommitted writes of a job that raised.

    The stores flush and never commit, so a failed job leaves its writes in the
    open transaction. A retry must start from the last durable checkpoint, not
    from a half-applied run. The steps the loop checkpointed are already
    committed and survive this rollback by design.

    Raises nothing: the job already failed, and a rollback error must not mask
    the error that caused it.
    """
    if session is None:
        return
    try:
        await session.rollback()
    except Exception as exc:  # noqa: BLE001 - must not mask the handler error
        log.error("Cannot roll back the failed job transaction", error=str(exc))


async def process_job(
    job: JobDTO,
    jobs_service: JobsService,
    context: JobContext,
    heartbeat: Callable[[], Awaitable[None]] | None = None,
    handlers: Mapping[str, JobHandler] | None = None,
    logger: Any | None = None,
) -> None:
    """
    Dispatch one claimed job, record the outcome, and commit it.

    An unknown job type and a handler error both go to the failure path. Only a
    handler that returns without an error marks the job completed. A failed job
    rolls back its uncommitted writes before the queue records the outcome, and
    each path ends with exactly one commit on the session that owns the job, so
    a job outcome survives the process that ran it.

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
        await rollback_failed_job(context.session, log)
        await jobs_service.mark_failed(job.id, error=str(err), permanent=True)
        await commit_job_outcome(context.session, log)
        return
    except PermanentJobError as err:
        log.error("Job failed permanently", job_id=str(job.id), job_type=job.type)
        # No rollback: writes before a PermanentJobError are deliberate,
        # such as rescheduling the next widget refresh after a poison payload.
        await jobs_service.mark_failed(job.id, error=str(err), permanent=True)
        await commit_job_outcome(context.session, log)
        return
    except Exception as err:
        log.error(
            "Job execution failed",
            job_id=str(job.id),
            job_type=job.type,
            error=str(err),
        )
        await rollback_failed_job(context.session, log)
        await jobs_service.mark_failed(
            job.id, error=str(err), retry_delay_seconds=JOB_RETRY_DELAY_SECONDS
        )
        await commit_job_outcome(context.session, log)
        return

    await jobs_service.mark_completed(job.id)
    await commit_job_outcome(context.session, log)
    log.info("Completed job", job_id=str(job.id), job_type=job.type)


async def run_worker(
    *,
    stop_event: asyncio.Event | None = None,
    install_signal_handlers: bool = True,
) -> None:
    """Run background job worker loop."""
    settings = get_settings()
    if install_signal_handlers:
        configure_logging(log_level=settings.LOG_LEVEL, log_format=settings.LOG_FORMAT)
    logger = get_logger("vigil.worker")

    worker_id = f"worker-{os.getpid()}-{uuid4().hex[:6]}"
    logger.info(
        "Starting Vigil background worker",
        worker_id=worker_id,
        poll_interval=settings.JOB_POLL_INTERVAL_SECONDS,
        heartbeat_interval=HEARTBEAT_INTERVAL_SECONDS,
        reap_interval=REAP_INTERVAL_SECONDS,
    )

    if stop_event is None:
        stop_event = asyncio.Event()

    if install_signal_handlers:
        def handle_signal(sig: int, frame: object) -> None:
            logger.info("Shutdown signal received", signal=sig)
            stop_event.set()

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

    last_reap = 0.0
    last_idle_sweep = 0.0
    last_widget_sweep = 0.0

    # Main worker poll loop
    while not stop_event.is_set():
        now_monotonic = time.monotonic()
        should_reap = reap_is_due(now_monotonic, last_reap)
        if should_reap:
            last_reap = now_monotonic
        should_sweep_idle = reap_is_due(
            now_monotonic, last_idle_sweep, IDLE_SWEEP_INTERVAL_SECONDS
        )
        if should_sweep_idle:
            last_idle_sweep = now_monotonic
        should_sweep_widgets = reap_is_due(
            now_monotonic, last_widget_sweep, WIDGET_SWEEP_INTERVAL_SECONDS
        )
        if should_sweep_widgets:
            last_widget_sweep = now_monotonic

        try:
            async for session in get_db_session(settings):
                jobs_service = JobsService(session=session, settings=settings)
                # The real model client, not the stub. Without this the loop
                # answers from its internal fake and no provider is ever called.
                context = JobContext(
                    settings=settings,
                    session=session,
                    llm=get_llm_client(settings=settings),
                )

                # 1. Reap stale locks on the fixed interval, not on every poll.
                if should_reap:
                    reaped = await jobs_service.reap_stale(
                        stale_threshold_seconds=STALE_LOCK_THRESHOLD_SECONDS
                    )
                    if reaped > 0:
                        logger.info("Reaped stale job locks", count=reaped)

                # 2. Enqueue the index job of every session that went idle.
                if should_sweep_idle:
                    memory_service = get_memory_service(
                        session=session, settings=settings
                    )
                    idle_jobs = await memory_service.enqueue_idle_session_index_jobs()
                    if idle_jobs:
                        logger.info(
                            "Enqueued idle session index jobs", count=len(idle_jobs)
                        )

                # 2b. Enqueue one refresh job per due widget row.
                if should_sweep_widgets:
                    widget_jobs = await sweep_due_widget_refreshes(
                        session=session, settings=settings
                    )
                    if widget_jobs:
                        logger.info(
                            "Enqueued widget refresh jobs", count=widget_jobs
                        )

                # 3. Claim the next job.
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

                # 4. Dispatch the job and record the outcome.
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
        poll_sleep = min(float(settings.JOB_POLL_INTERVAL_SECONDS), 1.0)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_sleep)
        except asyncio.TimeoutError:
            pass

    logger.info("Vigil background worker stopped", worker_id=worker_id)


def main() -> None:
    """CLI entrypoint for python -m src.worker."""
    try:
        asyncio.run(run_worker())
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)


if __name__ == "__main__":
    main()
