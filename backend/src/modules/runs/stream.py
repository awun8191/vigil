"""Resumable server-sent-event stream over the recorded run steps.

The stream is a cursor read. It asks the run step store for rows where
`step_number` is greater than the caller cursor, writes them, then polls again
after a short pause. A client that reconnects sends `Last-Event-ID` and receives
every later step exactly once. The stream holds no state between reads, so a
dropped connection loses nothing and a reconnected phone catches up.
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator
from uuid import UUID

from src.modules.runs.repository import RunStepDTO, RunsRepository


POLL_INTERVAL_SECONDS = 0.5
"""Pause between two cursor reads of the step store."""

KEEPALIVE_INTERVAL_SECONDS = 15.0
"""A comment is written when no step arrived for this long."""

KEEPALIVE_COMMENT = ": keepalive\n\n"
"""SSE comment that keeps an idle connection open. Clients ignore it."""

TERMINAL_RUN_STATUSES = frozenset({"completed", "failed"})
"""The stream closes after the run reaches one of these statuses."""

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
"""Headers that stop a proxy from buffering or caching the stream."""


def parse_last_event_id(raw_value: str | None, fallback: int = 0) -> int:
    """
    Read the resume cursor from a `Last-Event-ID` header value.

    A missing header, a non-numeric value, and a negative value all fall back
    to the query parameter cursor.

    Args:
        raw_value: Raw header value, or None when the header is absent.
        fallback: Cursor to use when the header is unusable.

    Returns:
        A non-negative step number.
    """
    if raw_value is None:
        return max(0, fallback)
    text = raw_value.strip()
    if not text:
        return max(0, fallback)
    try:
        value = int(text)
    except ValueError:
        return max(0, fallback)
    if value < 0:
        return max(0, fallback)
    return value


def format_sse_event(step: RunStepDTO) -> str:
    """
    Encode one run step as an SSE frame.

    The SSE id is the step number, so the client cursor is the same value that
    the next read compares against.

    Args:
        step: The run step to write.

    Returns:
        One SSE frame, terminated by a blank line.
    """
    data = json.dumps(
        {
            "run_id": str(step.run_id),
            "step_number": step.step_number,
            "event_type": step.event_type,
            "payload": step.payload,
            "created_at": step.created_at.isoformat(),
        }
    )
    return f"id: {step.step_number}\nevent: {step.event_type}\ndata: {data}\n\n"


async def stream_run_events(
    store: RunsRepository,
    run_id: UUID,
    last_event_id: int = 0,
    poll_interval: float = POLL_INTERVAL_SECONDS,
    keepalive_interval: float = KEEPALIVE_INTERVAL_SECONDS,
    max_duration_seconds: float | None = None,
) -> AsyncIterator[str]:
    """
    Yield every run step after the cursor, then close when the run ends.

    The cursor moves only after a frame is written, so a step is never skipped
    and never sent twice. A read that returns no row writes a keepalive comment
    when the keepalive interval has elapsed.

    Args:
        store: Run step reader, normally the runs repository.
        run_id: UUID of the run to follow.
        last_event_id: Highest step number the client already has.
        poll_interval: Pause between cursor reads, in seconds.
        keepalive_interval: Idle time before a keepalive comment, in seconds.
        max_duration_seconds: Optional limit on the stream lifetime.

    Yields:
        SSE frames as text.
    """
    cursor = max(0, last_event_id)
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    last_write_at = started_at

    while True:
        steps = await store.get_steps_since(run_id, cursor)
        written = 0
        for step in steps:
            if step.step_number <= cursor:
                continue
            cursor = step.step_number
            written += 1
            yield format_sse_event(step)
            last_write_at = loop.time()

        # A finished run closes the stream only after a read that returned no
        # new row, so a last step written just before the status change is
        # still delivered.
        run = await store.get_run_by_id(run_id)
        if written == 0 and run is not None and run.status in TERMINAL_RUN_STATUSES:
            return

        now = loop.time()
        if max_duration_seconds is not None:
            if (now - started_at) >= max_duration_seconds:
                return
        if (now - last_write_at) >= keepalive_interval:
            yield KEEPALIVE_COMMENT
            last_write_at = now

        await asyncio.sleep(poll_interval)
