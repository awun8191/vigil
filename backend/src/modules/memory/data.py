"""Pydantic v2 request/response wire schemas for the memory module."""
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class MemorySearchRequest(BaseModel):
    """Inbound query for long-term memory retrieval."""
    query: str = Field(..., min_length=1, max_length=2000, description="Natural language search text.")
    scopes: list[str] = Field(default=["global"], description="Memory scopes to search within.")
    top_n: int = Field(default=8, ge=1, le=50, description="Number of results to return.")


class MemorySearchResultItem(BaseModel):
    """Outbound representation of a retrieved memory chunk with decay weighting."""
    id: UUID = Field(..., description="Memory chunk UUID.")
    content: str = Field(..., description="Verbatim conversation chunk content.")
    summary: str | None = Field(default=None, description="Synthesized summary if present.")
    similarity: float = Field(..., description="Cosine similarity score.")
    age_days: float = Field(..., description="Chunk age in days.")
    final_score: float = Field(..., description="Decay-adjusted ranking score.")


class MemorySearchResponse(BaseModel):
    """Outbound collection of retrieved memory chunks."""
    results: list[MemorySearchResultItem] = Field(..., description="Ranked memory results.")
    query: str = Field(..., description="Echoed query.")
    path: str | None = Field(
        default=None,
        description="Retrieval path that answered the query: exact or hnsw.",
    )


class MemoryChunkResponse(BaseModel):
    """Wire representation of an individual memory chunk."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    scope: str
    content: str
    summary: str | None
    source_message_ids: list[UUID]
    embedding_model: str
    excluded: bool
    created_at: datetime


class MemoryChunkListResponse(BaseModel):
    """Paginated list of stored semantic memory chunks."""
    items: list[MemoryChunkResponse]
    total: int
    limit: int
    offset: int


class MemoryExcludeRequest(BaseModel):
    """Inbound request to exclude or unexclude a chunk from retrieval."""
    excluded: bool = Field(..., description="True to exclude from search; False to re-enable.")


class PersonalizationUsage(BaseModel):
    """Character usage for a persistent markdown store."""
    current: int = Field(..., ge=0, description="Current character count.")
    limit: int = Field(..., ge=1, description="Maximum allowed character count.")


class PersonalizationPresetItem(BaseModel):
    """Starter personality preset for SOUL.md."""
    id: str
    name: str
    description: str
    content: str


class PersonalizationResponse(BaseModel):
    """Outbound personalization and curated memory snapshot."""
    display_name: str = Field(..., description="Current user display name.")
    soul_md: str = Field(..., description="Raw contents of SOUL.md.")
    soul_usage: PersonalizationUsage
    user_md: str = Field(..., description="Serialized USER.md contents.")
    user_entries: list[str] = Field(default_factory=list, description="Discrete entries in USER.md.")
    user_usage: PersonalizationUsage
    memory_md: str = Field(..., description="Serialized MEMORY.md contents.")
    memory_entries: list[str] = Field(default_factory=list, description="Discrete entries in MEMORY.md.")
    memory_usage: PersonalizationUsage
    presets: list[PersonalizationPresetItem] = Field(default_factory=list, description="Available SOUL.md presets.")


class PersonalizationUpdateRequest(BaseModel):
    """Inbound partial update for SOUL.md, USER.md, MEMORY.md, or display_name."""
    display_name: str | None = Field(default=None, max_length=255)
    soul_md: str | None = Field(default=None, max_length=16000)
    reset_soul: bool = Field(default=False)
    user_md: str | None = Field(default=None, max_length=8000)
    user_entries: list[str] | None = Field(default=None)
    memory_md: str | None = Field(default=None, max_length=8000)
    memory_entries: list[str] | None = Field(default=None)
