"""SQL and in-memory project workspace stores."""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.projects.repository import (
    ProjectCreateDTO,
    ProjectDTO,
    ProjectUpdateDTO,
)
from src.schemas.projects import ProjectModel

_SLUG_CLEAN_RE = re.compile(r"[^a-z0-9]+")
_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def slugify_project_name(name: str) -> str:
    """Return a clean lowercase filesystem-safe slug for a project name."""
    cleaned = _SLUG_CLEAN_RE.sub("-", name.strip().lower()).strip("-")
    return (cleaned or "project")[:80]


def sanitize_workspace_filename(filename: str | None) -> str:
    """Return a safe single-segment file name for an uploaded workspace file."""
    raw = (filename or "").replace("\\", "/").replace("\x00", "").strip()
    leaf = Path(raw).name if raw else ""
    if not leaf or leaf in (".", ".."):
        return "attachment.bin"
    stem = Path(leaf).stem
    suffix = Path(leaf).suffix
    clean_stem = _FILENAME_SAFE_RE.sub("_", stem).strip("._-") or "file"
    clean_suffix = _FILENAME_SAFE_RE.sub("", suffix)
    if clean_suffix and not clean_suffix.startswith("."):
        clean_suffix = f".{clean_suffix}"
    return f"{clean_stem[:160]}{clean_suffix[:32]}"


def resolve_project_workspace_dir(
    settings: object,
    user_id: UUID,
    slug: str,
) -> Path:
    """Resolve and create `<WORKSPACE_BASE_PATH>/projects/<user_id>/<slug>` safely."""
    configured = Path(getattr(settings, "WORKSPACE_BASE_PATH", "/tmp/vigil/workspaces"))
    candidates = [
        configured / "projects" / str(user_id) / slug,
        Path("/tmp/vigil/workspaces") / "projects" / str(user_id) / slug,
    ]
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate.resolve()
        except OSError:
            continue
    return candidates[-1]


def resolve_session_workspace_dir(
    settings: object,
    session_id: UUID | str,
) -> Path:
    """Resolve and create `<WORKSPACE_BASE_PATH>/sessions/<session_id>` safely."""
    configured = Path(getattr(settings, "WORKSPACE_BASE_PATH", "/tmp/vigil/workspaces"))
    sid = str(session_id).strip() or "default"
    candidates = [
        configured / "sessions" / sid,
        Path("/tmp/vigil/workspaces") / "sessions" / sid,
    ]
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate.resolve()
        except OSError:
            continue
    return candidates[-1]


class InMemoryProjectsRepository:
    """Process-local project workspace store for tests and simulation."""

    def __init__(self, settings: object | None = None) -> None:
        self._settings = settings
        self._projects: dict[UUID, ProjectDTO] = {}

    def _unique_slug(self, user_id: UUID, base_slug: str) -> str:
        existing = {p.slug for p in self._projects.values() if p.user_id == user_id}
        if base_slug not in existing:
            return base_slug
        suffix = 2
        while f"{base_slug}-{suffix}" in existing:
            suffix += 1
        return f"{base_slug}-{suffix}"

    async def list_projects(
        self,
        user_id: UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ProjectDTO]:
        items = [p for p in self._projects.values() if p.user_id == user_id]
        items.sort(key=lambda p: (p.updated_at, p.created_at), reverse=True)
        return items[offset : offset + limit]

    async def get_project(self, project_id: UUID) -> ProjectDTO | None:
        return self._projects.get(project_id)

    async def create_project(self, data: ProjectCreateDTO) -> ProjectDTO:
        base_slug = slugify_project_name(data.slug or data.name)
        slug = self._unique_slug(data.user_id, base_slug)
        workspace_dir = resolve_project_workspace_dir(self._settings, data.user_id, slug)
        now = _now()
        dto = ProjectDTO(
            id=uuid4(),
            user_id=data.user_id,
            name=data.name.strip(),
            slug=slug,
            description=data.description.strip() if data.description else None,
            workspace_path=str(workspace_dir),
            instructions=data.instructions.strip() if data.instructions else None,
            created_at=now,
            updated_at=now,
        )
        self._projects[dto.id] = dto
        return dto

    async def update_project(
        self,
        project_id: UUID,
        data: ProjectUpdateDTO,
    ) -> ProjectDTO | None:
        existing = self._projects.get(project_id)
        if existing is None:
            return None
        updated = replace(
            existing,
            name=data.name.strip() if data.name is not None else existing.name,
            description=(
                data.description.strip() or None
                if data.description is not None
                else existing.description
            ),
            instructions=(
                data.instructions.strip() or None
                if data.instructions is not None
                else existing.instructions
            ),
            updated_at=_now(),
        )
        self._projects[project_id] = updated
        return updated

    async def delete_project(self, project_id: UUID) -> bool:
        return self._projects.pop(project_id, None) is not None


class SqlProjectsRepository:
    """PostgreSQL project workspace repository."""

    def __init__(self, session: AsyncSession, settings: object | None = None) -> None:
        self.session = session
        self._settings = settings

    @staticmethod
    def _dto(model: ProjectModel) -> ProjectDTO:
        return ProjectDTO(
            id=model.id,
            user_id=model.user_id,
            name=model.name,
            slug=model.slug,
            description=model.description,
            workspace_path=model.workspace_path,
            instructions=model.instructions,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def _unique_slug(self, user_id: UUID, base_slug: str) -> str:
        stmt = select(ProjectModel.slug).where(ProjectModel.user_id == user_id)
        existing = set((await self.session.execute(stmt)).scalars().all())
        if base_slug not in existing:
            return base_slug
        suffix = 2
        while f"{base_slug}-{suffix}" in existing:
            suffix += 1
        return f"{base_slug}-{suffix}"

    async def list_projects(
        self,
        user_id: UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ProjectDTO]:
        stmt = (
            select(ProjectModel)
            .where(ProjectModel.user_id == user_id)
            .order_by(ProjectModel.updated_at.desc(), ProjectModel.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._dto(row) for row in rows]

    async def get_project(self, project_id: UUID) -> ProjectDTO | None:
        model = await self.session.get(ProjectModel, project_id)
        return self._dto(model) if model is not None else None

    async def create_project(self, data: ProjectCreateDTO) -> ProjectDTO:
        base_slug = slugify_project_name(data.slug or data.name)
        slug = await self._unique_slug(data.user_id, base_slug)
        workspace_dir = resolve_project_workspace_dir(self._settings, data.user_id, slug)
        now = _now()
        model = ProjectModel(
            id=uuid4(),
            user_id=data.user_id,
            name=data.name.strip(),
            slug=slug,
            description=data.description.strip() if data.description else None,
            workspace_path=str(workspace_dir),
            instructions=data.instructions.strip() if data.instructions else None,
            created_at=now,
            updated_at=now,
        )
        self.session.add(model)
        await self.session.flush()
        return self._dto(model)

    async def update_project(
        self,
        project_id: UUID,
        data: ProjectUpdateDTO,
    ) -> ProjectDTO | None:
        model = await self.session.get(ProjectModel, project_id)
        if model is None:
            return None
        if data.name is not None:
            model.name = data.name.strip()
        if data.description is not None:
            model.description = data.description.strip() or None
        if data.instructions is not None:
            model.instructions = data.instructions.strip() or None
        model.updated_at = _now()
        await self.session.flush()
        return self._dto(model)

    async def delete_project(self, project_id: UUID) -> bool:
        model = await self.session.get(ProjectModel, project_id)
        if model is None:
            return False
        await self.session.delete(model)
        await self.session.flush()
        return True
