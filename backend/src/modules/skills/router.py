"""Skills router factory: registry listing, pinned Git installs, and status changes.

An install requires a pinned commit SHA. Enable and disable change the
``enabled`` flag. Neither endpoint deletes a row.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from src.config.exceptions import NotFoundError
from src.config.settings import Settings
from src.dependencies import get_current_device, get_skills_service
from src.modules.auth.repository import DeviceDTO
from src.modules.skills.data import SkillInstallRequest, SkillResponse
from src.modules.skills.service import SkillsService


def build_skills_router(settings: Settings) -> APIRouter:
    """Build and configure the skills APIRouter.

    Endpoints:
      - GET  /api/v1/skills
      - GET  /api/v1/skills/{skill_id}
      - POST /api/v1/skills/install
      - POST /api/v1/skills/{skill_id}/enable
      - POST /api/v1/skills/{skill_id}/disable
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
        service: SkillsService = Depends(get_skills_service),
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
        service: SkillsService = Depends(get_skills_service),
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
        service: SkillsService = Depends(get_skills_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> SkillResponse:
        skill = await service.install_from_git(payload.repo_url, payload.commit)
        return SkillResponse.model_validate(skill)

    @router.post(
        "/{skill_id}/enable",
        response_model=SkillResponse,
        status_code=status.HTTP_200_OK,
        summary="Enable Skill",
    )
    async def enable_skill(
        skill_id: UUID,
        service: SkillsService = Depends(get_skills_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> SkillResponse:
        updated = await service.update_status(skill_id, True)
        if not updated:
            raise NotFoundError(f"Skill {skill_id} not found")
        return SkillResponse.model_validate(updated)

    @router.post(
        "/{skill_id}/disable",
        response_model=SkillResponse,
        status_code=status.HTTP_200_OK,
        summary="Disable Skill",
    )
    async def disable_skill(
        skill_id: UUID,
        service: SkillsService = Depends(get_skills_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> SkillResponse:
        updated = await service.update_status(skill_id, False)
        if not updated:
            raise NotFoundError(f"Skill {skill_id} not found")
        return SkillResponse.model_validate(updated)

    return router
