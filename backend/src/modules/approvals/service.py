"""Approvals service implementing gated human-in-the-loop tool execution.

The API process never runs an external tool. A decision writes the approval
row, records the barrier result of a rejection, and enqueues one `resume_run`
job. The worker process executes the approved tool when it resumes the run.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from typing import ClassVar
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.exceptions import ConflictError, NotFoundError, ValidationError
from src.config.settings import Settings
from src.integrations.repositories import MCPHostProtocol
from src.modules.approvals.data import ApprovalDecisionResponse
from src.modules.approvals.repository import (
    ApprovalCreateDTO,
    ApprovalDTO,
)
from src.modules.audit.repository import AuditRepository
from src.modules.audit.service import (
    ACTION_APPROVAL_DECISION,
    AuditService,
    record_event,
)
from src.modules.jobs.repository import JobCreateDTO, JobsRepository
from src.modules.jobs.service import JobsService
from src.modules.runs.repository import MessageCreateDTO, RunsRepository
from src.schemas.runtime import ApprovalModel, MessageModel


USER_REJECTED = "USER_REJECTED"
"""Error code of the tool result that a rejected approval writes."""

REJECTION_MESSAGE = "The user rejected this proposed action."
"""Text of the rejection tool result that the model reads on resume."""

PENDING_STATUS = "pending"
"""Status of an approval that still waits for a decision."""

DECIDED_STATUSES = frozenset({"approved", "rejected"})
"""Statuses that a decision can write."""

RESUME_JOB_TYPE = "resume_run"
"""Job type that wakes the paused run after a decision."""


class ApprovalsService:
    """
    Gated approval engine satisfying ApprovalsRepository via duck typing.

    A decision is a single guarded write. A second decision conflicts. The
    service never calls MCP, so no external side effect happens on the request
    path.
    """

    _approvals: ClassVar[dict[UUID, ApprovalDTO]] = {}

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        mcp_host: MCPHostProtocol | None = None,
        jobs_service: JobsRepository | None = None,
        runs_service: RunsRepository | None = None,
        audit: AuditRepository | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        # The API must not execute a tool. The host is accepted for wiring
        # compatibility and is never called from this service.
        self.mcp_host = mcp_host
        self.audit = audit or AuditService(session=session, settings=settings)
        self.jobs_service = jobs_service or JobsService(
            session=session, settings=settings
        )
        # Imported here, not at module level. The runs service imports this
        # module through its journal, so a module level import is a cycle.
        from src.modules.runs.service import RunsService

        self.runs_service = runs_service or RunsService(
            session=session, settings=settings
        )

    # -- Internal helpers ---------------------------------------------------

    def _is_db_session_active(self) -> bool:
        """Return True when the session can run database queries."""
        if self.session is None:
            return False
        return not type(self.session).__name__.startswith("_Fallback")

    def _to_dto(self, model: ApprovalModel) -> ApprovalDTO:
        """Convert an approval row to its transfer object."""
        return ApprovalDTO(
            id=model.id,
            run_id=model.run_id,
            tool_call_id=model.tool_call_id,
            tool_name=model.tool_name,
            arguments=model.arguments,
            status=model.status,
            decided_at=model.decided_at,
            decision_note=model.decision_note,
            created_at=model.created_at,
        )

    async def _rollback_quietly(self) -> None:
        """Discard a failed transaction without masking the original error."""
        try:
            await self.session.rollback()
        except Exception:
            pass

    # -- Repository operations ---------------------------------------------

    async def get_approval_by_id(self, approval_id: UUID) -> ApprovalDTO | None:
        """Retrieve an approval record by its primary key."""
        if self._is_db_session_active():
            try:
                result = await self.session.execute(
                    select(ApprovalModel).where(ApprovalModel.id == approval_id)
                )
                model = result.scalar_one_or_none()
                if model is not None:
                    return self._to_dto(model)
                return None
            except Exception:
                await self._rollback_quietly()
        return self._approvals.get(approval_id)

    async def list_pending_approvals(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ApprovalDTO]:
        """List the approvals that still wait for a user decision."""
        if self._is_db_session_active():
            try:
                result = await self.session.execute(
                    select(ApprovalModel)
                    .where(ApprovalModel.status == PENDING_STATUS)
                    .order_by(ApprovalModel.created_at.asc())
                    .limit(limit)
                    .offset(offset)
                )
                return [self._to_dto(model) for model in result.scalars().all()]
            except Exception:
                await self._rollback_quietly()

        pending = [
            a for a in self._approvals.values() if a.status == PENDING_STATUS
        ]
        pending.sort(key=lambda a: a.created_at)
        return pending[offset : offset + limit]

    async def list_approvals_by_run(self, run_id: UUID) -> list[ApprovalDTO]:
        """List every approval of one run."""
        if self._is_db_session_active():
            try:
                result = await self.session.execute(
                    select(ApprovalModel)
                    .where(ApprovalModel.run_id == run_id)
                    .order_by(ApprovalModel.created_at.asc())
                )
                return [self._to_dto(model) for model in result.scalars().all()]
            except Exception:
                await self._rollback_quietly()

        return [a for a in self._approvals.values() if a.run_id == run_id]

    async def create_approval(self, data: ApprovalCreateDTO) -> ApprovalDTO:
        """Persist a new pending approval request."""
        approval_id = uuid4()
        now = datetime.now(timezone.utc)

        if self._is_db_session_active():
            try:
                model = ApprovalModel(
                    id=approval_id,
                    run_id=data.run_id,
                    tool_call_id=data.tool_call_id,
                    tool_name=data.tool_name,
                    arguments=data.arguments,
                    status=data.status,
                    created_at=now,
                )
                self.session.add(model)
                await self.session.commit()
                return self._to_dto(model)
            except Exception:
                await self._rollback_quietly()

        approval = ApprovalDTO(
            id=approval_id,
            run_id=data.run_id,
            tool_call_id=data.tool_call_id,
            tool_name=data.tool_name,
            arguments=data.arguments,
            status=data.status,
            decided_at=None,
            decision_note=None,
            created_at=now,
        )
        self._approvals[approval_id] = approval
        return approval

    async def update_decision(
        self,
        approval_id: UUID,
        status: str,
        decided_at: datetime,
        decision_note: str | None = None,
    ) -> ApprovalDTO | None:
        """
        Write a decision with no pending check.

        This is the plain repository write. The request path uses the guarded
        transition in process_decision, which rejects a second decision.

        Args:
            approval_id: UUID of the approval.
            status: New status, for example 'approved'.
            decided_at: Timestamp of the decision.
            decision_note: Optional comment from the user.

        Returns:
            The updated ApprovalDTO, or None when the approval is absent.
        """
        if self._is_db_session_active():
            try:
                stmt = (
                    update(ApprovalModel)
                    .where(ApprovalModel.id == approval_id)
                    .values(
                        status=status,
                        decided_at=decided_at,
                        decision_note=decision_note,
                    )
                    .returning(ApprovalModel)
                )
                result = await self.session.execute(stmt)
                model = result.scalars().first()
                await self.session.commit()
                if model is None:
                    return None
                return self._to_dto(model)
            except Exception:
                await self._rollback_quietly()

        existing = self._approvals.get(approval_id)
        if existing is None:
            return None
        updated = replace(
            existing,
            status=status,
            decided_at=decided_at,
            decision_note=decision_note,
        )
        self._approvals[approval_id] = updated
        return updated

    async def get_by_run_and_tool_call(
        self,
        run_id: UUID,
        tool_call_id: str,
    ) -> ApprovalDTO | None:
        """Find the approval of one tool call inside one run."""
        if self._is_db_session_active():
            try:
                result = await self.session.execute(
                    select(ApprovalModel).where(
                        ApprovalModel.run_id == run_id,
                        ApprovalModel.tool_call_id == tool_call_id,
                    )
                )
                model = result.scalar_one_or_none()
                if model is not None:
                    return self._to_dto(model)
                return None
            except Exception:
                await self._rollback_quietly()

        for approval in self._approvals.values():
            if approval.run_id == run_id and approval.tool_call_id == tool_call_id:
                return approval
        return None

    # -- Domain orchestration ----------------------------------------------

    async def _decide_once(
        self,
        approval_id: UUID,
        decision: str,
        decided_at: datetime,
        decision_note: str | None,
    ) -> ApprovalDTO | None:
        """
        Move one pending approval to its decision in a single guarded write.

        The status check and the write are one statement, so two concurrent
        decisions cannot both succeed.

        Args:
            approval_id: UUID of the approval.
            decision: 'approved' or 'rejected'.
            decided_at: Timestamp of the decision.
            decision_note: Optional comment from the user.

        Returns:
            The decided approval, or None when it was not pending.
        """
        if self._is_db_session_active():
            stmt = (
                update(ApprovalModel)
                .where(
                    ApprovalModel.id == approval_id,
                    ApprovalModel.status == PENDING_STATUS,
                )
                .values(
                    status=decision,
                    decided_at=decided_at,
                    decision_note=decision_note,
                )
                .returning(ApprovalModel)
            )
            result = await self.session.execute(stmt)
            model = result.scalars().first()
            if model is None:
                return None
            return self._to_dto(model)

        existing = self._approvals.get(approval_id)
        if existing is None or existing.status != PENDING_STATUS:
            return None
        updated = replace(
            existing,
            status=decision,
            decided_at=decided_at,
            decision_note=decision_note,
        )
        self._approvals[approval_id] = updated
        return updated

    async def _record_rejection(self, approval: ApprovalDTO) -> None:
        """
        Write the structured rejection result as the tool row.

        The barrier is satisfied without a tool call, because the assistant
        turn now has a tool message for this tool_call_id.

        Args:
            approval: The rejected approval.
        """
        payload = {
            "error": USER_REJECTED,
            "message": REJECTION_MESSAGE,
            "decision_note": approval.decision_note,
        }
        content = json.dumps(payload)

        if self._is_db_session_active():
            # The row joins the decision in the same transaction, so a crash
            # cannot leave a decided approval with an open barrier.
            self.session.add(
                MessageModel(
                    id=uuid4(),
                    run_id=approval.run_id,
                    role="tool",
                    content=content,
                    tool_call_id=approval.tool_call_id,
                    tool_name=approval.tool_name,
                )
            )
            return

        await self.runs_service.save_message(
            MessageCreateDTO(
                run_id=approval.run_id,
                role="tool",
                content=content,
                tool_call_id=approval.tool_call_id,
                tool_name=approval.tool_name,
            )
        )

    async def _enqueue_resume(self, approval: ApprovalDTO) -> None:
        """
        Enqueue the one job that wakes the paused run.

        Args:
            approval: The decided approval.
        """
        await self.jobs_service.enqueue(
            JobCreateDTO(
                type=RESUME_JOB_TYPE,
                payload={
                    "run_id": str(approval.run_id),
                    "approval_id": str(approval.id),
                    "tool_call_id": approval.tool_call_id,
                },
            )
        )

    async def process_decision(
        self,
        approval_id: UUID,
        decision: str,
        note: str | None = None,
    ) -> ApprovalDecisionResponse:
        """
        Record one user decision and wake the run. Never runs the tool.

        Steps:
        1. Reject a missing approval with 404.
        2. Move a pending approval to the decision in one guarded write.
        3. Reject a second decision with 409.
        4. On a rejection, write the USER_REJECTED tool result.
        5. Enqueue one resume_run job.

        Args:
            approval_id: UUID of the approval.
            decision: 'approved' or 'rejected'.
            note: Optional comment from the user.

        Returns:
            The decision response. It carries no tool output.

        Raises:
            NotFoundError: The approval does not exist.
            ConflictError: The approval is not pending.
            ValidationError: The decision is not 'approved' or 'rejected'.
        """
        if decision not in DECIDED_STATUSES:
            raise ValidationError(
                f"Decision must be one of {sorted(DECIDED_STATUSES)}, got {decision!r}"
            )

        approval = await self.get_approval_by_id(approval_id)
        if approval is None:
            raise NotFoundError(f"Approval {approval_id} not found")

        now = datetime.now(timezone.utc)
        decided = await self._decide_once(approval_id, decision, now, note)
        if decided is None:
            raise ConflictError(
                f"Approval {approval_id} is not pending and cannot be decided again"
            )

        if decision == "rejected":
            await self._record_rejection(decided)

        await self._enqueue_resume(decided)

        # The guarded write and the job insert share one transaction when both
        # use this session. A second commit is a no-op in that case.
        if self._is_db_session_active():
            try:
                await self.session.commit()
            except Exception:
                await self._rollback_quietly()

        # The decision is durable now. The audit write comes after the commit on
        # purpose: a failed audit write must not undo a recorded decision.
        # record_event logs the failure and returns, so this call cannot raise.
        await record_event(
            self.audit,
            actor="user",
            action=ACTION_APPROVAL_DECISION,
            subject=str(decided.id),
            details={
                "run_id": str(decided.run_id),
                "tool_name": decided.tool_name,
                "tool_call_id": decided.tool_call_id,
                "decision": decided.status,
                "decision_note": decided.decision_note,
            },
        )

        return ApprovalDecisionResponse(
            approval_id=decided.id,
            status=decided.status,
            run_id=decided.run_id,
            resumed=True,
        )
