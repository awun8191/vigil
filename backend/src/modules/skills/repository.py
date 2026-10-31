"""Skills repository protocol and data transfer objects."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID


@dataclass(frozen=True)
class SkillDTO:
    """Represents an installed capability skill."""
    id: UUID
    name: str
    description: str
    version: int
    source: str  # builtin, git, upload
    commit: str | None
    enabled: bool
    storage_path: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SkillCreateDTO:
    """Payload for registering a new skill in the registry."""
    name: str
    description: str
    storage_path: str
    version: int = 1
    source: str = "builtin"
    commit: str | None = None
    enabled: bool = True


@runtime_checkable
class SkillsRepository(Protocol):
    """Protocol contract for the skill registry and manifest storage."""

    async def get_by_name(self, name: str) -> SkillDTO | None:
        """
        Retrieve a skill by its unique name identifier.

        Args:
            name: String name of the skill.

        Returns:
            SkillDTO if found, None otherwise.
        """
        ...

    async def get_by_id(self, skill_id: UUID) -> SkillDTO | None:
        """
        Retrieve a skill by its primary key.

        Args:
            skill_id: UUID of the skill.

        Returns:
            SkillDTO if found, None otherwise.
        """
        ...

    async def list_skills(self, enabled_only: bool = False) -> list[SkillDTO]:
        """
        List all installed skills with optional enabled filtering.

        Args:
            enabled_only: If True, returns only enabled skills.

        Returns:
            List of SkillDTOs.
        """
        ...

    async def save_skill(self, data: SkillCreateDTO) -> SkillDTO:
        """
        Register a new skill in the registry.

        Args:
            data: Skill creation DTO.

        Returns:
            The created SkillDTO.
        """
        ...

    async def update_status(self, skill_id: UUID, enabled: bool) -> SkillDTO | None:
        """
        Enable or disable a skill.

        Args:
            skill_id: UUID of the skill.
            enabled: Desired enabled status.

        Returns:
            Updated SkillDTO, or None if not found.
        """
        ...

    async def delete_skill(self, skill_id: UUID) -> bool:
        """
        Unregister and remove a skill from the registry.

        Args:
            skill_id: UUID of the skill to delete.

        Returns:
            True if deleted, False if not found.
        """
        ...
