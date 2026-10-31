"""Jobs module dependencies."""
from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings, get_settings
from src.modules.jobs.service import JobsService


def get_jobs_service(
    session: AsyncSession,
    settings: Settings = Depends(get_settings),
) -> JobsService:
    """Dependency provider for JobsService."""
    return JobsService(session=session, settings=settings)
