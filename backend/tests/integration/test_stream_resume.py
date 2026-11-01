"""Integration tests for the resumable SSE stream over run steps.

The stream is a cursor read. A reconnect sends `Last-Event-ID` and receives
every later step exactly once, with no gap and no duplicate.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
import pytest

from src.config.settings import Settings
from src.dependencies import get_runs_service
from src.main import create_app
from src.modules.runs.repository import (
    RunCreateDTO,
    RunDTO,
    RunStepCreateDTO,
    RunStepDTO,
)
from src.modules.runs.service import RunsService
from src.modules.runs.stream import (
    KEEPALIVE_COMMENT,
    parse_last_event_id,
    stream_run_events,
)


AUTH_HEADERS = {"Authorization": "Bearer vgt_testtoken"}
"""Bearer token that the test environment accepts."""


def _fresh_runs_service() -> RunsService:
    """Return an empty in-memory runs service.

    A service built without a session owns a new journal, so every call gives
    an empty store. The old class level dictionaries are gone.
    """
    return RunsService(session=None, settings=Settings(APP_ENV="test"))  # type: ignore[arg-type]


@pytest.fixture
def runs_service() -> RunsService:
    """Empty in-memory runs service for one test."""
    return _fresh_runs_service()


async def _seed_run(
    runs_service: RunsService,
    step_count: int,
    status: str = "running",
) -> UUID:
    """Create one run with steps numbered 1..step_count.

    The run starts in ``running`` because the state machine has no edge from
    ``awaiting_approval`` to ``completed``. A paused run resumes through
    ``running`` first.
    """
    run = await runs_service.create_run(
        RunCreateDTO(config_id=uuid4(), status=status)
    )
    for number in range(1, step_count + 1):
        await runs_service.save_run_step(
            RunStepCreateDTO(
                run_id=run.id,
                step_number=number,
                event_type="thought",
                payload={"step": number},
            )
        )
    return run.id


async def _append_step(runs_service: RunsService, run_id: UUID, number: int) -> None:
    """Append one more step to a run."""
    await runs_service.save_run_step(
        RunStepCreateDTO(
            run_id=run_id,
            step_number=number,
            event_type="tool_result",
            payload={"step": number},
        )
    )


def _event_ids(body: str) -> list[int]:
    """Read the SSE ids of a stream body in order."""
    return [
        int(line.split(":", 1)[1].strip())
        for line in body.splitlines()
        if line.startswith("id: ")
    ]


def _sse_client(runs_service: RunsService) -> httpx.AsyncClient:
    """Build an ASGI test client whose runs dependency returns the service."""
    app = create_app(Settings(DEBUG=True, APP_ENV="test"))
    app.dependency_overrides[get_runs_service] = lambda: runs_service
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.mark.asyncio
async def test_stream_no_gap_no_duplicate(runs_service: RunsService) -> None:
    """
    Reconnect with Last-Event-ID: 3 and receive steps 4 and 5 only.

    Steps 4 and 5 arrive after the reconnect, so the test also proves that the
    poll picks up new rows without a second connection.
    """
    run_id = await _seed_run(runs_service, step_count=3)

    async with _sse_client(runs_service) as client:
        stream_task = asyncio.create_task(
            client.get(
                f"/api/v1/runs/{run_id}/events",
                headers={**AUTH_HEADERS, "Last-Event-ID": "3"},
            )
        )
        # Let the stream open and run one poll before the new steps exist.
        await asyncio.sleep(0.6)
        await _append_step(runs_service, run_id, 4)
        await _append_step(runs_service, run_id, 5)
        await runs_service.update_run_status(run_id, status="completed")
        response = await asyncio.wait_for(stream_task, timeout=15)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"

    ids = _event_ids(response.text)
    assert ids == [4, 5]
    assert len(ids) == len(set(ids))
    assert all(later - earlier == 1 for earlier, later in zip(ids, ids[1:]))
    assert "id: 3" not in response.text
    assert "event: tool_result" in response.text


@pytest.mark.asyncio
async def test_stream_replay_from_zero_includes_every_step(runs_service: RunsService) -> None:
    """A client with no header receives every step from the first one."""
    run_id = await _seed_run(runs_service, step_count=3)
    await runs_service.update_run_status(run_id, status="completed")

    async with _sse_client(runs_service) as client:
        response = await client.get(
            f"/api/v1/runs/{run_id}/events", headers=AUTH_HEADERS
        )

    assert response.status_code == 200
    assert _event_ids(response.text) == [1, 2, 3]


@pytest.mark.asyncio
async def test_stream_since_step_query_is_used_without_header(runs_service: RunsService) -> None:
    """The since_step query parameter opens the stream at the same cursor."""
    run_id = await _seed_run(runs_service, step_count=5)
    await runs_service.update_run_status(run_id, status="completed")

    async with _sse_client(runs_service) as client:
        response = await client.get(
            f"/api/v1/runs/{run_id}/events?since_step=3", headers=AUTH_HEADERS
        )

    assert response.status_code == 200
    assert _event_ids(response.text) == [4, 5]


@pytest.mark.asyncio
async def test_stream_header_overrides_query(runs_service: RunsService) -> None:
    """The Last-Event-ID header wins over an older query cursor."""
    run_id = await _seed_run(runs_service, step_count=5)
    await runs_service.update_run_status(run_id, status="completed")

    async with _sse_client(runs_service) as client:
        response = await client.get(
            f"/api/v1/runs/{run_id}/events?since_step=1",
            headers={**AUTH_HEADERS, "Last-Event-ID": "4"},
        )

    assert response.status_code == 200
    assert _event_ids(response.text) == [5]


@pytest.mark.asyncio
async def test_stream_unknown_run_returns_404(runs_service: RunsService) -> None:
    """A stream request for an absent run returns the standard 404 envelope."""
    async with _sse_client(runs_service) as client:
        response = await client.get(
            f"/api/v1/runs/{uuid4()}/events", headers=AUTH_HEADERS
        )

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_stream_writes_keepalive_comment() -> None:
    """An idle stream writes a keepalive comment before the next poll."""
    service = _fresh_runs_service()
    run_id = await _seed_run(service, step_count=0, status="running")

    frames = []
    async for frame in stream_run_events(
        service,
        run_id,
        last_event_id=0,
        poll_interval=0.01,
        keepalive_interval=0.01,
        max_duration_seconds=0.05,
    ):
        frames.append(frame)

    assert KEEPALIVE_COMMENT in frames
    assert not any(frame.startswith("id: ") for frame in frames)


@pytest.mark.asyncio
async def test_stream_skips_rows_at_or_below_the_cursor() -> None:
    """A store that repeats old rows never produces a duplicate frame."""
    step_run = uuid4()
    now = datetime.now(timezone.utc)

    def _step(number: int) -> RunStepDTO:
        return RunStepDTO(
            id=uuid4(),
            run_id=step_run,
            step_number=number,
            event_type="thought",
            payload={"step": number},
            created_at=now,
        )

    class _RepeatingStore:
        """Store that returns the acknowledged row again on every read."""

        def __init__(self) -> None:
            self.calls = 0

        async def get_steps_since(self, run_id: UUID, step_number: int) -> list[RunStepDTO]:
            self.calls += 1
            if self.calls == 1:
                return [_step(3), _step(4)]
            return [_step(4), _step(5)]

        async def get_run_by_id(self, run_id: UUID) -> RunDTO | None:
            return RunDTO(
                id=step_run,
                config_id=uuid4(),
                card_id=None,
                parent_run_id=None,
                depth=0,
                status="completed",
                started_at=now,
                ended_at=now,
                token_usage={},
                cost_usd=0.0,
            )

    frames = [
        frame
        async for frame in stream_run_events(
            _RepeatingStore(),  # type: ignore[arg-type]
            step_run,
            last_event_id=3,
            poll_interval=0.01,
        )
    ]

    ids = _event_ids("".join(frames))
    assert ids == [4, 5]


def test_parse_last_event_id() -> None:
    """The cursor parser accepts a number and falls back on bad input."""
    assert parse_last_event_id("7") == 7
    assert parse_last_event_id(" 7 ") == 7
    assert parse_last_event_id(None, fallback=3) == 3
    assert parse_last_event_id("", fallback=3) == 3
    assert parse_last_event_id("abc", fallback=3) == 3
    assert parse_last_event_id("-2", fallback=3) == 3
    assert parse_last_event_id("0") == 0
    assert parse_last_event_id(None, fallback=-5) == 0
