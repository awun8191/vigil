"""Projects service managing scoped project workspaces and file inspection."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.exceptions import NotFoundError
from src.modules.projects.repository import (
    ProjectCreateDTO,
    ProjectDTO,
    ProjectFileDTO,
    ProjectsRepository,
    ProjectUpdateDTO,
)
from src.modules.projects.state import (
    InMemoryProjectsRepository,
    SqlProjectsRepository,
    sanitize_workspace_filename,
)

MAX_WORKSPACE_FILES = 200
MAX_UPLOAD_FILE_BYTES = 50 * 1024 * 1024


class ProjectsService:
    """Projects service satisfying ProjectsRepository via duck typing."""

    _repo: ProjectsRepository

    def __init__(
        self,
        session: AsyncSession | None,
        settings: object,
        repository: ProjectsRepository | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        if repository is not None:
            self._repo = repository
        elif session is not None:
            self._repo = SqlProjectsRepository(session, settings=settings)
        else:
            self._repo = InMemoryProjectsRepository(settings=settings)

    async def list_projects(
        self,
        user_id: UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ProjectDTO]:
        return await self._repo.list_projects(user_id, limit=limit, offset=offset)

    async def get_project(self, project_id: UUID) -> ProjectDTO | None:
        return await self._repo.get_project(project_id)

    async def get_owned_project(self, project_id: UUID, user_id: UUID) -> ProjectDTO:
        """Return project if it belongs to `user_id`, otherwise raise NotFoundError."""
        project = await self.get_project(project_id)
        if project is None or project.user_id != user_id:
            raise NotFoundError(f"Project {project_id} not found")
        return project

    async def create_project(self, data: ProjectCreateDTO) -> ProjectDTO:
        return await self._repo.create_project(data)

    async def update_project(
        self,
        project_id: UUID,
        data: ProjectUpdateDTO,
    ) -> ProjectDTO | None:
        return await self._repo.update_project(project_id, data)

    async def delete_project(self, project_id: UUID) -> bool:
        return await self._repo.delete_project(project_id)

    def list_workspace_files(
        self, project: ProjectDTO, limit: int = MAX_WORKSPACE_FILES
    ) -> list[ProjectFileDTO]:
        """List non-hidden regular files inside `project.workspace_path`."""
        root = Path(project.workspace_path)
        if not root.is_dir():
            return []
        resolved_root = root.resolve()
        files: list[ProjectFileDTO] = []
        for candidate in sorted(resolved_root.rglob("*")):
            if candidate.is_symlink() or not candidate.is_file():
                continue
            try:
                rel = candidate.resolve().relative_to(resolved_root)
            except ValueError:
                continue
            if any(part.startswith(".") for part in rel.parts):
                continue
            stat = candidate.stat()
            files.append(
                ProjectFileDTO(
                    path=rel.as_posix(),
                    size_bytes=stat.st_size,
                    updated_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                )
            )
            if len(files) >= limit:
                break
        return files

    def save_workspace_file(
        self,
        project: ProjectDTO,
        filename: str | None,
        content: bytes,
    ) -> ProjectFileDTO:
        """Write one uploaded file safely inside `project.workspace_path`."""
        safe_name = sanitize_workspace_filename(filename)
        root = Path(project.workspace_path)
        root.mkdir(parents=True, exist_ok=True)
        resolved_root = root.resolve()
        target = (resolved_root / safe_name).resolve()
        if target.parent != resolved_root:
            raise ValueError(f"Upload path escapes project workspace: {safe_name!r}")
        target.write_bytes(content)
        stat = target.stat()
        return ProjectFileDTO(
            path=safe_name,
            size_bytes=stat.st_size,
            updated_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        )
