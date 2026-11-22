"""Dashboard widget tools: ``create_widget``, ``update_widget``, ``delete_widget``.

All widget tools are ``local``, so the existing run approval policy gates dashboard
writes. Identity always comes from the ``ToolContext``, never from tool args:
any ``user_id`` in args is rejected by ``extra="forbid"``, and a ``None``
context identity fails the call instead of falling back to an arg-supplied id.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.modules.runs.tools import Tool, ToolContext, ToolResult
from src.modules.widgets.data import (
    WidgetType,
    merge_a2ui_payload_update,
    validate_widget_payload,
)


class CreateWidgetArgs(BaseModel):
    """Arguments for the ``create_widget`` tool."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(
        ...,
        min_length=1,
        max_length=120,
        description="Short panel title shown in the dashboard rail header.",
    )
    widget_type: WidgetType = Field(
        default="a2ui_surface",
        description="Must be 'a2ui_surface'.",
    )
    data_payload: dict[str, Any] = Field(
        ...,
        description=(
            "A2UI v0.9 surface specification object with keys: "
            "'root' (str root component id), "
            "'size' ('sm'|'md'|'lg', optional), "
            "'icon' (optional: 'activity'|'calendar'|'check-square'|'clock'|'credit-card'|'flame'|'git-branch'|'globe'|'heart-pulse'|'inbox'|'layers'|'mail'|'music'|'package'|'sparkles'|'train-front'|'trending-up'|'zap'), "
            "'components' (flat list of {id, component, props, children} where component is one of "
            "'Column', 'Row', 'Divider', 'Metric', 'Sparkline', 'ProgressBar', 'Badge', 'KeyValueList', 'DataTable', 'Text', 'CheckList', 'Button'), "
            "and 'data_model' (JSON object referenced by props via RFC 6901 JSON Pointers like {'path': '/summary/value'}). "
            "CRITICAL: NEVER populate 'data_model' with invented or sample numbers, fake gigs, or placeholder tasks. "
            "Use only real values retrieved from memory_search / connector tools, values explicitly provided by the user via ask_user, "
            "or truthful zero/empty defaults ('0', '—', []). "
            "Schema structure example: {'icon': 'clock', 'root': 'col_1', 'components': [{'id': 'col_1', 'component': 'Column', 'children': ['m_1', 'kv_1']}, "
            "{'id': 'm_1', 'component': 'Metric', 'props': {'label': 'Tracked Total', 'value': {'path': '/total'}, 'tone': 'neutral'}}, "
            "{'id': 'kv_1', 'component': 'KeyValueList', 'props': {'items': {'path': '/items'}}}], "
            "'data_model': {'total': '0', 'items': []}}"
        ),
    )
    refresh: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional refresh source: {'kind': 'connector_query', 'connector_id': 'gmail', 'query': 'is:unread'} "
            "or {'kind': 'static'}."
        ),
    )
    refresh_interval_minutes: int | None = Field(
        default=None,
        description="Optional refresh cadence in minutes. Must be one of: 15, 60, 240, 1440.",
    )


class UpdateWidgetArgs(BaseModel):
    """Arguments for the ``update_widget`` tool."""

    model_config = ConfigDict(extra="forbid")

    widget_id: UUID = Field(...)
    data_payload: dict[str, Any] = Field(...)


class DeleteWidgetArgs(BaseModel):
    """Arguments for the ``delete_widget`` tool."""

    model_config = ConfigDict(extra="forbid")

    widget_id: UUID = Field(...)


def _widgets_service(context: ToolContext) -> Any:
    """Return the widget store the tools write through.

    The store uses the run executor session, so tool writes land in the
    same Postgres database the API reads. A missing session fails loudly
    instead of writing to process-local memory.
    """
    if context.session is None:
        raise RuntimeError("widget tools need a database session, got none")
    from src.config.settings import Settings
    from src.modules.widgets.service import WidgetsService

    return WidgetsService(session=context.session, settings=Settings())


def _next_refresh_at(interval_minutes: int | None) -> datetime | None:
    """Compute the next refresh time from an allowlisted interval."""
    if interval_minutes is None:
        return None
    return datetime.now(timezone.utc) + timedelta(minutes=interval_minutes)


