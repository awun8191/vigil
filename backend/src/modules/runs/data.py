"""Pydantic v2 request/response wire schemas for the runs module."""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class RunCreateRequest(BaseModel):
    """Inbound request to trigger a new agent execution run."""
    config_id: UUID = Field(..., description="Target AgentConfig UUID.")
    card_id: UUID | None = Field(default=None, description="Optional initiating Card UUID.")
    initial_prompt: str | None = Field(default=None, description="Optional prompt override.")


class RunStepResponse(BaseModel):
    """Wire representation of an execution step event."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(..., description="Step UUID.")
    run_id: UUID = Field(..., description="Parent run UUID.")
    step_number: int = Field(..., description="Monotonically increasing step counter.")
    event_type: Literal["thought", "tool_call", "tool_result", "approval_required", "run_completed", "run_failed"] = Field(
        ..., description="Step event category."
    )
    payload: dict[str, Any] = Field(..., description="Event specific payload.")
    created_at: datetime = Field(..., description="Event emission timestamp.")


class RunResponse(BaseModel):
    """Wire representation of an agent execution run."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(..., description="Unique run UUID.")
    config_id: UUID = Field(..., description="Associated AgentConfig UUID.")
    card_id: UUID | None = Field(default=None, description="Triggering Card UUID.")
    parent_run_id: UUID | None = Field(default=None, description="Parent run if sub-agent run.")
    depth: int = Field(default=0, description="Nesting depth of the run.")
    status: Literal[
        "queued",
        "running",
        "awaiting_approval",
        "awaiting_background_job",
        "completed",
        "failed",
    ] = Field(..., description="Current execution state.")
    started_at: datetime = Field(..., description="Execution start timestamp.")
    ended_at: datetime | None = Field(default=None, description="Completion timestamp.")
    token_usage: dict[str, int] = Field(default_factory=dict, description="Token consumption metrics.")
    cost_usd: float = Field(default=0.0, description="Estimated total run cost in USD.")


class RunCancelResponse(BaseModel):
    """Confirmation of run cancellation."""
    run_id: UUID
    status: str
    cancelled_at: datetime
