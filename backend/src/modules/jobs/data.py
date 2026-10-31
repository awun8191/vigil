"""Pydantic v2 request/response wire schemas for the jobs module."""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class EnqueueJobRequest(BaseModel):
    """Inbound request to enqueue an asynchronous background job."""
    type: str = Field(..., description="Job task name ('poll_connector', 'triage_item', 'resume_run').")
    payload: dict[str, Any] = Field(default_factory=dict, description="Job payload parameters.")
    run_at: datetime | None = Field(default=None, description="Optional scheduled execution timestamp.")
    max_attempts: int = Field(default=5, ge=1, le=20, description="Maximum retry limit.")


class JobResponse(BaseModel):
    """Outbound representation of a background queue job."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    payload: dict[str, Any]
    run_at: datetime
    attempts: int
    max_attempts: int
    status: Literal["pending", "locked", "completed", "failed"]
    locked_by: str | None
    locked_at: datetime | None
    last_error: str | None


class QueueStatsResponse(BaseModel):
    """Outbound metrics on background queue depth and state."""
    pending: int = Field(..., description="Count of pending jobs ready to execute.")
    locked: int = Field(..., description="Count of jobs currently claimed by workers.")
    completed: int = Field(..., description="Count of successfully executed jobs.")
    failed: int = Field(..., description="Count of dead-letter failed jobs.")
    total: int = Field(..., description="Total tracked jobs.")
