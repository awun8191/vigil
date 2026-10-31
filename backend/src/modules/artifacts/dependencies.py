"""Artifacts module dependencies."""
from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings, get_settings
from src.modules.artifacts.service import ArtifactsService


def get_artifacts_service(
    session: AsyncSession,
    settings: Settings = Depends(get_settings),
) -> ArtifactsService:
    """Dependency provider for ArtifactsService."""
    return ArtifactsService(session=session, settings=settings)
