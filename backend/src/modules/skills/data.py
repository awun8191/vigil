"""Pydantic v2 request/response wire schemas for the skills module."""
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class SkillResponse(BaseModel):
    """Outbound representation of an installed skill."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(..., description="Skill UUID.")
    name: str = Field(..., description="Unique skill identifier (e.g. 'linear-mcp').")
    description: str = Field(..., description="Capability description.")
    version: str = Field(..., description="Manifest version string (e.g. '1.0.0').")
    source: str = Field(..., description="Source origin ('builtin', 'git', 'upload').")
    commit: str | None = Field(default=None, description="Pinned git commit SHA.")
    enabled: bool = Field(default=True, description="Whether skill is active.")
    storage_path: str = Field(..., description="Filesystem directory where skill files reside.")
    created_at: datetime
    updated_at: datetime


class SkillInstallRequest(BaseModel):
    """Inbound request to install a skill from a Git repository.

    ``commit`` is required. A branch name or a tag moves, so it is not a pin.
    The service rejects any value that is not a hexadecimal commit SHA.
    """

    repo_url: str = Field(
        ..., min_length=1, description="Git clone URL over https."
    )
    commit: str = Field(
        ...,
        min_length=1,
        description="Commit SHA to pin. A branch name is rejected.",
    )


class SkillToggleRequest(BaseModel):
    """Inbound request to enable or disable a skill."""
    enabled: bool = Field(..., description="True to enable; False to disable.")
