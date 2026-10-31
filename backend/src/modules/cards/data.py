"""Pydantic v2 request/response wire schemas for the cards module."""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class CardActionSchema(BaseModel):
    """Schema representing an interactive action button on a card."""
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Unique action key (e.g. 'quick_reply').")
    label: str = Field(..., description="Display text for the action button.")
    action_type: str = Field(..., description="Action trigger category ('run', 'dismiss', 'reply').")
    payload: dict[str, Any] = Field(default_factory=dict, description="Payload dispatched upon trigger.")


class CardResponse(BaseModel):
    """Wire representation of an Action Card in the companion feed."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(..., description="Unique card UUID.")
    user_id: UUID = Field(..., description="Target user UUID.")
    item_id: UUID | None = Field(default=None, description="Linked connector item UUID.")
    thread_id: str | None = Field(default=None, description="Connector thread identifier for grouping.")
    type: str = Field(..., description="Card classification ('email', 'calendar', 'alert').")
    urgency: Literal["critical", "high", "medium", "low"] = Field(..., description="Card urgency level.")
    title: str = Field(..., description="Card headline summary.")
    summary: str | None = Field(default=None, description="Detailed executive briefing text.")
    due_at: datetime | None = Field(default=None, description="Deadline timestamp if applicable.")
    status: Literal["new", "seen", "in_progress", "awaiting_approval", "done", "dismissed", "expired"] = Field(
        ..., description="Current card lifecycle state."
    )
    actions: list[CardActionSchema] = Field(default_factory=list, description="Available action buttons.")
    feedback: str | None = Field(default=None, description="User feedback rating if recorded.")
    created_at: datetime = Field(..., description="Card creation UTC timestamp.")
    updated_at: datetime = Field(..., description="Card last update UTC timestamp.")


class CardListResponse(BaseModel):
    """Paginated collection of cards."""
    items: list[CardResponse] = Field(..., description="List of card entities.")
    total: int = Field(..., description="Total matching card count.")
    limit: int = Field(..., description="Page size limit.")
    offset: int = Field(..., description="Pagination offset.")


class CardCreateRequest(BaseModel):
    """Inbound request for creating or injecting a card."""
    type: str = Field(..., description="Card category.")
    urgency: Literal["critical", "high", "medium", "low"] = Field(default="medium")
    title: str = Field(..., min_length=1, max_length=250)
    summary: str | None = None
    thread_id: str | None = None
    due_at: datetime | None = None
    actions: list[CardActionSchema] = Field(default_factory=list)


class CardUpdateRequest(BaseModel):
    """Inbound payload for updating status or fields of a card."""
    status: Literal["new", "seen", "in_progress", "awaiting_approval", "done", "dismissed", "expired"] | None = None
    summary: str | None = None
    due_at: datetime | None = None


class CardFeedbackRequest(BaseModel):
    """Inbound submission of user feedback on a card."""
    feedback: Literal["useful", "not_useful", "dismissed", "spam"] = Field(
        ..., description="User feedback label."
    )
    note: str | None = Field(default=None, description="Optional user commentary.")
