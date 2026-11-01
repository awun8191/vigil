"""Memory service: window chunking, the background index job, and retrieval."""
from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Protocol, Sequence, runtime_checkable
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, update
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
from src.modules.memory.retrieval import (
    SECONDS_PER_DAY,
    RetrievalCandidate,
    RetrievalPolicy,
    candidate_statement,
    hnsw_statements,
    rank_candidates,
)
from src.schemas.memory import MemoryChunkModel
from src.schemas.runtime import MessageModel, ThreadModel
from src.schemas.system import JobModel

INDEX_JOB_TYPE = "index_memory"
INDEX_BATCH_LIMIT = 200
DEFAULT_SCOPE = "global"

# Idle timeout producer. A session with no new turn for this window gets one
# index job, so a chat that goes quiet still reaches memory.
IDLE_INDEX_SECONDS = 900
IDLE_SWEEP_LIMIT = 50


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


@runtime_checkable
class IdleSessionStore(Protocol):
    """Store that finds the sessions that the idle timeout producer must index."""

    async def list_idle_sessions(self, idle_seconds: int, limit: int) -> list[UUID]:
        """Return sessions with unindexed turns and no turn inside the window."""
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


class SqlIdleSessionStore:
    """Postgres reader that finds the sessions of the idle timeout producer."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_idle_sessions(self, idle_seconds: int, limit: int) -> list[UUID]:
        """Return sessions with unindexed turns and no turn inside the window."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max(1, idle_seconds))
        statement = (
            select(MessageModel.session_id)
            .join(ThreadModel, ThreadModel.id == MessageModel.session_id)
            .where(
                MessageModel.indexed_at.is_(None),
                MessageModel.role.in_(("user", "assistant")),
                MessageModel.content.is_not(None),
            )
            .group_by(MessageModel.session_id)
            .having(func.max(MessageModel.created_at) <= cutoff)
            .order_by(MessageModel.session_id)
            .limit(limit)
        )
        rows = (await self.session.execute(statement)).all()
        return [row[0] for row in rows]


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

    async def enqueue_idle_session_index_jobs(
        self,
        *,
        idle_seconds: int = IDLE_INDEX_SECONDS,
        limit: int = IDLE_SWEEP_LIMIT,
        store: IdleSessionStore | None = None,
        jobs: JobsRepository | None = None,
    ) -> list[JobDTO]:
        """Enqueue one index job for each session that went idle.

        This is the idle timeout producer. It writes queue rows and embeds
        nothing. A session that already holds an open index job is skipped, so
        the sweep can run again without a second job.
        """
        selected = store
        if selected is None:
            if self.session is None:
                return []
            selected = SqlIdleSessionStore(self.session)
        enqueued: list[JobDTO] = []
        for session_id in await selected.list_idle_sessions(idle_seconds, limit):
            if await self._has_open_index_job(session_id, jobs=jobs):
                continue
            enqueued.append(await self.enqueue_index_job(session_id, jobs=jobs))
        return enqueued

    async def _has_open_index_job(
        self, session_id: UUID, jobs: JobsRepository | None = None
    ) -> bool:
        """True when the queue already holds an unfinished index job for a session."""
        if jobs is not None or self.session is None:
            return False
        statement = (
            select(JobModel.id)
            .where(
                JobModel.type == INDEX_JOB_TYPE,
                JobModel.status.in_(("pending", "locked")),
                JobModel.payload.op("->>")("session_id") == str(session_id),
            )
            .limit(1)
        )
        return (await self.session.execute(statement)).first() is not None

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

    @property
    def _session_bound(self) -> bool:
        """True when the service talks to Postgres. The in-memory store is test only."""
        return self.session is not None

    async def save_chunk(self, data: MemoryChunkCreateDTO) -> MemoryChunkDTO:
        """Persist one chunk. The database path is the production path."""
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
        if self._session_bound:
            self.session.add(
                MemoryChunkModel(
                    id=chunk_id,
                    user_id=data.user_id,
                    scope=data.scope,
                    content=data.content,
                    summary=data.summary,
                    # JSONB cannot serialize UUID objects.
                    source_message_ids=[str(item) for item in data.source_message_ids],
                    embedding=list(data.embedding),
                    embedding_model=data.embedding_model,
                    embedding_dim=data.embedding_dim,
                    excluded=False,
                    created_at=now,
                )
            )
            await self.session.flush()
        else:
            _IN_MEMORY_EMBEDDINGS[chunk_id] = list(data.embedding)
            self._chunks[chunk_id] = chunk
        return chunk

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
        """Search with the time decay rules.

        The argument defaults come from settings, not from constants in this file.
        The deployed override threshold is the measured value in settings, and
        this module holds no provisional copy of it.
        """
        explicit: dict[str, Any] = {}
        if candidate_k is not None:
            explicit["candidate_k"] = candidate_k
        if half_life_days is not None:
            explicit["half_life_days"] = half_life_days
        if floor is not None:
            explicit["floor"] = floor
        if override_threshold is not None:
            explicit["override_threshold"] = override_threshold
        policy = replace(RetrievalPolicy.from_settings(self.settings), **explicit)
        return await self.search_embedding(
            user_id,
            query_embedding,
            scopes=scopes,
            policy=policy.with_top_n(top_n),
        )

    async def search_embedding(
        self,
        user_id: UUID,
        query_embedding: Sequence[float],
        *,
        scopes: Sequence[str] | None = None,
        policy: RetrievalPolicy | None = None,
    ) -> list[MemorySearchResultDTO]:
        """Run the approximate scan, then apply the cut, the decay, and the order."""
        active_policy = policy or RetrievalPolicy.from_settings(self.settings)
        allowed_scopes = list(scopes or [DEFAULT_SCOPE])
        active_model = self.settings.EMBEDDING_MODEL

        if not self._session_bound:
            candidates = self._in_memory_candidates(
                user_id, query_embedding, allowed_scopes
            )
            return rank_candidates(candidates, active_policy, active_model)

        # The settings must be in force before the statement plans, because the
        # tenant filter runs after the approximate scan.
        for statement in hnsw_statements(active_policy):
            await self.session.execute(statement)
        rows = (
            await self.session.execute(
                candidate_statement(
                    user_id,
                    query_embedding,
                    allowed_scopes,
                    active_policy,
                    active_model,
                )
            )
        ).all()
        candidates = [
            RetrievalCandidate(
                id=row[0],
                content=row[1],
                summary=row[2],
                similarity=float(row[3]),
                age_days=float(row[4]),
                embedding_model=row[5],
            )
            for row in rows
        ]
        return rank_candidates(candidates, active_policy, active_model)

    async def get_chunk_by_id(self, chunk_id: UUID) -> MemoryChunkDTO | None:
        """Return one chunk, from Postgres when the service is session bound."""
        if not self._session_bound:
            return self._chunks.get(chunk_id)
        row = (
            await self.session.execute(
                select(MemoryChunkModel).where(MemoryChunkModel.id == chunk_id)
            )
        ).scalar_one_or_none()
        return self._to_dto(row) if row is not None else None

    async def set_excluded(self, chunk_id: UUID, excluded: bool) -> bool:
        """Toggle the excluded flag. The row stays for inspection."""
        if not self._session_bound:
            chunk = self._chunks.get(chunk_id)
            if not chunk:
                return False
            self._chunks[chunk_id] = replace(chunk, excluded=excluded)
            return True
        result = await self.session.execute(
            update(MemoryChunkModel)
            .where(MemoryChunkModel.id == chunk_id)
            .values(excluded=excluded)
        )
        return bool(result.rowcount)

    async def delete_chunk(self, chunk_id: UUID) -> bool:
        """Delete one chunk. Returns False when the row does not exist."""
        if not self._session_bound:
            return self._chunks.pop(chunk_id, None) is not None
        result = await self.session.execute(
            delete(MemoryChunkModel).where(MemoryChunkModel.id == chunk_id)
        )
        return bool(result.rowcount)

    # Domain Orchestration Methods
    async def search_memory(
        self,
        user_id: UUID,
        query: str,
        scopes: list[str] | None = None,
        top_n: int = 8,
    ) -> list[MemorySearchResultDTO]:
        """Encode the query on the local model and return the decayed ranking."""
        policy = RetrievalPolicy.from_settings(self.settings).with_top_n(top_n)
        embedding = await asyncio.to_thread(self.embedder.embed_query, query)
        return await self.search_embedding(
            user_id, embedding, scopes=scopes, policy=policy
        )

    # ------------------------------------------------------------- internals

    @staticmethod
    def _to_dto(row: Any) -> MemoryChunkDTO:
        return MemoryChunkDTO(
            id=row.id,
            user_id=row.user_id,
            scope=row.scope,
            content=row.content,
            summary=row.summary,
            source_message_ids=[UUID(str(item)) for item in row.source_message_ids],
            embedding_model=row.embedding_model,
            excluded=row.excluded,
            created_at=row.created_at,
        )

    @staticmethod
    def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
        """Cosine similarity over the shared width, for the in-memory store only."""
        width = min(len(left), len(right))
        if width == 0:
            return 0.0
        dot = sum(float(left[i]) * float(right[i]) for i in range(width))
        left_norm = math.sqrt(sum(float(left[i]) ** 2 for i in range(width)))
        right_norm = math.sqrt(sum(float(right[i]) ** 2 for i in range(width)))
        if left_norm == 0.0 or right_norm == 0.0:
            return 0.0
        return dot / (left_norm * right_norm)

    def _in_memory_candidates(
        self,
        user_id: UUID,
        query_embedding: Sequence[float],
        scopes: Sequence[str],
    ) -> list[RetrievalCandidate]:
        """Score the in-memory store, so session-less callers share the same rules."""
        now = datetime.now(timezone.utc)
        candidates: list[RetrievalCandidate] = []
        for chunk in self._chunks.values():
            if chunk.user_id != user_id or chunk.scope not in scopes or chunk.excluded:
                continue
            vector = _IN_MEMORY_EMBEDDINGS.get(chunk.id, [])
            candidates.append(
                RetrievalCandidate(
                    id=chunk.id,
                    content=chunk.content,
                    summary=chunk.summary,
                    similarity=self._cosine_similarity(query_embedding, vector),
                    age_days=max(
                        0.0, (now - chunk.created_at).total_seconds() / SECONDS_PER_DAY
                    ),
                    embedding_model=chunk.embedding_model,
                )
            )
        return candidates


# The in-memory chunk DTO holds no vector, so the store keeps one beside it.
_IN_MEMORY_EMBEDDINGS: dict[UUID, list[float]] = {}
