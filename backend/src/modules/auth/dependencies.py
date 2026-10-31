"""Auth module dependencies."""
from __future__ import annotations

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.exceptions import UnauthorizedError
from src.config.settings import Settings, get_settings
from src.modules.auth.repository import DeviceDTO, UserDTO
from src.modules.auth.service import AuthService


def get_auth_service(
    session: AsyncSession,
    settings: Settings = Depends(get_settings),
) -> AuthService:
    """Dependency provider for AuthService."""
    return AuthService(session=session, settings=settings)


async def get_current_device(
    authorization: str = Header(..., description="Device Bearer token: 'Bearer vgt_...'"),
    settings: Settings = Depends(get_settings),
    session: AsyncSession = None,  # type: ignore[assignment]
) -> DeviceDTO:
    """FastAPI dependency resolving and authenticating the calling client device."""
    service = AuthService(session=session, settings=settings)
    device, _user = await service.authenticate_token(authorization)
    return device


async def get_current_user(
    device: DeviceDTO = Depends(get_current_device),
    settings: Settings = Depends(get_settings),
    session: AsyncSession = None,  # type: ignore[assignment]
) -> UserDTO:
    """FastAPI dependency resolving the User entity owning the authenticated device."""
    service = AuthService(session=session, settings=settings)
    user = await service.get_user_by_id(device.user_id)
    if not user:
        user = await service.get_or_create_default_user()
    return user
