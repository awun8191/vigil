"""Compaction module dependencies."""
from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings, get_settings
from src.modules.compaction.service import CompactionService


def get_compaction_service(
    session: AsyncSession,
    settings: Settings = Depends(get_settings),
) -> CompactionService:
    """Dependency provider for CompactionService."""
    return CompactionService(session=session, settings=settings)
