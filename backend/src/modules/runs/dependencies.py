"""Runs module dependencies."""
from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings, get_settings
from src.modules.runs.service import RunsService


def get_runs_service(
    session: AsyncSession,
    settings: Settings = Depends(get_settings),
) -> RunsService:
    """Dependency provider for RunsService."""
    return RunsService(session=session, settings=settings)