def _parse_refresh(
    refresh: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate the refresh block and return its config plus an error."""
    if refresh is None:
        return None, None
    from src.modules.widgets.data import ConnectorQueryRefresh, StaticRefresh

    kind = refresh.get("kind") if isinstance(refresh, dict) else None
    model = (
        ConnectorQueryRefresh if kind == "connector_query"
        else StaticRefresh if kind == "static"
        else None
    )
    if model is None:
        return None, "invalid refresh config: kind must be connector_query or static"
    try:
        parsed = model.model_validate(refresh)
    except ValidationError as exc:
        return None, f"invalid refresh config: {exc}"
    return parsed.model_dump(), None


async def create_widget(args: CreateWidgetArgs, context: ToolContext) -> ToolResult:
    """Create one dashboard widget scoped to the context user."""
    if context.user_id is None:
        return ToolResult.failure("create_widget needs a run owner, got none")
    if args.refresh_interval_minutes is not None and args.refresh_interval_minutes not in (
        15,
        60,
        240,
        1440,
    ):
        return ToolResult.failure(
            "refresh_interval_minutes must be one of 15, 60, 240, 1440"
        )
    try:
        parsed_payload = validate_widget_payload(args.widget_type, args.data_payload)
    except (ValueError, ValidationError) as exc:
        return ToolResult.failure(
            f"payload does not match {args.widget_type}: {exc}\n"
            'Expected a2ui_surface shape: {"root": "main", "components": ['
            '{"id": "main", "component": {"Column": {"children": {"explicitList": ["title_txt"]}}}}, '
            '{"id": "title_txt", "component": {"Text": {"text": {"path": "/label"}, "variant": "h2"}}}], '
            '"data_model": {"label": "Value"}, "catalog_id": "urn:vigil:a2ui:catalog:panels:v1"}'
        )
    refresh_config, refresh_error = _parse_refresh(args.refresh)
    if refresh_error is not None:
        return ToolResult.failure(refresh_error)
    from src.modules.widgets.repository import WidgetCreateDTO

    service = _widgets_service(context)
    created = await service.create_widget(
        WidgetCreateDTO(
            user_id=context.user_id,
            title=args.title,
            widget_type=args.widget_type,
            data_payload=parsed_payload.model_dump(),
            refresh_config=refresh_config,
            refresh_interval_minutes=args.refresh_interval_minutes,
            next_refresh_at=_next_refresh_at(args.refresh_interval_minutes),
        )
    )
    if refresh_config is not None and refresh_config.get("kind") == "connector_query":
        from src.modules.jobs.repository import JobCreateDTO
        from src.modules.jobs.service import JobsService

        assert context.session is not None
        jobs = JobsService(session=context.session, settings=service.settings)
        await jobs.enqueue(
            JobCreateDTO(
                type="refresh_widget",
                payload={"widget_id": str(created.id), "user_id": str(context.user_id)},
                run_at=created.next_refresh_at,
            )
        )
    return ToolResult.success(
        {
            "widget_id": str(created.id),
            "widget_type": created.widget_type,
            "title": created.title,
            "version": 1,
        }
    )


async def update_widget(args: UpdateWidgetArgs, context: ToolContext) -> ToolResult:
    """Replace one widget payload scoped to the context user."""
    if context.user_id is None:
        return ToolResult.failure("update_widget needs a run owner, got none")
    from src.modules.widgets.repository import WidgetUpdateDTO

    service = _widgets_service(context)
    existing = await service.get_widget(args.widget_id, context.user_id)
    if existing is None:
        return ToolResult.failure(f"widget {args.widget_id} not found")
    try:
        parsed_payload = merge_a2ui_payload_update(
            existing.data_payload, args.data_payload
        )
    except (ValueError, ValidationError) as exc:
        return ToolResult.failure(
            f"payload does not match {existing.widget_type}: {exc}"
        )
    updated = await service.update_widget_payload(
        args.widget_id,
        context.user_id,
        WidgetUpdateDTO(data_payload=parsed_payload.model_dump()),
    )
    if updated is None:
        return ToolResult.failure(f"widget {args.widget_id} not found")
    return ToolResult.success(
        {"widget_id": str(updated.id), "version": updated.version}
    )


async def delete_widget(args: DeleteWidgetArgs, context: ToolContext) -> ToolResult:
    """Delete one dashboard widget scoped to the context user."""
    if context.user_id is None:
        return ToolResult.failure("delete_widget needs a run owner, got none")
    service = _widgets_service(context)
    removed = await service.delete_widget(args.widget_id, context.user_id)
    if not removed:
        return ToolResult.failure(f"widget {args.widget_id} not found")
    return ToolResult.success({"widget_id": str(args.widget_id)})


WIDGET_TOOLS: tuple[Tool, ...] = (
    Tool.define(
        name="create_widget",
        description=(
            "Create a persistent A2UI (v0.9) dashboard panel for the user using catalog "
            "'urn:vigil:a2ui:catalog:panels:v1' (flat components list + JSON Pointer data_model)."
        ),
        level="local",
        args_model=CreateWidgetArgs,
        handler=create_widget,
    ),
    Tool.define(
        name="update_widget",
        description=(
            "Update an owned A2UI dashboard widget's data_model (via {'data_model': ...} or "
            "{'updateDataModel': ...}) or replace its full A2UI surface layout and data."
        ),
        level="local",
        args_model=UpdateWidgetArgs,
        handler=update_widget,
    ),
    Tool.define(
        name="delete_widget",
        description="Delete one dashboard widget the user owns.",
        level="local",
        args_model=DeleteWidgetArgs,
        handler=delete_widget,
    ),
)


__all__ = [
    "WIDGET_TOOLS",
    "CreateWidgetArgs",
    "UpdateWidgetArgs",
    "DeleteWidgetArgs",
    "create_widget",
    "update_widget",
    "delete_widget",
]
