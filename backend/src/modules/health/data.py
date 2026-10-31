"""Pydantic v2 request/response wire schemas for the health module."""
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class HealthStatusResponse(BaseModel):
    """Wire response schema for system liveness and readiness probes."""
    model_config = ConfigDict(from_attributes=True)

    status: Literal["ok", "degraded", "unhealthy"] = Field(
        ...,
        description="Overall health status of the application.",
        examples=["ok"],
    )
    version: str = Field(
        ...,
        description="Current application release version.",
        examples=["0.1.0"],
    )
    database: Literal["connected", "disabled", "error"] = Field(
        ...,
        description="Status of PostgreSQL database connectivity.",
        examples=["connected"],
    )
    timestamp: datetime = Field(
        ...,
        description="UTC timestamp when the probe was evaluated.",
    )
    details: dict[str, Any] | None = Field(
        default=None,
        description="Optional diagnostic details for individual subsystems.",
    )
