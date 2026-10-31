"""Unit tests for the memory index job and its indexed_at dedupe."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID, uuid4

import pytest

from src.config.settings import Settings
from src.modules.jobs.service import JobsService
from src.modules.memory.repository import MemoryChunkCreateDTO
from src.modules.memory.service import (
    INDEX_JOB_TYPE,
    IndexResult,
    IndexableMessage,
    MemoryService,
)

EMBEDDING_DIM = 384


def _settings() -> Settings:
    return Settings(_env_file=None)


class FakeIndexStore:
    """In-memory index store that enforces the indexed_at rule."""

    def __init__(self, messages: list[IndexableMessage]) -> None:
        self.messages = list(messages)
        self.indexed = set()
        self.chunks = []
        self.commits = 0
        self.rollbacks = 0
        self.marked_at = None

    async def fetch_unindexed(self, session_id: UUID, limit: int) -> list[IndexableMessage]:
        rows = [
            message
            for message in self.messages
            if message.session_id == session_id and message.id not in self.indexed
        ]
        return rows[:limit]

    async def add_chunks(self, chunks: Sequence[MemoryChunkCreateDTO]) -> int:
        self.chunks.extend(chunks)
        return len(chunks)

    async def mark_indexed(self, message_ids: Sequence[UUID], indexed_at: datetime) -> int:
        fresh = [message_id for message_id in message_ids if message_id not in self.indexed]
        self.indexed.update(message_ids)
        self.marked_at = indexed_at
        return len(fresh)

    async def rollback(self) -> None:
        self.rollbacks += 1
        self.chunks.clear()

    async def commit(self) -> None:
        self.commits += 1


class FakeEmbedder:
    """Deterministic embedder that records the texts it receives."""

    model_id = "bge-small-en-v1.5"
    dimensions = EMBEDDING_DIM

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def count_tokens(self, text: str) -> int:
        return len(text.split())

    def truncate(self, text: str, max_tokens: int | None = None) -> str:
        limit = 512 if max_tokens is None else max_tokens
        words = text.split()
        return text if len(words) <= limit else " ".join(words[:limit])

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[0.01] * EMBEDDING_DIM for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.01] * EMBEDDING_DIM

    def unload(self) -> None:
        return None


class ExplodingEmbedder(FakeEmbedder):
    """Fails the test if anything embeds during the enqueue path."""

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise AssertionError("the enqueue path must never embed")


class RacingStore(FakeIndexStore):
    """Pretend another worker marked the rows first."""

    async def mark_indexed(self, message_ids: Sequence[UUID], indexed_at: datetime) -> int:
        self.indexed.update(message_ids)
        self.marked_at = indexed_at
        return 0


def _conversation(session_id: UUID, user_id: UUID, turns: int = 10) -> list[IndexableMessage]:
    return [
        IndexableMessage(
            id=uuid4(),
            session_id=session_id,
            user_id=user_id,
            role="user" if index % 2 == 0 else "assistant",
            content=f"turn {index} about the quarterly budget review",
        )
        for index in range(turns)
    ]


async def test_no_double_index() -> None:
    """Indexing the same messages twice produces one chunk set, because indexed_at is set."""
    session_id = uuid4()
    user_id = uuid4()
    store = FakeIndexStore(_conversation(session_id, user_id))
    embedder = FakeEmbedder()
    service = MemoryService(session=None, settings=_settings(), embedder=embedder)

    first = await service.index_session(session_id, store=store)

    assert first.skipped is False
    assert first.messages_indexed == 10
    assert first.windows == 2
    assert first.chunks_created == 2
    assert len(store.chunks) == 2
    assert store.marked_at is not None, "indexed_at must be written"
    assert len(store.indexed) == 10
    assert store.commits == 1

    second = await service.index_session(session_id, store=store)

    assert second.skipped is True
    assert second.messages_indexed == 0
    assert second.chunks_created == 0
    assert len(store.chunks) == 2, "the second pass must not add a chunk set"
    assert len(embedder.calls) == 1, "the second pass must not embed again"


async def test_chunks_carry_model_scope_and_sources() -> None:
    """Each chunk records the model, the width, the scope, and its source messages."""
    session_id = uuid4()
    user_id = uuid4()
    messages = _conversation(session_id, user_id)
    store = FakeIndexStore(messages)
    service = MemoryService(session=None, settings=_settings(), embedder=FakeEmbedder())

    await service.index_session(session_id, store=store)

    assert store.chunks
    for chunk in store.chunks:
        assert chunk.user_id == user_id
        assert chunk.scope == "global"
        assert chunk.embedding_model == "bge-small-en-v1.5"
        assert chunk.embedding_dim == EMBEDDING_DIM
        assert len(chunk.embedding) == EMBEDDING_DIM
        assert chunk.source_message_ids
        assert set(chunk.source_message_ids) <= {message.id for message in messages}

    covered = {message_id for chunk in store.chunks for message_id in chunk.source_message_ids}
    assert covered == {message.id for message in messages}


async def test_tools_not_embedded_at_service_level() -> None:
    """Tool rows never reach a chunk, even when the store returns them."""
    session_id = uuid4()
    user_id = uuid4()
    messages = _conversation(session_id, user_id, turns=6)
    tool_message = IndexableMessage(
        id=uuid4(),
        session_id=session_id,
        user_id=user_id,
        role="tool",
        content='SECRET_TOOL_OUTPUT {"stdout": "root:x:0:0"}',
    )
    store = FakeIndexStore([*messages, tool_message])
    service = MemoryService(session=None, settings=_settings(), embedder=FakeEmbedder())

    await service.index_session(session_id, store=store)

    payload = "\n".join(chunk.content for chunk in store.chunks)
    assert "SECRET_TOOL_OUTPUT" not in payload
    source_ids = {message_id for chunk in store.chunks for message_id in chunk.source_message_ids}
    assert tool_message.id not in source_ids


async def test_enqueue_index_job_does_not_embed() -> None:
    """The user request path writes one queue row and no embedding call."""
    settings = _settings()
    jobs = JobsService(session=None, settings=settings)
    session_id = uuid4()
    service = MemoryService(session=None, settings=settings, embedder=ExplodingEmbedder())

    job = await service.enqueue_index_job(session_id, jobs=jobs)

    assert job.type == INDEX_JOB_TYPE == "index_memory"
    assert job.payload["session_id"] == str(session_id)
    assert job.payload["scope"] == "global"
    assert job.status == "pending"


async def test_run_index_job_reads_the_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """The job payload carries the session id and the scope."""
    session_id = uuid4()
    captured: list[tuple[UUID, str]] = []

    async def fake_index_session(
        session: UUID, *, scope: str = "global", **kwargs: object
    ) -> IndexResult:
        captured.append((session, scope))
        return IndexResult(messages_indexed=4, chunks_created=1, windows=1)

    service = MemoryService(session=None, settings=_settings(), embedder=FakeEmbedder())
    monkeypatch.setattr(service, "index_session", fake_index_session)

    result = await service.run_index_job({"session_id": str(session_id), "scope": "card"})

    assert captured == [(session_id, "card")]
    assert result.chunks_created == 1


async def test_run_index_job_rejects_a_payload_without_session() -> None:
    """A malformed job fails loudly instead of indexing the wrong rows."""
    service = MemoryService(session=None, settings=_settings(), embedder=FakeEmbedder())
    with pytest.raises(ValueError):
        await service.run_index_job({"scope": "global"})


async def test_losing_worker_discards_its_chunks() -> None:
    """A worker that loses the indexed_at race rolls back and keeps one chunk set."""
    session_id = uuid4()
    user_id = uuid4()
    store = RacingStore(_conversation(session_id, user_id))
    service = MemoryService(session=None, settings=_settings(), embedder=FakeEmbedder())

    result = await service.index_session(session_id, store=store)

    assert result.skipped is True
    assert result.chunks_created == 0
    assert store.rollbacks == 1
    assert store.commits == 0
    assert store.chunks == [], "the losing chunk set must not survive"


async def test_an_empty_session_is_skipped() -> None:
    """A session with no unindexed rows produces nothing and commits nothing."""
    store = FakeIndexStore([])
    service = MemoryService(session=None, settings=_settings(), embedder=FakeEmbedder())

    result = await service.index_session(uuid4(), store=store)

    assert result.skipped is True
    assert store.commits == 0
