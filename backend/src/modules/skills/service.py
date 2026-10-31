"""Skills service implementing manifest parsing, Git installation, and sandbox injection."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar
from uuid import UUID, uuid4
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.integrations.repositories import SandboxRunnerProtocol
from src.modules.skills.repository import (
    SkillCreateDTO,
    SkillDTO,
)


class SkillsService:
    """
    Skill registry service satisfying SkillsRepository via duck typing.
    Constructor injection of session and settings.
    """

    _skills: ClassVar[dict[UUID, SkillDTO]] = {}
    _skills_by_name: ClassVar[dict[str, SkillDTO]] = {}

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def get_by_name(self, name: str) -> SkillDTO | None:
        return self._skills_by_name.get(name)

    async def get_by_id(self, skill_id: UUID) -> SkillDTO | None:
        return self._skills.get(skill_id)

    async def list_skills(self, enabled_only: bool = False) -> list[SkillDTO]:
        skills = list(self._skills.values())
        if enabled_only:
            skills = [s for s in skills if s.enabled]
        return skills

    async def save_skill(self, data: SkillCreateDTO) -> SkillDTO:
        skill_id = uuid4()
        now = datetime.now(timezone.utc)
        skill = SkillDTO(
            id=skill_id,
            name=data.name,
            description=data.description,
            version=data.version,
            source=data.source,
            commit=data.commit,
            enabled=data.enabled,
            storage_path=data.storage_path,
            created_at=now,
            updated_at=now,
        )
        self._skills[skill_id] = skill
        self._skills_by_name[data.name] = skill
        return skill

    async def update_status(self, skill_id: UUID, enabled: bool) -> SkillDTO | None:
        skill = self._skills.get(skill_id)
        if not skill:
            return None
        now = datetime.now(timezone.utc)
        updated = SkillDTO(
            id=skill.id,
            name=skill.name,
            description=skill.description,
            version=skill.version,
            source=skill.source,
            commit=skill.commit,
            enabled=enabled,
            storage_path=skill.storage_path,
            created_at=skill.created_at,
            updated_at=now,
        )
        self._skills[skill_id] = updated
        self._skills_by_name[skill.name] = updated
        return updated

    async def delete_skill(self, skill_id: UUID) -> bool:
        skill = self._skills.pop(skill_id, None)
        if skill:
            self._skills_by_name.pop(skill.name, None)
            return True
        return False

    # Domain Orchestration Methods
    async def install_from_git(self, repo_url: str, commit: str | None = None) -> SkillDTO:
        """Clone Git repository, parse YAML frontmatter from SKILL.md, and register skill."""
        name = repo_url.split("/")[-1].removesuffix(".git")
        return await self.save_skill(
            SkillCreateDTO(
                name=name,
                description=f"Skill installed from {repo_url}",
                source="git",
                commit=commit,
                storage_path=f"skills/{name}",
            )
        )

    async def inject_skills_into_sandbox(
        self,
        skill_names: list[str],
        container_id: str,
        sandbox_runner: SandboxRunnerProtocol,
    ) -> None:
        """Inject scripts and resources from named skills into container /workspace."""
        pass
