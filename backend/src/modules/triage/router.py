"""Triage router factory providing proactive triage endpoints."""
from uuid import UUID
from fastapi import APIRouter, Depends, status

from src.config.exceptions import NotFoundError
from src.config.settings import Settings
from src.dependencies import get_current_device, get_triage_service
from src.modules.auth.repository import DeviceDTO
from src.modules.triage.data import TriageItemRequest, TriageResultResponse
from src.modules.triage.repository import TriageRepository


def build_triage_router(settings: Settings) -> APIRouter:
    """
    Build and configure triage APIRouter instance.
    Endpoints:
      - POST /api/v1/triage/items/{item_id}
      - GET /api/v1/triage/items/{item_id}
      - GET /api/v1/triage/recent
    """
    router = APIRouter(prefix="/triage", tags=["Triage"])

    @router.post(
        "/items/{item_id}",
        response_model=TriageResultResponse,
        status_code=status.HTTP_200_OK,
        summary="Trigger Proactive Triage",
    )
    async def run_triage(
        item_id: UUID,
        service: TriageRepository = Depends(get_triage_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> TriageResultResponse:
        result = await service.triage_item(item_id)  # type: ignore[attr-defined]
        return TriageResultResponse.model_validate(result)

    @router.get(
        "/items/{item_id}",
        response_model=TriageResultResponse,
        status_code=status.HTTP_200_OK,
        summary="Get Triage Result",
    )
    async def get_triage(
        item_id: UUID,
        service: TriageRepository = Depends(get_triage_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> TriageResultResponse:
        result = await service.get_triage_result_by_item(item_id)
        if not result:
            raise NotFoundError(f"Triage result for item {item_id} not found")
        return TriageResultResponse.model_validate(result)

    @router.get(
        "/recent",
        response_model=list[TriageResultResponse],
        status_code=status.HTTP_200_OK,
        summary="List Recent Triage Results",
    )
    async def list_recent(
        service: TriageRepository = Depends(get_triage_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> list[TriageResultResponse]:
        results = await service.list_recent_triage(limit=50)
        return [TriageResultResponse.model_validate(r) for r in results]

    return router
