"""Unit tests for the widgets service over the sessionless store.

No database is needed: the service falls back to its in-memory dict when the
session is None, with the same user scoping and version semantics.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.config.settings import Settings
from src.modules.widgets.repository import WidgetCreateDTO, WidgetUpdateDTO
from src.modules.widgets.service import WidgetsService


def _service() -> WidgetsService:
    """Return a sessionless service with a fresh store."""
    service = WidgetsService(session=None, settings=Settings(APP_ENV="test"))
    service._widgets.clear()
    return service


def _dto(user_id, **overrides):
    """Build a metric_card creation DTO for one user."""
    values = {
        "user_id": user_id,
        "title": "Panel",
        "widget_type": "metric_card",
        "data_payload": {"value": "1", "delta": None, "caption": None},
    }
    values.update(overrides)
    return WidgetCreateDTO(**values)


async def test_create_assigns_version_one() -> None:
    """A new widget starts at version 1."""
    service = _service()
    created = await service.create_widget(_dto(uuid4()))
    assert created.version == 1
    assert created.title == "Panel"


async def test_create_computes_no_refresh_by_default() -> None:
    """Without an interval there is no next refresh time."""
    service = _service()
    created = await service.create_widget(_dto(uuid4()))
    assert created.next_refresh_at is None


async def test_get_scopes_by_user() -> None:
    """Another user cannot read the row."""
    service = _service()
    owner = uuid4()
    created = await service.create_widget(_dto(owner))
    assert await service.get_widget(created.id, owner) is not None
    assert await service.get_widget(created.id, uuid4()) is None


async def test_list_returns_only_own_rows() -> None:
    """A list holds the caller rows and nothing else."""
    service = _service()
    owner = uuid4()
    await service.create_widget(_dto(owner, title="Mine"))
    await service.create_widget(_dto(uuid4(), title="Theirs"))
    rows = await service.list_widgets(owner)
    assert [r.title for r in rows] == ["Mine"]


async def test_update_bumps_version() -> None:
    """A payload update bumps the version and the timestamp."""
    service = _service()
    owner = uuid4()
    created = await service.create_widget(_dto(owner))
    updated = await service.update_widget_payload(
        created.id,
        owner,
        WidgetUpdateDTO(data_payload={"value": "2", "delta": None, "caption": None}),
    )
    assert updated is not None
    assert updated.version == 2
    assert updated.data_payload["value"] == "2"
    assert updated.updated_at >= created.updated_at


async def test_update_rejects_other_user() -> None:
    """A cross-user update returns None and leaves the version unchanged."""
    service = _service()
    created = await service.create_widget(_dto(uuid4()))
    result = await service.update_widget_payload(
        created.id,
        uuid4(),
        WidgetUpdateDTO(data_payload={"value": "2", "delta": None, "caption": None}),
    )
    assert result is None
    stored = await service.get_widget(created.id, created.user_id)
    assert stored is not None
    assert stored.version == 1


async def test_claim_due_refresh_orders_and_limits() -> None:
    """Due rows come first by refresh time, future rows never appear."""
    service = _service()
    owner = uuid4()
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    first = await service.create_widget(_dto(owner, title="first", next_refresh_at=past))
    second = await service.create_widget(
        _dto(owner, title="second", next_refresh_at=past - timedelta(minutes=1))
    )
    await service.create_widget(_dto(owner, title="future", next_refresh_at=future))
    await service.create_widget(_dto(owner, title="none"))
    due = await service.claim_due_refresh(limit=50)
    assert [w.id for w in due] == [second.id, first.id]


async def test_apply_refresh_bumps_version_and_reschedules() -> None:
    """A refresh writes data, bumps the version, and moves the next run."""
    service = _service()
    owner = uuid4()
    created = await service.create_widget(_dto(owner))
    moment = datetime.now(timezone.utc) + timedelta(minutes=15)
    applied = await service.apply_refresh(
        created.id,
        owner,
        {"value": "9", "delta": None, "caption": None},
        moment,
    )
    assert applied is not None
    assert applied.version == 2
    assert applied.data_payload["value"] == "9"
    assert applied.next_refresh_at == moment


async def test_touch_refresh_keeps_version() -> None:
    """A static refresh reschedules without a version bump."""
    service = _service()
    owner = uuid4()
    created = await service.create_widget(_dto(owner))
    moment = datetime.now(timezone.utc) + timedelta(minutes=60)
    touched = await service.touch_refresh(created.id, owner, moment)
    assert touched is not None
    assert touched.version == 1
    assert touched.next_refresh_at == moment
    assert touched.data_payload == created.data_payload


async def test_created_widget_visible_to_fresh_service_instance() -> None:
    """A fresh service on the same store lists the created widget."""
    from types import SimpleNamespace

    shared: dict = {}
    first = WidgetsService(session=SimpleNamespace(_widgets_memory=shared), settings=Settings(APP_ENV="test"))  # type: ignore[arg-type]
    owner = uuid4()
    created = await first.create_widget(_dto(owner, title="Persisted"))
    fresh = WidgetsService(session=SimpleNamespace(_widgets_memory=shared), settings=Settings(APP_ENV="test"))  # type: ignore[arg-type]
    rows = await fresh.list_widgets(owner)
    assert [r.id for r in rows] == [created.id]
    assert rows[0].title == "Persisted"
    assert await fresh.get_widget(created.id, owner) is not None


async def test_delete_removes_owned_widget() -> None:
    """A delete removes the row and a second delete reports False."""
    service = _service()
    owner = uuid4()
    created = await service.create_widget(_dto(owner))
    assert await service.delete_widget(created.id, owner) is True
    assert await service.get_widget(created.id, owner) is None
    assert await service.list_widgets(owner) == []
    assert await service.delete_widget(created.id, owner) is False


async def test_delete_rejects_other_user() -> None:
    """A cross-user delete reports False and keeps the row."""
    service = _service()
    created = await service.create_widget(_dto(uuid4()))
    assert await service.delete_widget(created.id, uuid4()) is False
    assert await service.get_widget(created.id, created.user_id) is not None
