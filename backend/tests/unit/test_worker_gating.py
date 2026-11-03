"""Prove the API lifespan starts a job worker only when EMBED_WORKER is true.

Default off keeps the deployment at one poller: the compose worker service owns
the Docker socket, the API does not, so an embedded poller would claim runs it
cannot execute. These tests use the real lifespan and a recorder in place of
the worker loop. No database, no Docker, no provider call.
"""
import asyncio

import pytest

import src.main as main_module
from src.config.settings import Settings


@pytest.fixture
def worker_recorder(monkeypatch: pytest.MonkeyPatch) -> tuple[list[asyncio.Event], asyncio.Event]:
    """Replace the worker loop with a recorder that parks until cancelled."""
    calls: list[asyncio.Event] = []
    started = asyncio.Event()

    async def fake_run_worker(
        *,
        stop_event: asyncio.Event | None = None,
        install_signal_handlers: bool = True,
    ) -> None:
        assert stop_event is not None
        calls.append(stop_event)
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("src.worker.run_worker", fake_run_worker)
    return calls, started


def _app_for(monkeypatch: pytest.MonkeyPatch, settings: Settings):
    """Build the real app with get_settings returning the given settings."""
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    return main_module.create_app()


def test_embed_worker_defaults_to_false() -> None:
    """A process that sets nothing must not poll the queue."""
    assert Settings().EMBED_WORKER is False


@pytest.mark.parametrize("raw,expected", [("false", False), ("true", True)])
def test_embed_worker_parses_the_value_compose_passes(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    """The compose pin is a string, so prove the parser turns it into a bool."""
    monkeypatch.setenv("EMBED_WORKER", raw)
    assert Settings().EMBED_WORKER is expected


async def test_lifespan_does_not_start_worker_by_default(
    monkeypatch: pytest.MonkeyPatch,
    worker_recorder: tuple[list[asyncio.Event], asyncio.Event],
) -> None:
    """development + EMBED_WORKER=False: the lifespan leaves the queue alone."""
    calls, _ = worker_recorder
    app = _app_for(monkeypatch, Settings(APP_ENV="development", EMBED_WORKER=False))

    async with app.router.lifespan_context(app):
        # Give any wrongly created task a chance to run before we assert.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert calls == []


async def test_lifespan_starts_worker_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
    worker_recorder: tuple[list[asyncio.Event], asyncio.Event],
) -> None:
    """development + EMBED_WORKER=True: one task starts and its stop event sets on exit."""
    calls, started = worker_recorder
    app = _app_for(monkeypatch, Settings(APP_ENV="development", EMBED_WORKER=True))

    async with app.router.lifespan_context(app):
        await asyncio.wait_for(started.wait(), timeout=2)

    assert len(calls) == 1
    assert calls[0].is_set()
