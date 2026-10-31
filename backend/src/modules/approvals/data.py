"""Pydantic v2 request/response wire schemas for the approvals module."""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class ApprovalResponse(BaseModel):
    """Wire representation of a pending or decided human-in-the-loop tool approval."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(..., description="Approval UUID.")
    run_id: UUID = Field(..., description="Associated run UUID.")
    tool_call_id: str = Field(..., description="LLM tool call correlation ID.")
    tool_name: str = Field(..., description="External tool name (e.g. 'gmail_send').")
    arguments: dict[str, Any] = Field(..., description="Proposed tool invocation arguments.")
    status: Literal["pending", "approved", "rejected", "stale"] = Field(
        ..., description="Approval state."
    )
    decided_at: datetime | None = Field(default=None, description="Decision timestamp.")
    decision_note: str | None = Field(default=None, description="Optional explanation or reason.")


class ApprovalDecisionRequest(BaseModel):
    """Inbound decision submission from companion app."""
    decision: Literal["approved", "rejected"] = Field(..., description="Decision outcome.")
    decision_note: str | None = Field(default=None, description="Optional commentary.")


class ApprovalDecisionResponse(BaseModel):
    """Confirmation of processed approval decision."""
    approval_id: UUID
    status: str
    run_id: UUID
    resumed: bool = Field(default=True, description="Indicates run resumption was enqueued.")
