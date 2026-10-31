"""Approvals router factory providing human-in-the-loop approval endpoints."""
from uuid import UUID
from fastapi import APIRouter, Depends, status

from src.config.exceptions import NotFoundError
from src.config.settings import Settings
from src.dependencies import get_approvals_service, get_current_device
from src.modules.approvals.data import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalResponse,
)
from src.modules.approvals.repository import ApprovalsRepository
from src.modules.auth.repository import DeviceDTO


def build_approvals_router(settings: Settings) -> APIRouter:
    """
    Build and configure approvals APIRouter instance.
    Endpoints:
      - GET /api/v1/approvals
      - GET /api/v1/approvals/{id}
      - POST /api/v1/approvals/{id}/decision
    """
    router = APIRouter(prefix="/approvals", tags=["Approvals"])

    @router.get(
        "",
        response_model=list[ApprovalResponse],
        status_code=status.HTTP_200_OK,
        summary="List Pending Approvals",
    )
    async def list_pending(
        service: ApprovalsRepository = Depends(get_approvals_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> list[ApprovalResponse]:
        pending = await service.list_pending_approvals()
        return [ApprovalResponse.model_validate(p) for p in pending]

    @router.get(
        "/{approval_id}",
        response_model=ApprovalResponse,
        status_code=status.HTTP_200_OK,
        summary="Get Approval Details",
    )
    async def get_approval(
        approval_id: UUID,
        service: ApprovalsRepository = Depends(get_approvals_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> ApprovalResponse:
        approval = await service.get_approval_by_id(approval_id)
        if not approval:
            raise NotFoundError(f"Approval {approval_id} not found")
        return ApprovalResponse.model_validate(approval)

    @router.post(
        "/{approval_id}/decision",
        response_model=ApprovalDecisionResponse,
        status_code=status.HTTP_200_OK,
        summary="Submit Approval Decision",
    )
    async def submit_decision(
        approval_id: UUID,
        payload: ApprovalDecisionRequest,
        service: ApprovalsRepository = Depends(get_approvals_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> ApprovalDecisionResponse:
        return await service.process_decision(  # type: ignore[attr-defined]
            approval_id=approval_id,
            decision=payload.decision,
            note=payload.decision_note,
        )

    return router
