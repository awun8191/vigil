"""Settings module dependencies."""
from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings, get_settings
from src.modules.settings.service import SettingsService


def get_settings_service(
    session: AsyncSession,
    settings: Settings = Depends(get_settings),
) -> SettingsService:
    """Dependency provider for SettingsService."""
    return SettingsService(session=session, settings=settings)
