"""Widgets repository protocol and data transfer objects."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID


@dataclass(frozen=True)
class WidgetDTO:
    """One dashboard panel row owned by one user."""

    id: UUID
    user_id: UUID
    title: str
    widget_type: str
    data_payload: dict[str, Any]
    refresh_config: dict[str, Any] | None
    refresh_interval_minutes: int | None
    next_refresh_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class WidgetCreateDTO:
    """Payload for creating a dashboard widget."""

    user_id: UUID
    title: str
    widget_type: str
    data_payload: dict[str, Any]
    refresh_config: dict[str, Any] | None = None
    refresh_interval_minutes: int | None = None
    next_refresh_at: datetime | None = None


@dataclass(frozen=True)
class WidgetUpdateDTO:
    """Payload for updating a widget data payload."""

    data_payload: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class WidgetsRepository(Protocol):
    """Protocol contract for user-scoped dashboard widget storage."""

    async def get_widget(self, widget_id: UUID, user_id: UUID) -> WidgetDTO | None:
        """Return one widget row scoped to its owner, or None."""
        ...

    async def list_widgets(self, user_id: UUID) -> list[WidgetDTO]:
        """List every widget row of one user, newest first."""
        ...

    async def create_widget(self, data: WidgetCreateDTO) -> WidgetDTO:
        """Insert one widget row."""
        ...

    async def update_widget_payload(
        self, widget_id: UUID, user_id: UUID, data: WidgetUpdateDTO
    ) -> WidgetDTO | None:
        """Replace the payload of one owned widget, bumping version."""
        ...

    async def delete_widget(self, widget_id: UUID, user_id: UUID) -> bool:
        """Delete one owned widget. Returns True when a row was removed."""
        ...
