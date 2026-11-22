"""Pydantic v2 request/response wire schemas for scoped project workspaces."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreateRequest(BaseModel):
    """Inbound request to create a scoped project workspace."""

    name: str = Field(..., min_length=1, max_length=120, description="Project display name.")
    description: str | None = Field(
        default=None, max_length=2000, description="Optional short project summary."
    )
    instructions: str | None = Field(
        default=None,
        max_length=12000,
        description="Optional standing instructions injected when running inside this project.",
    )


class ProjectUpdateRequest(BaseModel):
    """Partial update payload for a project workspace."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    instructions: str | None = Field(default=None, max_length=12000)


class ProjectResponse(BaseModel):
    """Outbound wire representation of a project workspace."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(..., description="Project UUID.")
    user_id: UUID = Field(..., description="Owning user UUID.")
    name: str = Field(..., description="Project display name.")
    slug: str = Field(..., description="URL/directory-safe project slug.")
    description: str | None = Field(default=None, description="Optional project description.")
    workspace_path: str = Field(..., description="Host directory path for project files.")
    instructions: str | None = Field(default=None, description="Standing project instructions.")
    created_at: datetime = Field(..., description="Creation UTC timestamp.")
    updated_at: datetime = Field(..., description="Last update UTC timestamp.")


class ProjectFileResponse(BaseModel):
    """Metadata for one file inside a project workspace directory."""

    model_config = ConfigDict(from_attributes=True)

    path: str = Field(..., description="Relative file path inside the project workspace.")
    size_bytes: int = Field(..., ge=0, description="File size in bytes.")
    updated_at: datetime = Field(..., description="Last modified UTC timestamp.")


class ProjectDetailResponse(ProjectResponse):
    """Project workspace detail including discovered workspace files."""

    files: list[ProjectFileResponse] = Field(default_factory=list)
