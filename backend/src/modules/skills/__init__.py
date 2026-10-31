"""Skills domain module."""
from src.modules.skills.data import (
    SkillInstallRequest,
    SkillResponse,
    SkillToggleRequest,
)
from src.modules.skills.dependencies import get_skills_service
from src.modules.skills.repository import (
    SkillCreateDTO,
    SkillDTO,
    SkillsRepository,
)
from src.modules.skills.router import build_skills_router
from src.modules.skills.service import SkillsService

__all__ = [
    "SkillCreateDTO",
    "SkillDTO",
    "SkillInstallRequest",
    "SkillResponse",
    "SkillToggleRequest",
    "SkillsRepository",
    "SkillsService",
    "build_skills_router",
    "get_skills_service",
]
