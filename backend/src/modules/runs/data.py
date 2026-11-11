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
    session_id: UUID | None = Field(
        default=None,
        description=(
            "Optional chat session that owns the run. That session receives a memory index "
            "job when the run completes."
        ),
    )


class AgentConfigResponse(BaseModel):
    """Outbound agent configuration, without the system prompt text."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(..., description="Agent config UUID.")
    name: str = Field(..., description="Unique config name.")
    model: str = Field(..., description="Reasoning model name.")
    tools: list[str] = Field(default_factory=list, description="Enabled tool names.")
    skills: list[str] = Field(default_factory=list, description="Enabled skill names.")
    memory_scopes: list[str] = Field(default_factory=list, description="Memory scopes the run indexes.")
    limits: dict[str, Any] = Field(default_factory=dict, description="Config level limits.")


class RunMessageResponse(BaseModel):
    """Wire representation of one message row of a run's transcript."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(..., description="Message UUID.")
    run_id: UUID | None = Field(
        default=None, description="Owning run UUID when this row belongs to a run."
    )
    role: str = Field(..., description="user, assistant, system, or tool.")
    content: str | None = Field(
        default=None, description="Message text; tool rows carry their result here."
    )
    created_at: datetime = Field(..., description="Row creation timestamp.")


class RunStepResponse(BaseModel):
    """Wire representation of an execution step event."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(..., description="Step UUID.")
    run_id: UUID = Field(..., description="Parent run UUID.")
    step_number: int = Field(..., description="Monotonically increasing step counter.")
    event_type: Literal[
        "thought",
        "tool_call",
        "tool_result",
        "message_delta",
        "approval_required",
        "run_completed",
        "run_failed",
        "compaction",
    ] = Field(
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
    cost_micros: int = Field(
        default=0, description="Total run cost in integer micros. This is the source of truth."
    )
    cost_usd: float = Field(
        default=0.0, description="The same cost in major units, for display only."
    )


class RunCancelResponse(BaseModel):
    """Confirmation of run cancellation."""
    run_id: UUID
    status: str
    cancelled_at: datetime
