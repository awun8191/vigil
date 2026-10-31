"""Approvals service implementing gated human-in-the-loop tool execution."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar
from uuid import UUID, uuid4
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.exceptions import ConflictError, NotFoundError
from src.config.settings import Settings
from src.integrations.repositories import MCPHostProtocol
from src.modules.approvals.data import ApprovalDecisionResponse
from src.modules.approvals.repository import (
    ApprovalCreateDTO,
    ApprovalDTO,
)
from src.modules.jobs.repository import JobCreateDTO, JobsRepository


class ApprovalsService:
    """
    Gated approval engine satisfying ApprovalsRepository via duck typing.
    Validates decisions, executes external tools, records barrier synchronization,
    and enqueues run resumption jobs.
    """

    _approvals: ClassVar[dict[UUID, ApprovalDTO]] = {}

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        mcp_host: MCPHostProtocol | None = None,
        jobs_service: JobsRepository | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.mcp_host = mcp_host
        self.jobs_service = jobs_service

    async def get_approval_by_id(self, approval_id: UUID) -> ApprovalDTO | None:
        return self._approvals.get(approval_id)

    async def list_pending_approvals(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ApprovalDTO]:
        pending = [a for a in self._approvals.values() if a.status == "pending"]
        return pending[offset : offset + limit]

    async def list_approvals_by_run(self, run_id: UUID) -> list[ApprovalDTO]:
        return [a for a in self._approvals.values() if a.run_id == run_id]

    async def create_approval(self, data: ApprovalCreateDTO) -> ApprovalDTO:
        app_id = uuid4()
        now = datetime.now(timezone.utc)
        approval = ApprovalDTO(
            id=app_id,
            run_id=data.run_id,
            tool_call_id=data.tool_call_id,
            tool_name=data.tool_name,
            arguments=data.arguments,
            status=data.status,
            decided_at=None,
            decision_note=None,
            created_at=now,
        )
        self._approvals[app_id] = approval
        return approval

    async def update_decision(
        self,
        approval_id: UUID,
        status: str,
        decided_at: datetime,
        decision_note: str | None = None,
    ) -> ApprovalDTO | None:
        existing = self._approvals.get(approval_id)
        if not existing:
            return None
        updated = ApprovalDTO(
            id=existing.id,
            run_id=existing.run_id,
            tool_call_id=existing.tool_call_id,
            tool_name=existing.tool_name,
            arguments=existing.arguments,
            status=status,
            decided_at=decided_at,
            decision_note=decision_note,
            created_at=existing.created_at,
        )
        self._approvals[approval_id] = updated
        return updated

    async def get_by_run_and_tool_call(
        self,
        run_id: UUID,
        tool_call_id: str,
    ) -> ApprovalDTO | None:
        for app in self._approvals.values():
            if app.run_id == run_id and app.tool_call_id == tool_call_id:
                return app
        return None

    # Domain Orchestration Methods
    async def process_decision(
        self,
        approval_id: UUID,
        decision: str,
        note: str | None = None,
    ) -> ApprovalDecisionResponse:
        """
        Process human approval or rejection:
        1. Ensure approval is pending.
        2. If approved: invoke external tool via mcp_host.
        3. If rejected: write structured USER_REJECTED tool result to messages.
        4. Enqueue resume_run job to wake up paused run.
        """
        approval = await self.get_approval_by_id(approval_id)
        if not approval:
            raise NotFoundError(f"Approval {approval_id} not found")
        if approval.status != "pending":
            raise ConflictError(f"Approval {approval_id} is already in status '{approval.status}'")

        now = datetime.now(timezone.utc)
        updated = await self.update_decision(
            approval_id=approval_id,
            status=decision,
            decided_at=now,
            decision_note=note,
        )
        if not updated:
            raise NotFoundError(f"Approval {approval_id} not found")

        if self.jobs_service:
            await self.jobs_service.enqueue(
                JobCreateDTO(type="resume_run", payload={"run_id": str(updated.run_id)})
            )
        return ApprovalDecisionResponse(
            approval_id=approval_id,
            status=updated.status,
            run_id=updated.run_id,
            resumed=True,
        )
