"""Projects router factory providing scoped workspace endpoints."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status

from src.config.exceptions import NotFoundError
from src.config.settings import Settings
from src.dependencies import get_current_user, get_projects_service
from src.modules.auth.repository import UserDTO
from src.modules.projects.data import (
    ProjectCreateRequest,
    ProjectDetailResponse,
    ProjectFileResponse,
    ProjectResponse,
    ProjectUpdateRequest,
)
from src.modules.projects.repository import ProjectCreateDTO, ProjectUpdateDTO
from src.modules.projects.service import MAX_UPLOAD_FILE_BYTES, ProjectsService


def build_projects_router(settings: Settings) -> APIRouter:
    """Build and configure projects APIRouter instance."""
    router = APIRouter(prefix="/projects", tags=["Projects"])

    @router.get(
        "",
        response_model=list[ProjectResponse],
        status_code=status.HTTP_200_OK,
        summary="List Project Workspaces",
    )
    async def list_projects(
        service: ProjectsService = Depends(get_projects_service),
        user: UserDTO = Depends(get_current_user),
    ) -> list[ProjectResponse]:
        items = await service.list_projects(user.id)
        return [ProjectResponse.model_validate(item) for item in items]

    @router.post(
        "",
        response_model=ProjectResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Create Project Workspace",
    )
    async def create_project(
        payload: ProjectCreateRequest,
        service: ProjectsService = Depends(get_projects_service),
        user: UserDTO = Depends(get_current_user),
    ) -> ProjectResponse:
        project = await service.create_project(
            ProjectCreateDTO(
                user_id=user.id,
                name=payload.name,
                description=payload.description,
                instructions=payload.instructions,
            )
        )
        return ProjectResponse.model_validate(project)

    @router.get(
        "/{project_id}",
        response_model=ProjectDetailResponse,
        status_code=status.HTTP_200_OK,
        summary="Get Project Workspace Detail",
    )
    async def get_project(
        project_id: UUID,
        service: ProjectsService = Depends(get_projects_service),
        user: UserDTO = Depends(get_current_user),
    ) -> ProjectDetailResponse:
        project = await service.get_owned_project(project_id, user.id)
        files = [
            ProjectFileResponse.model_validate(f)
            for f in service.list_workspace_files(project)
        ]
        return ProjectDetailResponse(
            **ProjectResponse.model_validate(project).model_dump(),
            files=files,
        )

    @router.patch(
        "/{project_id}",
        response_model=ProjectResponse,
        status_code=status.HTTP_200_OK,
        summary="Update Project Workspace",
    )
    async def update_project(
        project_id: UUID,
        payload: ProjectUpdateRequest,
        service: ProjectsService = Depends(get_projects_service),
        user: UserDTO = Depends(get_current_user),
    ) -> ProjectResponse:
        await service.get_owned_project(project_id, user.id)
        updated = await service.update_project(
            project_id,
            ProjectUpdateDTO(
                name=payload.name,
                description=payload.description,
                instructions=payload.instructions,
            ),
        )
        if updated is None:
            raise NotFoundError(f"Project {project_id} not found")
        return ProjectResponse.model_validate(updated)

    @router.post(
        "/{project_id}/files",
        response_model=list[ProjectFileResponse],
        status_code=status.HTTP_201_CREATED,
        summary="Upload Files To Project Workspace",
    )
    async def upload_project_files(
        project_id: UUID,
        files: list[UploadFile] = File(...),
        service: ProjectsService = Depends(get_projects_service),
        user: UserDTO = Depends(get_current_user),
    ) -> list[ProjectFileResponse]:
        project = await service.get_owned_project(project_id, user.id)
        for upload in files:
            content = await upload.read()
            if len(content) > MAX_UPLOAD_FILE_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File {upload.filename!r} exceeds maximum upload size of 50 MB.",
                )
            service.save_workspace_file(project, upload.filename, content)
        return [
            ProjectFileResponse.model_validate(f)
            for f in service.list_workspace_files(project)
        ]

    @router.delete(
        "/{project_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Delete Project Workspace",
    )
    async def delete_project(
        project_id: UUID,
        service: ProjectsService = Depends(get_projects_service),
        user: UserDTO = Depends(get_current_user),
    ) -> Response:
        await service.get_owned_project(project_id, user.id)
        await service.delete_project(project_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
