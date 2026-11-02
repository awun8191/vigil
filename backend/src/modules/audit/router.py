"""Audit router factory: one read endpoint over the append-only log.

There is no write route by design. A stored event cannot be created, changed,
or removed through the API. The business services append the events.
"""
from fastapi import APIRouter, Depends, Query, status

from src.config.settings import Settings
from src.dependencies import get_audit_service, get_current_user
from src.modules.audit.data import AuditLogResponse, AuditPageResponse
from src.modules.audit.repository import AuditRepository
from src.modules.audit.service import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from src.modules.auth.repository import UserDTO


def build_audit_router(settings: Settings) -> APIRouter:
    """
    Build and configure the audit APIRouter instance.

    Endpoints:
      - GET /api/v1/audit

    The log is append only. No POST, PUT, PATCH, or DELETE route exists.
    """
    router = APIRouter(prefix="/audit", tags=["Audit"])

    @router.get(
        "",
        response_model=AuditPageResponse,
        status_code=status.HTTP_200_OK,
        summary="List Audit Events",
    )
    async def list_audit(
        limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
        cursor: str | None = Query(
            default=None, description="Cursor from the previous page."
        ),
        action: str | None = Query(
            default=None, max_length=64, description="Filter on one action name."
        ),
        service: AuditRepository = Depends(get_audit_service),
        _user: UserDTO = Depends(get_current_user),
    ) -> AuditPageResponse:
        """Return one cursor page of events, newest first."""
        page = await service.list_page(limit=limit, cursor=cursor, action=action)
        return AuditPageResponse(
            items=[AuditLogResponse.model_validate(event) for event in page.items],
            next_cursor=page.next_cursor,
        )

    return router
