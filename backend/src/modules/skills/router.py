"""Skills router factory providing skill registry, Git installation, and status toggles."""
from uuid import UUID
from fastapi import APIRouter, Depends, Query, status

from src.config.exceptions import NotFoundError
from src.config.settings import Settings
from src.dependencies import get_current_device, get_skills_service
from src.modules.auth.repository import DeviceDTO
from src.modules.skills.data import SkillInstallRequest, SkillResponse, SkillToggleRequest
from src.modules.skills.repository import SkillsRepository


def build_skills_router(settings: Settings) -> APIRouter:
    """
    Build and configure skills APIRouter instance.
    Endpoints:
      - GET /api/v1/skills
      - GET /api/v1/skills/{id}
      - POST /api/v1/skills/install
      - PATCH /api/v1/skills/{id}/toggle
      - DELETE /api/v1/skills/{id}
    """
    router = APIRouter(prefix="/skills", tags=["Skills"])

    @router.get(
        "",
        response_model=list[SkillResponse],
        status_code=status.HTTP_200_OK,
        summary="List Skills",
    )
    async def list_skills(
        enabled_only: bool = Query(default=False),
        service: SkillsRepository = Depends(get_skills_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> list[SkillResponse]:
        skills = await service.list_skills(enabled_only=enabled_only)
        return [SkillResponse.model_validate(s) for s in skills]

    @router.get(
        "/{skill_id}",
        response_model=SkillResponse,
        status_code=status.HTTP_200_OK,
        summary="Get Skill Details",
    )
    async def get_skill(
        skill_id: UUID,
        service: SkillsRepository = Depends(get_skills_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> SkillResponse:
        skill = await service.get_by_id(skill_id)
        if not skill:
            raise NotFoundError(f"Skill {skill_id} not found")
        return SkillResponse.model_validate(skill)

    @router.post(
        "/install",
        response_model=SkillResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Install Skill from Git",
    )
    async def install_skill(
        payload: SkillInstallRequest,
        service: SkillsRepository = Depends(get_skills_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> SkillResponse:
        skill = await service.install_from_git(payload.repo_url, payload.commit)  # type: ignore[attr-defined]
        return SkillResponse.model_validate(skill)

    @router.patch(
        "/{skill_id}/toggle",
        response_model=SkillResponse,
        status_code=status.HTTP_200_OK,
        summary="Enable or Disable Skill",
    )
    async def toggle_skill(
        skill_id: UUID,
        payload: SkillToggleRequest,
        service: SkillsRepository = Depends(get_skills_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> SkillResponse:
        updated = await service.update_status(skill_id, payload.enabled)
        if not updated:
            raise NotFoundError(f"Skill {skill_id} not found")
        return SkillResponse.model_validate(updated)

    @router.delete(
        "/{skill_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Delete Skill",
    )
    async def delete_skill(
        skill_id: UUID,
        service: SkillsRepository = Depends(get_skills_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> None:
        await service.delete_skill(skill_id)

    return router
