"""Widgets domain module."""
from src.modules.widgets.data import (
    ConnectorQueryRefresh,
    FeedEntry,
    FeedPayload,
    MetricPayload,
    StaticRefresh,
    TablePayload,
    TaskItem,
    TaskListPayload,
    WidgetCreateRequest,
    WidgetResponse,
    WidgetUpdateRequest,
    validate_widget_payload,
)
from src.modules.widgets.dependencies import get_widgets_service
from src.modules.widgets.repository import (
    WidgetCreateDTO,
    WidgetDTO,
    WidgetsRepository,
    WidgetUpdateDTO,
)
from src.modules.widgets.router import build_widgets_router
from src.modules.widgets.service import WidgetsService
from src.modules.widgets.stream import (
    format_widget_event,
    parse_widget_cursor,
    stream_widget_events,
)

__all__ = [
    "ConnectorQueryRefresh",
    "FeedEntry",
    "FeedPayload",
    "MetricPayload",
    "StaticRefresh",
    "TablePayload",
    "TaskItem",
    "TaskListPayload",
    "WidgetCreateDTO",
    "WidgetCreateRequest",
    "WidgetDTO",
    "WidgetResponse",
    "WidgetUpdateDTO",
    "WidgetUpdateRequest",
    "WidgetsRepository",
    "WidgetsService",
    "build_widgets_router",
    "format_widget_event",
    "get_widgets_service",
    "parse_widget_cursor",
    "stream_widget_events",
    "validate_widget_payload",
]
