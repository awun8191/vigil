"""Pydantic v2 request/response wire schemas for the compaction module."""
from typing import Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class CompactionDryRunRequest(BaseModel):
    """Inbound request to evaluate context compaction without persisting changes."""
    messages: list[dict[str, Any]] = Field(..., description="Raw message list.")
    max_tokens: int = Field(default=8000, description="Target context token ceiling.")


class CompactionDryRunResponse(BaseModel):
    """Outbound dry run metrics."""
    original_tokens: int
    compacted_tokens: int
    summary_block: str
    units_dropped: int
    units_stubbed: int


class CompactionRunRequest(BaseModel):
    """Inbound request to trigger compaction on a live run."""
    max_tokens: int | None = Field(default=None, description="Optional token window override.")


class CompactionRunResponse(BaseModel):
    """Outbound confirmation of live run compaction."""
    run_id: UUID
    original_tokens: int
    compacted_tokens: int
    summary_block: str
    units_dropped: int
    units_stubbed: int
