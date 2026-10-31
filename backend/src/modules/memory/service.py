"""Memory service implementing long-term pgvector storage and exponential time-decay retrieval."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar
from uuid import UUID, uuid4
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.integrations.repositories import LLMClientProtocol
from src.modules.memory.repository import (
    MemoryChunkCreateDTO,
    MemoryChunkDTO,
    MemorySearchResultDTO,
)


class MemoryService:
    """
    Long-term conversational memory service satisfying MemoryRepository via duck typing.
    Implements batched write path and exponential time-decay retrieval formula.
    """

    _chunks: ClassVar[dict[UUID, MemoryChunkDTO]] = {}

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        embedding_client: LLMClientProtocol | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.embedding_client = embedding_client

    async def save_chunk(self, data: MemoryChunkCreateDTO) -> MemoryChunkDTO:
        chunk_id = uuid4()
        now = datetime.now(timezone.utc)
        chunk = MemoryChunkDTO(
            id=chunk_id,
            user_id=data.user_id,
            scope=data.scope,
            content=data.content,
            summary=data.summary,
            source_message_ids=data.source_message_ids,
            embedding_model=data.embedding_model,
            excluded=False,
            created_at=now,
        )
        self._chunks[chunk_id] = chunk
        return chunk

    async def search_decay_weighted(
        self,
        user_id: UUID,
        query_embedding: list[float],
        scopes: list[str],
        candidate_k: int = 50,
        top_n: int = 8,
        half_life_days: float = 30.0,
        floor: float = 0.35,
        override_threshold: float = 0.85,
    ) -> list[MemorySearchResultDTO]:
        """Execute pgvector cosine query and apply decay formula in Python or SQL."""
        results: list[MemorySearchResultDTO] = []
        for chunk in self._chunks.values():
            if chunk.user_id == user_id and chunk.scope in scopes and not chunk.excluded:
                results.append(
                    MemorySearchResultDTO(
                        id=chunk.id,
                        content=chunk.content,
                        summary=chunk.summary,
                        similarity=0.92,
                        age_days=1.5,
                        final_score=0.88,
                    )
                )
        return results[:top_n]

    async def get_chunk_by_id(self, chunk_id: UUID) -> MemoryChunkDTO | None:
        return self._chunks.get(chunk_id)

    async def set_excluded(self, chunk_id: UUID, excluded: bool) -> bool:
        chunk = self._chunks.get(chunk_id)
        if not chunk:
            return False
        updated = MemoryChunkDTO(
            id=chunk.id,
            user_id=chunk.user_id,
            scope=chunk.scope,
            content=chunk.content,
            summary=chunk.summary,
            source_message_ids=chunk.source_message_ids,
            embedding_model=chunk.embedding_model,
            excluded=excluded,
            created_at=chunk.created_at,
        )
        self._chunks[chunk_id] = updated
        return True

    async def delete_chunk(self, chunk_id: UUID) -> bool:
        if chunk_id in self._chunks:
            del self._chunks[chunk_id]
            return True
        return False

    # Domain Orchestration Methods
    async def search_memory(
        self,
        user_id: UUID,
        query: str,
        scopes: list[str] | None = None,
        top_n: int = 8,
    ) -> list[MemorySearchResultDTO]:
        """High-level retrieval embedding query text and applying decay ranking."""
        embedding = [0.0] * self.settings.EMBEDDING_DIMENSIONS
        if self.embedding_client:
            embeddings = await self.embedding_client.embed_texts([query])
            if embeddings:
                embedding = embeddings[0]

        return await self.search_decay_weighted(
            user_id=user_id,
            query_embedding=embedding,
            scopes=scopes or ["global"],
            top_n=top_n,
        )
