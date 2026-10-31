"""Approvals repository protocol and data transfer objects."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID


@dataclass(frozen=True)
class ApprovalDTO:
    """Represents a gated approval request for external side-effect execution."""
    id: UUID
    run_id: UUID
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    status: str  # pending, approved, rejected, stale
    decided_at: datetime | None
    decision_note: str | None
    created_at: datetime


@dataclass(frozen=True)
class ApprovalCreateDTO:
    """Payload for registering a pending tool approval."""
    run_id: UUID
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    status: str = "pending"


@runtime_checkable
class ApprovalsRepository(Protocol):
    """Protocol contract for human-in-the-loop tool approvals."""

    async def get_approval_by_id(self, approval_id: UUID) -> ApprovalDTO | None:
        """
        Retrieve an approval record by its primary key.

        Args:
            approval_id: UUID of the approval.

        Returns:
            ApprovalDTO if found, None otherwise.
        """
        ...

    async def list_pending_approvals(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ApprovalDTO]:
        """
        List all approvals currently awaiting user decision.

        Args:
            limit: Maximum items to return.
            offset: Pagination offset.

        Returns:
            List of pending ApprovalDTOs ordered by creation time ascending.
        """
        ...

    async def list_approvals_by_run(self, run_id: UUID) -> list[ApprovalDTO]:
        """
        List all approvals associated with a specific run.

        Args:
            run_id: UUID of the run.

        Returns:
            List of ApprovalDTOs.
        """
        ...

    async def create_approval(self, data: ApprovalCreateDTO) -> ApprovalDTO:
        """
        Persist a new pending approval record.

        Args:
            data: Approval creation DTO.

        Returns:
            The created ApprovalDTO.
        """
        ...

    async def update_decision(
        self,
        approval_id: UUID,
        status: str,
        decided_at: datetime,
        decision_note: str | None = None,
    ) -> ApprovalDTO | None:
        """
        Update an approval with the user's decision.

        Args:
            approval_id: UUID of the approval.
            status: Decision status ('approved' or 'rejected').
            decided_at: Timestamp of decision.
            decision_note: Optional user-provided comment or rationale.

        Returns:
            Updated ApprovalDTO, or None if approval not found.
        """
        ...

    async def get_by_run_and_tool_call(
        self,
        run_id: UUID,
        tool_call_id: str,
    ) -> ApprovalDTO | None:
        """
        Find an approval entry by run ID and specific tool call ID.

        Args:
            run_id: UUID of the run.
            tool_call_id: LLM tool call identifier.

        Returns:
            ApprovalDTO if found, None otherwise.
        """
        ...
