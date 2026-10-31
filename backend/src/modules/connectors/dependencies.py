"""Connectors module dependencies."""
from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings, get_settings
from src.modules.connectors.service import ConnectorsService


def get_connectors_service(
    session: AsyncSession,
    settings: Settings = Depends(get_settings),
) -> ConnectorsService:
    """Dependency provider for ConnectorsService."""
    return ConnectorsService(session=session, settings=settings)
