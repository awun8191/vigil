"""Health module dependencies."""
from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings, get_settings
from src.modules.health.service import HealthService


def get_health_service(
    session: AsyncSession,
    settings: Settings = Depends(get_settings),
) -> HealthService:
    """Dependency provider for HealthService."""
    return HealthService(session=session, settings=settings)
