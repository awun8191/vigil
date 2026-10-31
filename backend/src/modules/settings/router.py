"""Settings router factory providing preference management and audit log inspection."""
from fastapi import APIRouter, Depends, Query, status

from src.config.settings import Settings
from src.dependencies import get_current_user, get_settings_service
from src.modules.auth.repository import UserDTO
from src.modules.settings.data import (
    AuditLogResponse,
    SystemLimitsResponse,
    UserSettingsResponse,
    UserSettingsUpdateRequest,
)
from src.modules.settings.repository import SettingsRepository


def build_settings_router(settings: Settings) -> APIRouter:
    """
    Build and configure settings APIRouter instance.
    Endpoints:
      - GET /api/v1/settings
      - PATCH /api/v1/settings
      - GET /api/v1/settings/limits
      - GET /api/v1/settings/audit-logs
    """
    router = APIRouter(prefix="/settings", tags=["Settings"])

    @router.get(
        "",
        response_model=UserSettingsResponse,
        status_code=status.HTTP_200_OK,
        summary="Get User Settings",
    )
    async def get_settings(
        service: SettingsRepository = Depends(get_settings_service),
        user: UserDTO = Depends(get_current_user),
    ) -> UserSettingsResponse:
        user_settings = await service.get_user_settings(user.id)
        if not user_settings:
            user_settings = await service.update_user_settings(user.id, {})
        return UserSettingsResponse.model_validate(user_settings)

    @router.patch(
        "",
        response_model=UserSettingsResponse,
        status_code=status.HTTP_200_OK,
        summary="Update User Settings",
    )
    async def update_settings(
        payload: UserSettingsUpdateRequest,
        service: SettingsRepository = Depends(get_settings_service),
        user: UserDTO = Depends(get_current_user),
    ) -> UserSettingsResponse:
        updates = payload.model_dump(exclude_unset=True)
        updated = await service.update_user_settings(user.id, updates)
        return UserSettingsResponse.model_validate(updated)

    @router.get(
        "/limits",
        response_model=SystemLimitsResponse,
        status_code=status.HTTP_200_OK,
        summary="Get System Limits",
    )
    async def get_limits(
        service: SettingsRepository = Depends(get_settings_service),
        _user: UserDTO = Depends(get_current_user),
    ) -> SystemLimitsResponse:
        return service.get_system_limits()  # type: ignore[attr-defined]

    @router.get(
        "/audit-logs",
        response_model=list[AuditLogResponse],
        status_code=status.HTTP_200_OK,
        summary="List Audit Log Entries",
    )
    async def list_audit(
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        service: SettingsRepository = Depends(get_settings_service),
        _user: UserDTO = Depends(get_current_user),
    ) -> list[AuditLogResponse]:
        entries = await service.list_audit_entries(limit=limit, offset=offset)
        return [AuditLogResponse.model_validate(e) for e in entries]

    return router
