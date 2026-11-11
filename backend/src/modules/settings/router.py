from typing import Any
from fastapi import APIRouter, Depends, Query, status

from src.config.exceptions import NotFoundError, ValidationError
from src.config.settings import Settings
from src.dependencies import (
    get_current_user,
    get_key_vault_service,
    get_settings_service,
)
from src.modules.auth.repository import UserDTO
from src.modules.settings.data import (
    AuditLogResponse,
    SystemLimitsResponse,
    UserSettingsResponse,
    UserSettingsUpdateRequest,
)
from src.modules.settings.repository import SettingsRepository
from src.modules.settings.vault import (
    KeyVaultService,
    SetSecretRequest,
    VaultSecretDTO,
)
from src.modules.settings.validators import (
    ValidateSecretRequest,
    VaultValidationResponse,
    validate_api_key,
)


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
    async def get_user_settings(
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

    @router.get(
        "/vault",
        response_model=list[VaultSecretDTO],
        status_code=status.HTTP_200_OK,
        summary="List Stored Vault Secrets",
    )
    async def list_vault_secrets(
        vault: KeyVaultService = Depends(get_key_vault_service),
        user: UserDTO = Depends(get_current_user),
    ) -> list[VaultSecretDTO]:
        return await vault.list_secrets(user.id)

    @router.post(
        "/vault",
        status_code=status.HTTP_200_OK,
        summary="Store or Update Vault Secret",
    )
    async def set_vault_secret(
        payload: SetSecretRequest,
        vault: KeyVaultService = Depends(get_key_vault_service),
        user: UserDTO = Depends(get_current_user),
    ) -> dict[str, Any]:
        try:
            await vault.set_secret(user.id, payload.key, payload.value)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return {"ok": True, "key": payload.key}

    @router.post(
        "/vault/test",
        response_model=VaultValidationResponse,
        status_code=status.HTTP_200_OK,
        summary="Validate a Secret Before Storing It",
    )
    async def test_vault_secret(
        payload: ValidateSecretRequest,
        _user: UserDTO = Depends(get_current_user),
    ) -> VaultValidationResponse:
        """Run the provider check for a key without storing it."""
        return await validate_api_key(payload.key, payload.value, settings)

    @router.post(
        "/vault/{key}/validate",
        response_model=VaultValidationResponse,
        status_code=status.HTTP_200_OK,
        summary="Validate a Stored Vault Secret",
    )
    async def validate_stored_secret(
        key: str,
        vault: KeyVaultService = Depends(get_key_vault_service),
        user: UserDTO = Depends(get_current_user),
    ) -> VaultValidationResponse:
        """Read a stored key and run the provider check for it."""
        try:
            value = await vault.get_secret(user.id, key)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if value is None:
            raise NotFoundError(f"Secret {key!r} not found in vault")
        return await validate_api_key(key, value, settings)

    @router.delete(
        "/vault/{key}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Delete Stored Vault Secret",
    )
    async def delete_vault_secret(
        key: str,
        vault: KeyVaultService = Depends(get_key_vault_service),
        user: UserDTO = Depends(get_current_user),
    ) -> None:
        try:
            deleted = await vault.delete_secret(user.id, key)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if not deleted:
            raise NotFoundError(f"Secret {key!r} not found in vault")

    return router

