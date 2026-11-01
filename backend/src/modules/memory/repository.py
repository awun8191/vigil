"""Memory repository protocol and data transfer objects."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID


@dataclass(frozen=True)
class MemoryChunkDTO:
    """Represents a vectorized chunk of conversational memory."""
    id: UUID
    user_id: UUID
    scope: str  # global, card, agent
    content: str
    summary: str | None
    source_message_ids: list[UUID]
    embedding_model: str
    excluded: bool
    created_at: datetime


@dataclass(frozen=True)
class MemoryChunkCreateDTO:
    """Payload for persisting a newly embedded memory chunk."""
    user_id: UUID
    scope: str
    content: str
    source_message_ids: list[UUID]
    embedding: list[float]
    embedding_model: str
    summary: str | None = None
    embedding_dim: int = 384


@dataclass(frozen=True)
class MemorySearchResultDTO:
    """Scored memory retrieval candidate incorporating semantic similarity and time-decay."""
    id: UUID
    content: str
    summary: str | None
    similarity: float
    age_days: float
    final_score: float


@runtime_checkable
class MemoryRepository(Protocol):
    """Protocol contract for vector memory persistence and time-decay search."""

    async def save_chunk(self, data: MemoryChunkCreateDTO) -> MemoryChunkDTO:
        """
        Persist a memory chunk and its vector embedding in PostgreSQL.

        Args:
            data: Memory chunk creation DTO.

        Returns:
            The persisted MemoryChunkDTO.
        """
        ...

    async def search_decay_weighted(
        self,
        user_id: UUID,
        query_embedding: list[float],
        scopes: list[str],
        candidate_k: int | None = None,
        top_n: int | None = None,
        half_life_days: float | None = None,
        floor: float | None = None,
        override_threshold: float | None = None,
    ) -> list[MemorySearchResultDTO]:
        """
        Execute vector similarity search with exponential time-decay scoring.

        Args:
            user_id: Owner user UUID.
            query_embedding: Vector embedding of search query (384-dim).
            scopes: Allowed scopes to filter (e.g. ['global', 'card']).
            candidate_k: Initial cosine similarity candidate set size. None uses settings.
            top_n: Final ranked results to return. None uses settings.
            half_life_days: Exponential decay half-life in days. None uses settings.
            floor: Minimum recency multiplier floor. None uses settings.
            override_threshold: Similarity score that bypasses decay. None uses the
                value from settings. This protocol holds no threshold constant.

        Returns:
            List of ranked MemorySearchResultDTOs.
        """
        ...

    async def get_chunk_by_id(self, chunk_id: UUID) -> MemoryChunkDTO | None:
        """
        Retrieve a chunk by its primary key.

        Args:
            chunk_id: UUID of the chunk.

        Returns:
            MemoryChunkDTO if found, None otherwise.
        """
        ...

    async def set_excluded(self, chunk_id: UUID, excluded: bool) -> bool:
        """
        Toggle the excluded flag on a chunk without dropping the row.

        Args:
            chunk_id: UUID of the chunk.
            excluded: Boolean state.

        Returns:
            True if updated, False if chunk not found.
        """
        ...

    async def delete_chunk(self, chunk_id: UUID) -> bool:
        """
        Permanently remove a chunk from memory.

        Args:
            chunk_id: UUID of the chunk to delete.

        Returns:
            True if deleted, False if not found.
        """
        ...
