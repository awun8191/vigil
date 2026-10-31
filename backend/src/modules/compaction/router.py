"""Compaction router factory providing context compaction and dry run endpoints."""
from uuid import UUID
from fastapi import APIRouter, Depends, status

from src.config.settings import Settings
from src.dependencies import get_compaction_service, get_current_device
from src.modules.auth.repository import DeviceDTO
from src.modules.compaction.data import (
    CompactionDryRunRequest,
    CompactionDryRunResponse,
    CompactionRunRequest,
    CompactionRunResponse,
)
from src.modules.compaction.repository import CompactionRepository


def build_compaction_router(settings: Settings) -> APIRouter:
    """
    Build and configure compaction APIRouter instance.
    Endpoints:
      - POST /api/v1/compaction/dry-run
      - POST /api/v1/compaction/run/{run_id}
    """
    router = APIRouter(prefix="/compaction", tags=["Compaction"])

    @router.post(
        "/dry-run",
        response_model=CompactionDryRunResponse,
        status_code=status.HTTP_200_OK,
        summary="Evaluate Context Compaction Dry Run",
    )
    async def dry_run(
        payload: CompactionDryRunRequest,
        service: CompactionRepository = Depends(get_compaction_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> CompactionDryRunResponse:
        return await service.dry_run(payload.messages, payload.max_tokens)  # type: ignore[attr-defined]

    @router.post(
        "/run/{run_id}",
        response_model=CompactionRunResponse,
        status_code=status.HTTP_200_OK,
        summary="Trigger Live Run Compaction",
    )
    async def compact_run(
        run_id: UUID,
        payload: CompactionRunRequest,
        service: CompactionRepository = Depends(get_compaction_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> CompactionRunResponse:
        target_tokens = payload.max_tokens or 8000
        result = await service.check_and_compact(run_id, target_tokens)  # type: ignore[attr-defined]
        return CompactionRunResponse(
            run_id=run_id,
            original_tokens=result.original_tokens if result else 0,
            compacted_tokens=result.compacted_tokens if result else 0,
            summary_block=result.summary_block if result else "",
            units_dropped=result.units_dropped if result else 0,
            units_stubbed=result.units_stubbed if result else 0,
        )

    return router
