"""Pydantic v2 request and response wire schemas for the widgets module."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MetricPayload(BaseModel):
    """Payload of a metric_card widget."""

    model_config = ConfigDict(extra="forbid")

    value: str = Field(..., min_length=1, max_length=64)
    delta: str | None = Field(default=None, max_length=64)
    caption: str | None = Field(default=None, max_length=200)


class TablePayload(BaseModel):
    """Payload of a data_table widget."""

    model_config = ConfigDict(extra="forbid")

    columns: list[str] = Field(..., min_length=1, max_length=8)
    rows: list[list[str]] = Field(default_factory=list, max_length=50)


class TaskItem(BaseModel):
    """One checkable row of a task_list widget."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=64)
    label: str = Field(..., min_length=1, max_length=200)
    done: bool = False


class TaskListPayload(BaseModel):
    """Payload of a task_list widget."""

    model_config = ConfigDict(extra="forbid")

    items: list[TaskItem] = Field(default_factory=list, max_length=50)


class FeedEntry(BaseModel):
    """One timestamped line of a status_feed widget."""

    model_config = ConfigDict(extra="forbid")

    ts: str = Field(..., min_length=1, max_length=64)
    text: str = Field(..., min_length=1, max_length=280)


class FeedPayload(BaseModel):
    """Payload of a status_feed widget."""

    model_config = ConfigDict(extra="forbid")

    entries: list[FeedEntry] = Field(default_factory=list, max_length=30)


WidgetType = Literal["metric_card", "data_table", "task_list", "status_feed"]

#: Payload model for each widget type, used for type-matched validation.
WIDGET_PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "metric_card": MetricPayload,
    "data_table": TablePayload,
    "task_list": TaskListPayload,
    "status_feed": FeedPayload,
}


def validate_widget_payload(widget_type: str, payload: dict[str, Any]) -> BaseModel:
    """Parse a raw payload dict against the model of its widget type.

    Raises:
        ValueError: The widget type is unknown or the payload fails validation.
    """
    model = WIDGET_PAYLOAD_MODELS.get(widget_type)
    if model is None:
        raise ValueError(f"unknown widget_type: {widget_type!r}")
    return model.model_validate(payload)


class ConnectorQueryRefresh(BaseModel):
    """Refresh policy backed by one connector query."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["connector_query"] = "connector_query"
    connector_id: str = Field(..., min_length=1, max_length=64)
    query: str = Field(..., min_length=1, max_length=500)
    prompt_hint: str | None = Field(default=None, max_length=1000)


class StaticRefresh(BaseModel):
    """Refresh policy that only recomputes the next refresh time."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["static"] = "static"


RefreshConfig = Annotated[ConnectorQueryRefresh | StaticRefresh, Field(discriminator="kind")]

RefreshInterval = Literal[15, 60, 240, 1440]


class WidgetCreateRequest(BaseModel):
    """Inbound request for creating a dashboard widget."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=120)
    widget_type: WidgetType = Field(...)
    data_payload: dict[str, Any] = Field(...)
    refresh: RefreshConfig | None = None
    refresh_interval_minutes: RefreshInterval | None = None


class WidgetUpdateRequest(BaseModel):
    """Inbound payload update for an existing widget. Title is user editable later."""

    model_config = ConfigDict(extra="forbid")

    widget_id: UUID = Field(...)
    data_payload: dict[str, Any] = Field(...)


class WidgetResponse(BaseModel):
    """Wire representation of one dashboard widget."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(...)
    user_id: UUID = Field(...)
    title: str = Field(...)
    widget_type: str = Field(...)
    data_payload: dict[str, Any] = Field(...)
    refresh_config: dict[str, Any] | None = Field(default=None)
    refresh_interval_minutes: int | None = Field(default=None)
    next_refresh_at: datetime | None = Field(default=None)
    version: int = Field(...)
    updated_at: datetime = Field(...)
