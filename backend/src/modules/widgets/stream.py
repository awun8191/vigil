"""Resumable server-sent-event stream over dashboard widget rows.

The stream is a stateless cursor read. It asks the widget store for rows
whose ``updated_at`` is newer than the caller cursor, writes them, then polls
again after a short pause. A client that reconnects sends ``Last-Event-ID``
and receives every later update exactly once. The stream holds no state
between reads, so a dropped connection loses nothing.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import AsyncIterator
from uuid import UUID

from src.modules.runs.stream import (
    KEEPALIVE_COMMENT,
    KEEPALIVE_INTERVAL_SECONDS,
    POLL_INTERVAL_SECONDS,
    SSE_HEADERS,
)
from src.modules.widgets.repository import WidgetDTO, WidgetsRepository

__all__ = [
    "KEEPALIVE_COMMENT",
    "KEEPALIVE_INTERVAL_SECONDS",
    "POLL_INTERVAL_SECONDS",
    "SSE_HEADERS",
    "format_widget_event",
    "parse_widget_cursor",
    "stream_widget_events",
]


def parse_widget_cursor(raw_value: str | None, fallback: str | None = None) -> str:
    """Read the resume cursor from a ``Last-Event-ID`` header value.

    The cursor is an ISO-8601 timestamp. A missing or unparsable header falls
    back to the ``since`` query parameter, then to the epoch.

    Args:
        raw_value: Raw header value, or None when the header is absent.
        fallback: ``since`` query value to use when the header is unusable.

    Returns:
        An ISO-8601 timestamp string that the next read compares against.
    """
    for candidate in (raw_value, fallback):
        if candidate is None:
            continue
        text = candidate.strip()
        if not text:
            continue
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.isoformat()
    return datetime(1970, 1, 1, tzinfo=timezone.utc).isoformat()


def _cursor_value(widget: WidgetDTO) -> str:
    """Return the stream cursor of one widget row."""
    moment = widget.updated_at
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.isoformat()


def format_widget_event(widget: WidgetDTO) -> str:
    """Encode one widget row as an SSE frame.

    The SSE id is the row ``updated_at``, so the client cursor is the same
    value that the next read compares against.

    Args:
        widget: The widget row to write.

    Returns:
        One SSE frame, terminated by a blank line.
    """
    data = json.dumps(
        {
            "id": str(widget.id),
            "version": widget.version,
            "data_payload": widget.data_payload,
            "updated_at": _cursor_value(widget),
        }
    )
    return f"id: {_cursor_value(widget)}\nevent: widget_updated\ndata: {data}\n\n"


async def stream_widget_events(
    store: WidgetsRepository,
    user_id: UUID,
    cursor: str = "",
    poll_interval: float = POLL_INTERVAL_SECONDS,
    keepalive_interval: float = KEEPALIVE_INTERVAL_SECONDS,
    max_duration_seconds: float | None = None,
) -> AsyncIterator[str]:
    """Yield every widget update after the cursor, newest cursor wins.

    The cursor moves only after a frame is written, so an update is never
    skipped and never sent twice. A read that returns no row writes a
    keepalive comment when the keepalive interval has elapsed.

    Args:
        store: Widget reader, normally the widgets service.
        user_id: Owner whose rows are streamed.
        cursor: Highest ``updated_at`` the client already has.
        poll_interval: Pause between cursor reads, in seconds.
        keepalive_interval: Idle time before a keepalive comment, in seconds.
        max_duration_seconds: Optional limit on the stream lifetime.

    Yields:
        SSE frames as text.
    """
    current = cursor or parse_widget_cursor(None)
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    last_write_at = started_at
    while True:
        if max_duration_seconds is not None and (
            loop.time() - started_at
        ) >= max_duration_seconds:
            return
        widgets = await store.list_widgets(user_id)
        fresh = [w for w in widgets if _cursor_value(w) > current]
        fresh.sort(key=_cursor_value)
        for widget in fresh:
            yield format_widget_event(widget)
            current = _cursor_value(widget)
            last_write_at = loop.time()
        if max_duration_seconds is not None and (
            loop.time() - started_at
        ) >= max_duration_seconds:
            return
        await asyncio.sleep(poll_interval)
        if (loop.time() - last_write_at) >= keepalive_interval:
            yield KEEPALIVE_COMMENT
            last_write_at = loop.time()
