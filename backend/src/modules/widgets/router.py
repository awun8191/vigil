"""Widgets router factory providing dashboard panel endpoints and an SSE stream."""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import StreamingResponse
from pydantic import ValidationError as PydanticValidationError

from src.config.exceptions import NotFoundError, ValidationError
from src.config.settings import Settings
from src.dependencies import get_current_user, get_widgets_service
from src.modules.auth.repository import UserDTO
from src.modules.widgets.data import (
    WidgetCreateRequest,
    WidgetResponse,
    WidgetUpdateRequest,
    validate_widget_payload,
)
from src.modules.widgets.repository import WidgetCreateDTO, WidgetUpdateDTO
from src.modules.widgets.service import WidgetsService
from src.modules.widgets.stream import (
    SSE_HEADERS,
    parse_widget_cursor,
    stream_widget_events,
)


def _next_refresh_at(interval_minutes: int | None) -> datetime | None:
    """Compute the next refresh time from an allowlisted interval."""
    if interval_minutes is None:
        return None
    return datetime.now(timezone.utc) + timedelta(minutes=interval_minutes)


def _to_response(widget: object) -> WidgetResponse:
    """Convert a widget DTO to its wire representation."""
    return WidgetResponse.model_validate(widget)


def build_widgets_router(settings: Settings) -> APIRouter:
    """Build and configure the widgets APIRouter instance.

    Endpoints:
      - POST /api/v1/widgets
      - GET /api/v1/widgets
      - GET /api/v1/widgets/stream
      - PATCH /api/v1/widgets/{id}
      - DELETE /api/v1/widgets/{id}

    ``GET /stream`` is registered before ``PATCH /{id}`` so the stream path
    never reaches the UUID path parser.
    """
    router = APIRouter(prefix="/widgets", tags=["Widgets"])

    @router.post(
        "",
        response_model=WidgetResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Create Widget",
    )
    async def create_widget(
        payload: WidgetCreateRequest,
        service: WidgetsService = Depends(get_widgets_service),
        user: UserDTO = Depends(get_current_user),
    ) -> WidgetResponse:
        try:
            parsed = validate_widget_payload(payload.widget_type, payload.data_payload)
        except (ValueError, PydanticValidationError) as exc:
            raise ValidationError(f"payload does not match {payload.widget_type}: {exc}") from exc
        created = await service.create_widget(
            WidgetCreateDTO(
                user_id=user.id,
                title=payload.title,
                widget_type=payload.widget_type,
                data_payload=parsed.model_dump(),
                refresh_config=(
                    payload.refresh.model_dump() if payload.refresh is not None else None
                ),
                refresh_interval_minutes=payload.refresh_interval_minutes,
                next_refresh_at=_next_refresh_at(payload.refresh_interval_minutes),
            )
        )
        return _to_response(created)

    @router.get(
        "",
        response_model=list[WidgetResponse],
        status_code=status.HTTP_200_OK,
        summary="List Widgets",
    )
    async def list_widgets(
        service: WidgetsService = Depends(get_widgets_service),
        user: UserDTO = Depends(get_current_user),
    ) -> list[WidgetResponse]:
        widgets = await service.list_widgets(user.id)
        return [_to_response(w) for w in widgets]

    @router.get(
        "/stream",
        summary="Stream Widget Updates via SSE",
    )
    async def stream_widgets(
        since: str | None = Query(default=None),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        service: WidgetsService = Depends(get_widgets_service),
        user: UserDTO = Depends(get_current_user),
    ) -> StreamingResponse:
        """Stream widget rows updated after the client cursor.

        The ``Last-Event-ID`` header from a reconnect wins over the ``since``
        query parameter. Frames carry ``event: widget_updated`` with the row
        id, version, payload, and timestamp.
        """
        cursor = parse_widget_cursor(last_event_id, fallback=since)
        return StreamingResponse(
            stream_widget_events(service, user.id, cursor),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    @router.patch(
        "/{widget_id}",
        response_model=WidgetResponse,
        status_code=status.HTTP_200_OK,
        summary="Update Widget Payload",
    )
    async def update_widget(
        widget_id: UUID,
        payload: WidgetUpdateRequest,
        service: WidgetsService = Depends(get_widgets_service),
        user: UserDTO = Depends(get_current_user),
    ) -> WidgetResponse:
        if payload.widget_id != widget_id:
            raise ValidationError("widget_id in the body must match the path id")
        existing = await service.get_widget(widget_id, user.id)
        if existing is None:
            raise NotFoundError(f"Widget {widget_id} not found")
        try:
            parsed = validate_widget_payload(existing.widget_type, payload.data_payload)
        except (ValueError, PydanticValidationError) as exc:
            raise ValidationError(
                f"payload does not match {existing.widget_type}: {exc}"
            ) from exc
        updated = await service.update_widget_payload(
            widget_id, user.id, WidgetUpdateDTO(data_payload=parsed.model_dump())
        )
        if updated is None:
            raise NotFoundError(f"Widget {widget_id} not found")
        return _to_response(updated)

    @router.delete(
        "/{widget_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Delete Widget",
    )
    async def delete_widget(
        widget_id: UUID,
        service: WidgetsService = Depends(get_widgets_service),
        user: UserDTO = Depends(get_current_user),
    ) -> None:
        removed = await service.delete_widget(widget_id, user.id)
        if not removed:
            raise NotFoundError(f"Widget {widget_id} not found")
        return None

    return router
