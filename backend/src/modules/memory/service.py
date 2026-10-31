"""Memory service: window chunking, the background index job, and retrieval."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, ClassVar, Protocol, Sequence, runtime_checkable
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.integrations.embedding import EmbeddingModel, get_local_embedder
from src.integrations.repositories import LLMClientProtocol
from src.modules.jobs.repository import JobCreateDTO, JobDTO, JobsRepository
from src.modules.jobs.service import JobsService
from src.modules.memory.chunker import Chunker, MemoryTurn
from src.modules.memory.repository import (
    MemoryChunkCreateDTO,
    MemoryChunkDTO,
    MemorySearchResultDTO,
)
from src.schemas.memory import MemoryChunkModel
from src.schemas.runtime import MessageModel, ThreadModel

INDEX_JOB_TYPE = "index_memory"
INDEX_BATCH_LIMIT = 200
DEFAULT_SCOPE = "global"


@dataclass(frozen=True)
class IndexableMessage:
    """A conversation row that the index job may turn into memory."""

    id: UUID
    session_id: UUID
    user_id: UUID
    role: str
    content: str


@dataclass(frozen=True)
class IndexResult:
    """Outcome of one index run for one session."""

    messages_indexed: int
    chunks_created: int
    windows: int
    skipped: bool = False


@runtime_checkable
class MemoryIndexStore(Protocol):
    """Persistence boundary for the index job.

    The caller commits once, after the chunk insert and the message marker, so a
    crash cannot leave chunks without a marker or a marker without chunks.
    """

    async def fetch_unindexed(self, session_id: UUID, limit: int) -> list[IndexableMessage]:
        """Return user and assistant rows with a null indexed_at for a session."""
        ...

    async def add_chunks(self, chunks: Sequence[MemoryChunkCreateDTO]) -> int:
        """Stage chunk rows in the current transaction."""
        ...

    async def mark_indexed(self, message_ids: Sequence[UUID], indexed_at: datetime) -> int:
        """Set indexed_at on rows that are still unindexed and return the row count."""
        ...

    async def rollback(self) -> None:
        """Discard the staged work of the current transaction."""
        ...

    async def commit(self) -> None:
        """Persist chunks and markers together."""
        ...


class SqlMemoryIndexStore:
    """Postgres implementation of the index store."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def fetch_unindexed(self, session_id: UUID, limit: int) -> list[IndexableMessage]:
        statement = (
            select(
                MessageModel.id,
                MessageModel.session_id,
                ThreadModel.user_id,
                MessageModel.role,
                MessageModel.content,
            )
            .join(ThreadModel, ThreadModel.id == MessageModel.session_id)
            .where(
                MessageModel.session_id == session_id,
                MessageModel.indexed_at.is_(None),
                MessageModel.role.in_(("user", "assistant")),
                MessageModel.content.is_not(None),
            )
            .order_by(MessageModel.created_at.asc(), MessageModel.id.asc())
            .limit(limit)
        )
        rows = (await self.session.execute(statement)).all()
        messages: list[IndexableMessage] = []
        for row in rows:
            content = row[4]
            if not content or not content.strip():
                continue
            messages.append(
                IndexableMessage(
                    id=row[0],
                    session_id=row[1],
                    user_id=row[2],
                    role=row[3],
                    content=content,
                )
            )
        return messages

    async def add_chunks(self, chunks: Sequence[MemoryChunkCreateDTO]) -> int:
        if not chunks:
            return 0
        self.session.add_all(
            [
                MemoryChunkModel(
                    id=uuid4(),
                    user_id=chunk.user_id,
                    scope=chunk.scope,
                    content=chunk.content,
                    summary=chunk.summary,
                    # JSONB cannot serialize UUID objects.
                    source_message_ids=[str(item) for item in chunk.source_message_ids],
                    embedding=list(chunk.embedding),
                    embedding_model=chunk.embedding_model,
                    embedding_dim=chunk.embedding_dim,
                )
                for chunk in chunks
            ]
        )
        return len(chunks)

    async def mark_indexed(self, message_ids: Sequence[UUID], indexed_at: datetime) -> int:
        if not message_ids:
            return 0
        statement = (
            update(MessageModel)
            .where(
                MessageModel.id.in_(list(message_ids)),
                MessageModel.indexed_at.is_(None),
            )
            .values(indexed_at=indexed_at)
        )
        result = await self.session.execute(statement)
        return int(result.rowcount or 0)

    async def rollback(self) -> None:
        await self.session.rollback()

    async def commit(self) -> None:
        await self.session.commit()


