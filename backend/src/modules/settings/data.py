"""Pydantic v2 request/response wire schemas for the settings module."""
from datetime import datetime
from typing import Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class UserSettingsResponse(BaseModel):
    """Outbound representation of user preferences and caps."""
    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    spending_cap_usd_monthly: float = Field(..., description="Monthly LLM spend cap in USD.")
    polling_interval_seconds: int = Field(..., description="Connector background poll cadence.")
    approval_defaults: dict[str, str] = Field(..., description="Default approval policy mapping.")
    preferred_model_tier: str = Field(..., description="Preferred inference tier.")


class UserSettingsUpdateRequest(BaseModel):
    """Inbound request to update user preferences."""
    spending_cap_usd_monthly: float | None = Field(default=None, ge=0.0)
    polling_interval_seconds: int | None = Field(default=None, ge=30)
    approval_defaults: dict[str, str] | None = None
    preferred_model_tier: str | None = None


class AuditLogResponse(BaseModel):
    """Outbound representation of a security or operational audit entry."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor: str
    action: str
    subject: str
    details: dict[str, Any]
    created_at: datetime


class SystemLimitsResponse(BaseModel):
    """Outbound system-wide resource limits and timeouts."""
    max_steps_per_run: int = 40
    max_cost_usd_per_run: float = 0.50
    run_timeout_seconds: int = 900
    docker_memory_limit: str = "1024m"
