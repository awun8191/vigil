"""Projects repository protocol and data transfer objects."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID


@dataclass(frozen=True)
class ProjectDTO:
    """Represents a scoped project workspace."""

    id: UUID
    user_id: UUID
    name: str
    slug: str
    description: str | None
    workspace_path: str
    instructions: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ProjectCreateDTO:
    """Payload for creating a scoped project workspace."""

    user_id: UUID
    name: str
    description: str | None = None
    instructions: str | None = None
    slug: str | None = None


@dataclass(frozen=True)
class ProjectUpdateDTO:
    """Partial update payload for a scoped project workspace."""

    name: str | None = None
    description: str | None = None
    instructions: str | None = None


@dataclass(frozen=True)
class ProjectFileDTO:
    """Metadata for a file inside a project's workspace directory."""

    path: str
    size_bytes: int
    updated_at: datetime


@runtime_checkable
class ProjectsRepository(Protocol):
    """Protocol contract for scoped project workspaces."""

    async def list_projects(
        self,
        user_id: UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ProjectDTO]:
        """List scoped project workspaces owned by a user."""
        ...

    async def get_project(self, project_id: UUID) -> ProjectDTO | None:
        """Retrieve a project workspace by its UUID."""
        ...

    async def create_project(self, data: ProjectCreateDTO) -> ProjectDTO:
        """Create a project workspace and provision its directory."""
        ...

    async def update_project(
        self,
        project_id: UUID,
        data: ProjectUpdateDTO,
    ) -> ProjectDTO | None:
        """Update project metadata or instructions."""
        ...

    async def delete_project(self, project_id: UUID) -> bool:
        """Delete a project workspace record."""
        ...
