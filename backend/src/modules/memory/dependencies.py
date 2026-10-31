"""Memory module dependencies."""
from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings, get_settings
from src.modules.memory.service import MemoryService


def get_memory_service(
    session: AsyncSession,
    settings: Settings = Depends(get_settings),
) -> MemoryService:
    """Dependency provider for MemoryService."""
    return MemoryService(session=session, settings=settings)
