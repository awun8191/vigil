"""Projects module dependency provider."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.modules.projects.service import ProjectsService


def get_projects_service(
    session: AsyncSession,
    settings: Settings,
) -> ProjectsService:
    """Dependency provider for ProjectsService."""
    return ProjectsService(session=session, settings=settings)