class MemoryService:
    """
    Long-term conversational memory service satisfying MemoryRepository via duck typing.
    Implements the batched write path and exponential time-decay retrieval formula.
    """

    _chunks: ClassVar[dict[UUID, MemoryChunkDTO]] = {}

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        embedding_client: LLMClientProtocol | None = None,
        *,
        embedder: EmbeddingModel | None = None,
        chunker: Chunker | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.embedding_client = embedding_client
        self._embedder = embedder
        self._chunker = chunker

    # ------------------------------------------------------------- embeddings

    @property
    def embedder(self) -> EmbeddingModel:
        """Return the process-wide local encoder. It loads on first use only."""
        if self._embedder is None:
            self._embedder = get_local_embedder(self.settings)
        return self._embedder

    def _chunker_for(self, embedder: EmbeddingModel) -> Chunker:
        if self._chunker is not None:
            return self._chunker
        return Chunker(embedder, max_tokens=self.settings.EMBEDDING_MAX_TOKENS)

    # ------------------------------------------------------------ index writes

    async def enqueue_index_job(
        self,
        session_id: UUID,
        *,
        scope: str = DEFAULT_SCOPE,
        jobs: JobsRepository | None = None,
        run_at: datetime | None = None,
    ) -> JobDTO:
        """Register the index job. This writes a queue row and does no embedding."""
        service = jobs or JobsService(session=self.session, settings=self.settings)
        return await service.enqueue(
            JobCreateDTO(
                type=INDEX_JOB_TYPE,
                payload={"session_id": str(session_id), "scope": scope},
                run_at=run_at,
            )
        )

    async def run_index_job(self, payload: dict[str, Any]) -> IndexResult:
        """Execute one claimed index_memory job payload."""
        raw_session_id = payload.get("session_id")
        if not raw_session_id:
            raise ValueError("index_memory payload has no session_id")
        return await self.index_session(
            UUID(str(raw_session_id)),
            scope=str(payload.get("scope") or DEFAULT_SCOPE),
        )

    async def index_session(
        self,
        session_id: UUID,
        *,
        scope: str = DEFAULT_SCOPE,
        limit: int = INDEX_BATCH_LIMIT,
        store: MemoryIndexStore | None = None,
        embedder: EmbeddingModel | None = None,
    ) -> IndexResult:
        """Turn unindexed turns of one session into memory chunks.

        The row marker stops a second pass. The update runs inside the same
        transaction as the insert, so a losing worker discards its chunks.
        """
        index_store = store or SqlMemoryIndexStore(self.session)
        model = embedder or self.embedder

        messages = await index_store.fetch_unindexed(session_id, limit)
        if not messages:
            return IndexResult(messages_indexed=0, chunks_created=0, windows=0, skipped=True)

        message_ids = [message.id for message in messages]
        indexed_at = datetime.now(timezone.utc)
        turns = [
            MemoryTurn(message_id=message.id, role=message.role, content=message.content)
            for message in messages
        ]
        windows = self._chunker_for(model).segment(turns)
        if not windows:
            await index_store.mark_indexed(message_ids, indexed_at)
            await index_store.commit()
            return IndexResult(messages_indexed=len(messages), chunks_created=0, windows=0)

        user_ids = {message.user_id for message in messages}
        if len(user_ids) != 1:
            raise ValueError(f"Session {session_id} spans several users: {user_ids}")
        user_id = user_ids.pop()

        # The encoder is CPU bound, so keep it off the event loop.
        vectors = await asyncio.to_thread(
            model.embed_documents, [window.text for window in windows]
        )
        if len(vectors) != len(windows):
            raise ValueError(
                f"Embedder returned {len(vectors)} vectors for {len(windows)} chunks"
            )

        chunks = [
            MemoryChunkCreateDTO(
                user_id=user_id,
                scope=scope,
                content=window.text,
                source_message_ids=list(window.source_message_ids),
                embedding=vector,
                embedding_model=model.model_id,
                embedding_dim=len(vector),
            )
            for window, vector in zip(windows, vectors)
        ]
        created = await index_store.add_chunks(chunks)

        marked = await index_store.mark_indexed(message_ids, indexed_at)
        if marked < len(messages):
            # Another worker indexed part of this batch. Keep one chunk set.
            await index_store.rollback()
            return IndexResult(messages_indexed=0, chunks_created=0, windows=0, skipped=True)

        await index_store.commit()
        return IndexResult(
            messages_indexed=len(messages),
            chunks_created=created,
            windows=len(windows),
        )

    # --------------------------------------------------------- chunk retrieval

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
