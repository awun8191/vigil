"""Pydantic v2 request/response wire schemas for the triage module."""
from datetime import datetime
from typing import Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class TriageItemRequest(BaseModel):
    """Inbound request to run proactive triage on an ingested item."""
    item_id: UUID = Field(..., description="UUID of the ingested connector item.")


class TriageResultResponse(BaseModel):
    """Outbound representation of triage decision gate and metadata extraction."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(..., description="Triage result UUID.")
    item_id: UUID = Field(..., description="Connector item UUID.")
    answers: dict[str, str] = Field(..., description="Jev categorical question answers.")
    probabilities: dict[str, float] = Field(..., description="Calibrated probabilities for actionability.")
    decision: str = Field(..., description="Triage disposition ('card_created', 'discarded', 'review_required').")
    model_version: str = Field(..., description="Evaluator model version string.")
    created_at: datetime = Field(..., description="Evaluation timestamp.")


class ExtractedMetadataDTO(BaseModel):
    """Extracted Stage 2 metadata for Action Card synthesis."""
    title: str = Field(..., description="One-sentence headline summary.")
    summary: str = Field(..., description="Concise executive briefing.")
    due_at: datetime | None = Field(default=None, description="Extracted ISO deadline.")
    urgency: str = Field(..., description="Calculated card urgency level.")
    actions: list[dict[str, Any]] = Field(default_factory=list, description="Synthesized action buttons.")
