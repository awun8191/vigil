"""Pydantic v2 request/response wire schemas for the audit module."""
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AuditLogResponse(BaseModel):
    """Outbound representation of one stored audit event."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor: str = Field(..., description="Who acted: user, system, or worker.")
    action: str = Field(..., description="Event name, for example approval_decision.")
    subject: str = Field(..., description="Identifier of the object that changed.")
    details: dict[str, Any] = Field(..., description="Redacted event payload.")
    created_at: datetime


class AuditPageResponse(BaseModel):
    """One cursor page of audit events, newest first."""

    items: list[AuditLogResponse]
    next_cursor: str | None = Field(
        default=None,
        description="Pass this value as the cursor of the next page. None at the end.",
    )
