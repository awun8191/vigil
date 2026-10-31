"""Auth router factory providing device pairing and identity endpoints."""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status

from src.config.settings import Settings
from src.dependencies import get_auth_service, get_current_device, get_current_user
from src.modules.auth.data import (
    AuthMeResponse,
    PairingRequest,
    PairingResponse,
    RevokeDeviceRequest,
    RevokeDeviceResponse,
)
from src.modules.auth.repository import AuthRepository, DeviceDTO, UserDTO


def build_auth_router(settings: Settings) -> APIRouter:
    """
    Build and configure auth APIRouter instance.
    Endpoints:
      - POST /api/v1/auth/pair
      - GET /api/v1/auth/me
      - POST /api/v1/auth/revoke
    """
    router = APIRouter(prefix="/auth", tags=["Auth"])

    @router.post(
        "/pair",
        response_model=PairingResponse,
        status_code=status.HTTP_200_OK,
        summary="Pair Client Device",
        description="Exchange a one-time startup pairing code for a permanent device bearer token.",
    )
    async def pair_device(
        payload: PairingRequest,
        service: AuthRepository = Depends(get_auth_service),
    ) -> PairingResponse:
        return await service.pair_device(payload)  # type: ignore[attr-defined]

    @router.get(
        "/me",
        response_model=AuthMeResponse,
        status_code=status.HTTP_200_OK,
        summary="Get Authenticated Device Identity",
        description="Verify device bearer token and return device and user metadata.",
    )
    async def get_me(
        device: DeviceDTO = Depends(get_current_device),
        user: UserDTO = Depends(get_current_user),
    ) -> AuthMeResponse:
        return AuthMeResponse(
            device_id=device.id,
            device_name=device.name,
            user_id=user.id,
            user_display_name=user.display_name,
            created_at=user.created_at,
            last_seen_at=device.last_seen_at,
        )

    @router.post(
        "/revoke",
        response_model=RevokeDeviceResponse,
        status_code=status.HTTP_200_OK,
        summary="Revoke Device Token",
        description="Revoke an existing device token to permanently disable its access.",
    )
    async def revoke_device(
        payload: RevokeDeviceRequest,
        service: AuthRepository = Depends(get_auth_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> RevokeDeviceResponse:
        await service.revoke_device(payload.device_id)
        return RevokeDeviceResponse(
            device_id=payload.device_id,
            revoked=True,
            revoked_at=datetime.now(timezone.utc),
        )

    return router
