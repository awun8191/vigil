"""Pydantic v2 request/response wire schemas for the artifacts module."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class ArtifactCreateRequest(BaseModel):
    """Inbound request to register an artifact produced by a run."""
    run_id: UUID = Field(..., description="Originating run UUID.")
    kind: Literal["document", "diff", "log", "export"] = Field(..., description="Artifact category.")
    path: str = Field(..., description="Relative file path within the sandbox or workspace.")
    size_bytes: int = Field(..., ge=0, description="File size in bytes.")
    preview: str | None = Field(default=None, max_length=500, description="First 200-500 characters preview.")


class ArtifactResponse(BaseModel):
    """Outbound representation of an artifact."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(..., description="Artifact UUID.")
    run_id: UUID = Field(..., description="Run UUID.")
    kind: str = Field(..., description="Artifact category.")
    path: str = Field(..., description="File path.")
    size_bytes: int = Field(..., description="File size in bytes.")
    preview: str | None = Field(default=None, description="Text preview.")
    created_at: datetime = Field(..., description="Registration timestamp.")
