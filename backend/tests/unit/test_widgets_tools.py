"""Unit tests for the create_widget and update_widget tools.

Identity always comes from the ToolContext, never from tool args. The
context carries a stub session that shares one in-memory widget store per
test, so tool writes use the same memory semantics as the service tests
without touching a database.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.modules.jobs.service import JobsService
from src.modules.runs.tools import ToolContext, get_tool, invoke_tool
from src.modules.widgets.service import WidgetsService


_WIDGETS_MEMORY: dict[UUID, Any] = {}
"""In-memory widget rows shared by every service in one test."""


def _test_session() -> Any:
    """Return a stub session that shares the test widget store."""
    return SimpleNamespace(_widgets_memory=_WIDGETS_MEMORY)


def _service() -> WidgetsService:
    """Return a service over the shared test widget store."""
    return WidgetsService(session=_test_session(), settings=None)  # type: ignore[arg-type]


def _context(user_id, tmp_path: Path) -> ToolContext:
    """Build a tool context owned by one user."""
    return ToolContext(
        runner=SimpleNamespace(),
        container_id="run-1",
        delivery_dir=tmp_path,
        run_id=uuid4(),
        user_id=user_id,
        session=_test_session(),
    )


@pytest.fixture(autouse=True)
def _fresh_stores():
    """Clear the shared in-memory stores around each test."""
    _WIDGETS_MEMORY.clear()
    JobsService._jobs.clear()
    yield
    _WIDGETS_MEMORY.clear()
    JobsService._jobs.clear()


def test_widget_tools_are_local() -> None:
    """All widget tools are local, so the approval policy gates dashboard writes."""
    assert get_tool("create_widget").level == "local"
    assert get_tool("update_widget").level == "local"
    assert get_tool("delete_widget").level == "local"


async def test_create_widget_success(tmp_path: Path) -> None:
    """A valid call creates a row scoped to the context user."""
    owner = uuid4()
    result = await invoke_tool(
        "create_widget",
        {"title": "Panel", "widget_type": "metric_card", "data_payload": {"value": "1"}},
        _context(owner, tmp_path),
    )
    assert result.ok, result.error
    assert result.content["version"] == 1
    assert result.content["widget_type"] == "metric_card"
    service = _service()
    stored = await service.get_widget(UUID(result.content["widget_id"]), owner)
    assert stored is not None


async def test_create_widget_rejects_mismatch(tmp_path: Path) -> None:
    """A table payload for a metric card fails instead of raising."""
    result = await invoke_tool(
        "create_widget",
        {
            "title": "Panel",
            "widget_type": "metric_card",
            "data_payload": {"columns": ["a"], "rows": []},
        },
        _context(uuid4(), tmp_path),
    )
    assert not result.ok
    assert "metric_card" in (result.error or "")


async def test_create_widget_rejects_none_identity(tmp_path: Path) -> None:
    """No run owner means no write."""
    context = ToolContext(
        runner=SimpleNamespace(), container_id="run-1", delivery_dir=tmp_path
    )
    result = await invoke_tool(
        "create_widget",
        {"title": "Panel", "widget_type": "metric_card", "data_payload": {"value": "1"}},
        context,
    )
    assert not result.ok


async def test_create_widget_rejects_user_id_arg(tmp_path: Path) -> None:
    """A user_id in args is invalid: identity never comes from the model."""
    result = await invoke_tool(
        "create_widget",
        {
            "title": "Panel",
            "widget_type": "metric_card",
            "data_payload": {"value": "1"},
            "user_id": str(uuid4()),
        },
        _context(uuid4(), tmp_path),
    )
    assert not result.ok
    assert "invalid arguments" in (result.error or "")


async def test_create_widget_rejects_bad_interval(tmp_path: Path) -> None:
    """A raw cron-style interval outside the allowlist fails."""
    result = await invoke_tool(
        "create_widget",
        {
            "title": "Panel",
            "widget_type": "metric_card",
            "data_payload": {"value": "1"},
            "refresh_interval_minutes": 5,
        },
        _context(uuid4(), tmp_path),
    )
    assert not result.ok


async def test_create_widget_enqueues_connector_refresh(tmp_path: Path) -> None:
    """A connector_query refresh enqueues exactly one refresh_widget job."""
    owner = uuid4()
    result = await invoke_tool(
        "create_widget",
        {
            "title": "Panel",
            "widget_type": "status_feed",
            "data_payload": {"entries": []},
            "refresh": {"kind": "connector_query", "connector_id": "gmail", "query": "is:unread"},
            "refresh_interval_minutes": 60,
        },
        _context(owner, tmp_path),
    )
    assert result.ok, result.error
    jobs = [
        job
        for job in JobsService._jobs.values()
        if job.type == "refresh_widget"
        and job.payload.get("widget_id") == result.content["widget_id"]
    ]
    assert len(jobs) == 1
    assert jobs[0].payload["user_id"] == str(owner)


async def test_update_widget_success(tmp_path: Path) -> None:
    """A matching payload update bumps the version."""
    owner = uuid4()
    created = await invoke_tool(
        "create_widget",
        {"title": "Panel", "widget_type": "metric_card", "data_payload": {"value": "1"}},
        _context(owner, tmp_path),
    )
    assert created.ok
    updated = await invoke_tool(
        "update_widget",
        {"widget_id": created.content["widget_id"], "data_payload": {"value": "2"}},
        _context(owner, tmp_path),
    )
    assert updated.ok, updated.error
    assert updated.content["version"] == 2


async def test_create_widget_rejects_missing_session(tmp_path: Path) -> None:
    """A context without a session fails instead of writing to memory."""
    context = replace(_context(uuid4(), tmp_path), session=None)
    result = await invoke_tool(
        "create_widget",
        {"title": "Panel", "widget_type": "metric_card", "data_payload": {"value": "1"}},
        context,
    )
    assert not result.ok
    assert "session" in (result.error or "")
    assert _WIDGETS_MEMORY == {}


async def test_update_widget_rejects_missing_session(tmp_path: Path) -> None:
    """An update without a session fails instead of writing to memory."""
    context = replace(_context(uuid4(), tmp_path), session=None)
    result = await invoke_tool(
        "update_widget",
        {"widget_id": str(uuid4()), "data_payload": {"value": "2"}},
        context,
    )
    assert not result.ok
    assert "session" in (result.error or "")


async def test_update_widget_rejects_cross_user(tmp_path: Path) -> None:
    """Another user cannot update the row, and the version is unchanged."""
    owner = uuid4()
    created = await invoke_tool(
        "create_widget",
        {"title": "Panel", "widget_type": "metric_card", "data_payload": {"value": "1"}},
        _context(owner, tmp_path),
    )
    assert created.ok
    denied = await invoke_tool(
        "update_widget",
        {"widget_id": created.content["widget_id"], "data_payload": {"value": "2"}},
        _context(uuid4(), tmp_path),
    )
    assert not denied.ok
    assert "not found" in (denied.error or "")
    service = _service()
    stored = await service.get_widget(UUID(created.content["widget_id"]), owner)
    assert stored is not None
    assert stored.version == 1


async def test_update_widget_rejects_mismatch(tmp_path: Path) -> None:
    """A mismatched payload fails and the version is unchanged."""
    owner = uuid4()
    created = await invoke_tool(
        "create_widget",
        {"title": "Panel", "widget_type": "metric_card", "data_payload": {"value": "1"}},
        _context(owner, tmp_path),
    )
    assert created.ok
    bad = await invoke_tool(
        "update_widget",
        {
            "widget_id": created.content["widget_id"],
            "data_payload": {"columns": ["a"], "rows": []},
        },
        _context(owner, tmp_path),
    )
    assert not bad.ok
    service = _service()
    stored = await service.get_widget(UUID(created.content["widget_id"]), owner)
    assert stored is not None
    assert stored.version == 1


async def test_update_widget_rejects_none_identity(tmp_path: Path) -> None:
    """No run owner means no update."""
    context = ToolContext(
        runner=SimpleNamespace(), container_id="run-1", delivery_dir=tmp_path
    )
    result = await invoke_tool(
        "update_widget",
        {"widget_id": str(uuid4()), "data_payload": {"value": "1"}},
        context,
    )
    assert not result.ok


async def test_update_widget_bad_args_do_not_raise(tmp_path: Path) -> None:
    """A missing widget_id returns a failed result instead of raising."""
    result = await invoke_tool(
        "update_widget",
        {"data_payload": {"value": "1"}},
        _context(uuid4(), tmp_path),
    )
    assert not result.ok


def test_widget_arg_models_forbid_extra() -> None:
    """All widget arg models reject unknown keys at the boundary."""
    from pydantic import ValidationError

    from src.modules.runs.tools.widgets import CreateWidgetArgs, DeleteWidgetArgs, UpdateWidgetArgs

    with pytest.raises(ValidationError):
        CreateWidgetArgs.model_validate(
            {
                "title": "t",
                "widget_type": "metric_card",
                "data_payload": {"value": "1"},
                "user_id": str(uuid4()),
            }
        )
    with pytest.raises(ValidationError):
        UpdateWidgetArgs.model_validate(
            {
                "widget_id": str(uuid4()),
                "data_payload": {"value": "1"},
                "user_id": str(uuid4()),
            }
        )
    with pytest.raises(ValidationError):
        DeleteWidgetArgs.model_validate(
            {"widget_id": str(uuid4()), "user_id": str(uuid4())}
        )


async def test_two_contexts_share_one_user_widget(tmp_path: Path) -> None:
    """Two sessions for one user see and modify the same widget."""
    owner = uuid4()
    created = await invoke_tool(
        "create_widget",
        {"title": "Shared", "widget_type": "metric_card", "data_payload": {"value": "1"}},
        _context(owner, tmp_path),
    )
    assert created.ok, created.error
    widget_id = created.content["widget_id"]

    second_session_context = _context(owner, tmp_path)
    service = _service()
    stored = await service.get_widget(UUID(widget_id), owner)
    assert stored is not None
    assert stored.title == "Shared"

    updated = await invoke_tool(
        "update_widget",
        {"widget_id": widget_id, "data_payload": {"value": "2"}},
        second_session_context,
    )
    assert updated.ok, updated.error
    assert updated.content["version"] == 2
    reread = await service.get_widget(UUID(widget_id), owner)
    assert reread is not None
    assert reread.data_payload["value"] == "2"


async def test_delete_widget_success(tmp_path: Path) -> None:
    """An owned widget deletes and disappears from the store."""
    owner = uuid4()
    created = await invoke_tool(
        "create_widget",
        {"title": "Panel", "widget_type": "metric_card", "data_payload": {"value": "1"}},
        _context(owner, tmp_path),
    )
    assert created.ok
    removed = await invoke_tool(
        "delete_widget",
        {"widget_id": created.content["widget_id"]},
        _context(owner, tmp_path),
    )
    assert removed.ok, removed.error
    assert removed.content["widget_id"] == created.content["widget_id"]
    service = _service()
    assert await service.get_widget(UUID(created.content["widget_id"]), owner) is None


async def test_delete_widget_rejects_cross_user(tmp_path: Path) -> None:
    """Another user cannot delete the row, and the row survives."""
    owner = uuid4()
    created = await invoke_tool(
        "create_widget",
        {"title": "Panel", "widget_type": "metric_card", "data_payload": {"value": "1"}},
        _context(owner, tmp_path),
    )
    assert created.ok
    denied = await invoke_tool(
        "delete_widget",
        {"widget_id": created.content["widget_id"]},
        _context(uuid4(), tmp_path),
    )
    assert not denied.ok
    assert "not found" in (denied.error or "")
    service = _service()
    assert await service.get_widget(UUID(created.content["widget_id"]), owner) is not None


async def test_delete_widget_rejects_none_identity(tmp_path: Path) -> None:
    """No run owner means no delete."""
    context = ToolContext(
        runner=SimpleNamespace(), container_id="run-1", delivery_dir=tmp_path
    )
    result = await invoke_tool(
        "delete_widget",
        {"widget_id": str(uuid4())},
        context,
    )
    assert not result.ok


async def test_delete_widget_rejects_missing_session(tmp_path: Path) -> None:
    """A delete without a session fails instead of writing to memory."""
    context = replace(_context(uuid4(), tmp_path), session=None)
    result = await invoke_tool(
        "delete_widget",
        {"widget_id": str(uuid4())},
        context,
    )
    assert not result.ok
    assert "session" in (result.error or "")
