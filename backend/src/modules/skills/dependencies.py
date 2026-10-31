"""Skills module dependencies."""
from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings, get_settings
from src.modules.skills.service import SkillsService


def get_skills_service(
    session: AsyncSession,
    settings: Settings = Depends(get_settings),
) -> SkillsService:
    """Dependency provider for SkillsService."""
    return SkillsService(session=session, settings=settings)
