"""Unit tests for the A2UI refresh_widget worker handler and the widget sweeper."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from src.config.settings import Settings
from src.modules.jobs.repository import JobDTO
from src.modules.jobs.service import JOB_TYPE_PRIORITIES, priority_for_job_type
from src.modules.widgets.data import A2UI_KV_MAX_ITEMS, A2UI_TABLE_MAX_ROWS, validate_widget_payload
from src.modules.widgets.repository import WidgetCreateDTO
from src.modules.widgets.service import WidgetsService
import src.worker as worker_mod
from src.worker import (
    JOB_HANDLERS,
    JobContext,
    JobHandlerUnavailableError,
    PermanentJobError,
    UnknownJobTypeError,
    handle_refresh_widget,
    resolve_handler,
    sweep_due_widget_refreshes,
)


def _a2ui_metric_surface(value: str = "1") -> dict[str, Any]:
    return validate_widget_payload(
        "a2ui_surface",
        {
            "root": "col_1",
            "components": [
                {"id": "col_1", "component": "Column", "children": ["kpi_1"]},
                {
                    "id": "kpi_1",
                    "component": "Metric",
                    "props": {"label": "Score", "value": {"path": "/value"}},
                },
            ],
            "data_model": {"value": value},
        },
    ).model_dump()


def _a2ui_table_surface(rows: list[list[str]] | None = None) -> dict[str, Any]:
    return validate_widget_payload(
        "a2ui_surface",
        {
            "root": "col_1",
            "components": [
                {"id": "col_1", "component": "Column", "children": ["tbl_1"]},
                {
                    "id": "tbl_1",
                    "component": "DataTable",
                    "props": {"columns": ["a"], "rows": {"path": "/rows"}},
                },
            ],
            "data_model": {"rows": rows or []},
        },
    ).model_dump()


def _a2ui_kv_surface(items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return validate_widget_payload(
        "a2ui_surface",
        {
            "root": "col_1",
            "components": [
                {"id": "col_1", "component": "Column", "children": ["kv_1"]},
                {
                    "id": "kv_1",
                    "component": "KeyValueList",
                    "props": {"items": {"path": "/entries"}, "max_items": 10},
                },
            ],
            "data_model": {"entries": items or []},
        },
    ).model_dump()


def _make_job(**overrides: Any) -> JobDTO:
    """Build a locked refresh_widget JobDTO."""
    now = datetime.now(timezone.utc)
    values: dict[str, Any] = {
        "id": uuid4(),
        "type": "refresh_widget",
        "payload": {},
        "run_at": now,
        "attempts": 1,
        "max_attempts": 5,
        "status": "locked",
        "locked_by": "worker-test",
        "locked_at": now,
        "last_error": None,
        "created_at": now,
        "updated_at": now,
        "priority": priority_for_job_type("refresh_widget"),
        "heartbeat_at": None,
    }
    values.update(overrides)
    return JobDTO(**values)


_WIDGETS_MEMORY: dict[Any, Any] = {}
"""In-memory widget rows shared by every service in one test."""


def _test_session() -> Any:
    """Return a stub session that shares the test widget store."""
    return SimpleNamespace(_widgets_memory=_WIDGETS_MEMORY)


def _make_context() -> JobContext:
    """Build a sessionless job context over the shared test store."""
    return JobContext(session=_test_session(), settings=Settings(APP_ENV="test"))


async def _create_widget(**overrides: Any):
    """Insert one a2ui_surface row in the sessionless store."""
    values: dict[str, Any] = {
        "user_id": uuid4(),
        "title": "Panel",
        "widget_type": "a2ui_surface",
        "data_payload": _a2ui_metric_surface("1"),
    }
    values.update(overrides)
    service = WidgetsService(session=_test_session(), settings=Settings(APP_ENV="test"))
    return await service.create_widget(WidgetCreateDTO(**values))


@pytest.fixture(autouse=True)
def _fresh_stores():
    """Clear shared stores and restore the connector executor around each test."""
    _WIDGETS_MEMORY.clear()
    previous = worker_mod.WIDGET_CONNECTOR_QUERY
    worker_mod.WIDGET_CONNECTOR_QUERY = None
    yield
    _WIDGETS_MEMORY.clear()
    worker_mod.WIDGET_CONNECTOR_QUERY = previous


def test_refresh_widget_registered_with_priority_40() -> None:
    """The queue knows refresh_widget and ranks it below index_memory."""
    assert JOB_TYPE_PRIORITIES["refresh_widget"] == 40
    assert JOB_HANDLERS["refresh_widget"] is handle_refresh_widget


def test_resolve_handler_returns_refresh_handler() -> None:
    """Dispatch resolves refresh_widget to its handler."""
    assert resolve_handler("refresh_widget") is handle_refresh_widget


def test_resolve_handler_rejects_unknown_type() -> None:
    """An unknown job type still raises UnknownJobTypeError."""
    with pytest.raises(UnknownJobTypeError):
        resolve_handler("no_such_job")


async def test_missing_ids_are_unavailable() -> None:
    """A payload without widget_id and user_id cannot run."""
    with pytest.raises(JobHandlerUnavailableError):
        await handle_refresh_widget(_make_job(payload={}), _make_context())


async def test_non_uuid_ids_are_unavailable() -> None:
    """A payload with unparseable ids cannot run."""
    job = _make_job(payload={"widget_id": "nope", "user_id": "nope"})
    with pytest.raises(JobHandlerUnavailableError):
        await handle_refresh_widget(job, _make_context())


async def test_missing_row_fails_permanently() -> None:
    """A refresh of a deleted widget fails at once instead of retrying."""
    job = _make_job(payload={"widget_id": str(uuid4()), "user_id": str(uuid4())})
    with pytest.raises(PermanentJobError):
        await handle_refresh_widget(job, _make_context())


async def test_static_refresh_only_reschedules() -> None:
    """A static refresh recomputes next_refresh_at without touching data."""
    owner = uuid4()
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    initial_payload = _a2ui_metric_surface("1")
    created = await _create_widget(
        user_id=owner,
        data_payload=initial_payload,
        refresh_config={"kind": "static"},
        refresh_interval_minutes=60,
        next_refresh_at=past,
    )
    job = _make_job(payload={"widget_id": str(created.id), "user_id": str(owner)})
    await handle_refresh_widget(job, _make_context())

    service = WidgetsService(session=_test_session(), settings=Settings(APP_ENV="test"))
    stored = await service.get_widget(created.id, owner)
    assert stored is not None
    assert stored.version == 1
    assert stored.data_payload == initial_payload
    assert stored.next_refresh_at is not None
    assert stored.next_refresh_at > past


async def test_unknown_kind_fails_permanently() -> None:
    """An unknown refresh kind is a poison payload, not a retry."""
    owner = uuid4()
    created = await _create_widget(
        user_id=owner,
        refresh_config={"kind": "sql_view"},
        refresh_interval_minutes=60,
        next_refresh_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    job = _make_job(payload={"widget_id": str(created.id), "user_id": str(owner)})
    with pytest.raises(PermanentJobError):
        await handle_refresh_widget(job, _make_context())


async def test_connector_query_without_executor_fails_permanently() -> None:
    """Without an executor a connector_query refresh cannot succeed by retry."""
    owner = uuid4()
    created = await _create_widget(
        user_id=owner,
        refresh_config={"kind": "connector_query", "connector_id": "gmail", "query": "q"},
        refresh_interval_minutes=60,
        next_refresh_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    job = _make_job(payload={"widget_id": str(created.id), "user_id": str(owner)})
    with pytest.raises(PermanentJobError):
        await handle_refresh_widget(job, _make_context())


async def test_connector_query_applies_truncated_data_model_and_preserves_components() -> None:
    """A connector refresh updates data_model only, preserves A2UI components, and caps table rows."""
    owner = uuid4()
    initial = _a2ui_table_surface([])
    created = await _create_widget(
        user_id=owner,
        title="Inbox",
        widget_type="a2ui_surface",
        data_payload=initial,
        refresh_config={"kind": "connector_query", "connector_id": "gmail", "query": "q"},
        refresh_interval_minutes=60,
        next_refresh_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )

    async def fake_executor(connector_id: str, query: str, user_id) -> dict[str, Any]:
        assert connector_id == "gmail"
        assert query == "q"
        assert user_id == owner
        return {"data_model": {"rows": [[str(i)] for i in range(80)]}}

    worker_mod.WIDGET_CONNECTOR_QUERY = fake_executor
    job = _make_job(payload={"widget_id": str(created.id), "user_id": str(owner)})
    await handle_refresh_widget(job, _make_context())

    service = WidgetsService(session=_test_session(), settings=Settings(APP_ENV="test"))
    stored = await service.get_widget(created.id, owner)
    assert stored is not None
    assert stored.version == 2
    assert stored.data_payload["components"] == initial["components"]
    assert len(stored.data_payload["data_model"]["rows"]) == A2UI_TABLE_MAX_ROWS
    assert stored.next_refresh_at is not None
    assert stored.next_refresh_at > datetime.now(timezone.utc)


async def test_connector_query_truncates_key_value_entries() -> None:
    """A refreshed A2UI KeyValueList keeps at most A2UI_KV_MAX_ITEMS entries."""
    owner = uuid4()
    created = await _create_widget(
        user_id=owner,
        widget_type="a2ui_surface",
        data_payload=_a2ui_kv_surface([]),
        refresh_config={"kind": "connector_query", "connector_id": "gmail", "query": "q"},
        refresh_interval_minutes=60,
        next_refresh_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )

    async def fake_executor(connector_id: str, query: str, user_id) -> dict[str, Any]:
        return {
            "data_model": {
                "entries": [
                    {"label": f"Item {i}", "value": f"line {i}"} for i in range(45)
                ]
            }
        }

    worker_mod.WIDGET_CONNECTOR_QUERY = fake_executor
    job = _make_job(payload={"widget_id": str(created.id), "user_id": str(owner)})
    await handle_refresh_widget(job, _make_context())

    service = WidgetsService(session=_test_session(), settings=Settings(APP_ENV="test"))
    stored = await service.get_widget(created.id, owner)
    assert stored is not None
    assert stored.version == 2
    assert len(stored.data_payload["data_model"]["entries"]) == A2UI_KV_MAX_ITEMS


async def test_transient_connector_error_retries() -> None:
    """A connector failure raises a retryable error and writes nothing."""
    owner = uuid4()
    created = await _create_widget(
        user_id=owner,
        refresh_config={"kind": "connector_query", "connector_id": "gmail", "query": "q"},
        refresh_interval_minutes=60,
        next_refresh_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )

    async def failing_executor(connector_id: str, query: str, user_id) -> dict[str, Any]:
        raise ConnectionError("connector is down")

    worker_mod.WIDGET_CONNECTOR_QUERY = failing_executor
    job = _make_job(payload={"widget_id": str(created.id), "user_id": str(owner)})
    with pytest.raises(RuntimeError):
        await handle_refresh_widget(job, _make_context())

    service = WidgetsService(session=_test_session(), settings=Settings(APP_ENV="test"))
    stored = await service.get_widget(created.id, owner)
    assert stored is not None
    assert stored.version == 1


async def test_invalid_refreshed_payload_fails_permanently() -> None:
    """A refreshed payload that breaks the A2UI schema or pointers is never written."""
    owner = uuid4()
    initial = _a2ui_metric_surface("1")
    created = await _create_widget(
        user_id=owner,
        data_payload=initial,
        refresh_config={"kind": "connector_query", "connector_id": "gmail", "query": "q"},
        refresh_interval_minutes=60,
        next_refresh_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )

    async def bad_executor(connector_id: str, query: str, user_id) -> dict[str, Any]:
        return {"wrong": "shape"}

    worker_mod.WIDGET_CONNECTOR_QUERY = bad_executor
    job = _make_job(payload={"widget_id": str(created.id), "user_id": str(owner)})
    with pytest.raises(PermanentJobError):
        await handle_refresh_widget(job, _make_context())

    service = WidgetsService(session=_test_session(), settings=Settings(APP_ENV="test"))
    stored = await service.get_widget(created.id, owner)
    assert stored is not None
    assert stored.version == 1
    assert stored.data_payload == initial


async def test_sweeper_enqueues_one_job_per_due_row() -> None:
    """Due rows each get one refresh_widget job; future rows get none."""
    from src.modules.jobs.service import JobsService

    JobsService._jobs.clear()
    settings = Settings(APP_ENV="test")
    owner = uuid4()
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    service = WidgetsService(session=_test_session(), settings=settings)
    await service.create_widget(
        WidgetCreateDTO(
            user_id=owner,
            title="Due",
            widget_type="a2ui_surface",
            data_payload=_a2ui_metric_surface("1"),
            refresh_config={"kind": "static"},
            refresh_interval_minutes=60,
            next_refresh_at=past,
        )
    )
    await service.create_widget(
        WidgetCreateDTO(
            user_id=owner,
            title="Future",
            widget_type="a2ui_surface",
            data_payload=_a2ui_metric_surface("1"),
            refresh_config={"kind": "static"},
            refresh_interval_minutes=60,
            next_refresh_at=future,
        )
    )

    enqueued = await sweep_due_widget_refreshes(_test_session(), settings)
    assert enqueued == 1

    rerun = await sweep_due_widget_refreshes(_test_session(), settings)
    assert rerun == 0
    JobsService._jobs.clear()
