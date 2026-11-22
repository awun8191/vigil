"""Projects module for scoped project workspaces."""
from src.modules.projects.data import (
    ProjectCreateRequest,
    ProjectDetailResponse,
    ProjectFileResponse,
    ProjectResponse,
    ProjectUpdateRequest,
)
from src.modules.projects.dependencies import get_projects_service
from src.modules.projects.repository import (
    ProjectCreateDTO,
    ProjectDTO,
    ProjectFileDTO,
    ProjectsRepository,
    ProjectUpdateDTO,
)
from src.modules.projects.router import build_projects_router
from src.modules.projects.service import ProjectsService

__all__ = [
    "ProjectCreateDTO",
    "ProjectCreateRequest",
    "ProjectDTO",
    "ProjectDetailResponse",
    "ProjectFileDTO",
    "ProjectFileResponse",
    "ProjectResponse",
    "ProjectsRepository",
    "ProjectsService",
    "ProjectUpdateDTO",
    "ProjectUpdateRequest",
    "build_projects_router",
    "get_projects_service",
]
