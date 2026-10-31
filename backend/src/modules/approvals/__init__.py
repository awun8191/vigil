"""Approvals domain module."""
from src.modules.approvals.data import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalResponse,
)
from src.modules.approvals.dependencies import get_approvals_service
from src.modules.approvals.repository import (
    ApprovalCreateDTO,
    ApprovalDTO,
    ApprovalsRepository,
)
from src.modules.approvals.router import build_approvals_router
from src.modules.approvals.service import ApprovalsService

__all__ = [
    "ApprovalCreateDTO",
    "ApprovalDTO",
    "ApprovalDecisionRequest",
    "ApprovalDecisionResponse",
    "ApprovalResponse",
    "ApprovalsRepository",
    "ApprovalsService",
    "build_approvals_router",
    "get_approvals_service",
]
