"""End-to-end memory cycle: a fact is written, indexed, and recalled later.

The test writes one fact in one chat session and a few unrelated facts in other
sessions. It enqueues the index job at run completion, runs it through the worker
handler, and then asks for the fact from a new session. The fact must be in the
top three.

The second test pins the recency rule: two chunks with equal similarity, where
the newer chunk must outrank the older one.

Each test builds its own scratch database. When Postgres is absent the test skips
with a reason.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Sequence

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config.settings import Settings, get_settings
from src.dependencies import get_memory_service
from src.modules.jobs.service import JobsService
from src.schemas.base import Base
from src.worker import dispatch_job

DIMENSIONS = 384
ACTIVE_MODEL = "bge-small-en-v1.5"
DEFAULT_SCOPE = "global"

# The fact that the user writes in the first session.
FACT = "The spare key for the flat is in the blue pot on the balcony."
# The question that the user asks in the later session.
QUESTION = "Where is the spare key for the flat?"
# Unrelated facts from other sessions. They must not displace the wanted fact.
OTHER_FACTS = (
    "The dentist appointment is on 14 March at 9 am.",
    "The car service is booked for Friday morning.",
    "The wifi password for the guest network is cedar-42.",
    "The dog vaccination is due next month.",
)

INSERT_CHUNK = (
    "INSERT INTO memory_chunks "
    "(id, user_id, scope, content, embedding, embedding_model, created_at) "
    "VALUES ($1, $2, 'global', $3, $4::vector, $5, $6)"
)


@dataclass
class ScratchDatabase:
    """A database that belongs to one test and is dropped afterwards."""

    name: str
    dsn: str
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]


def _base_dsn() -> str:
    """Return the configured database URL in plain asyncpg form."""
    return get_settings().DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)


@asynccontextmanager
async def _scratch_database() -> AsyncIterator[ScratchDatabase]:
    """Create an empty database with the schema, then drop it."""
    base_dsn = _base_dsn()
    try:
        admin = await asyncpg.connect(base_dsn)
    except Exception as exc:  # pragma: no cover - depends on the host
        pytest.skip(f"PostgreSQL is not available: {exc}")
    name = f"virgil_memory_cycle_{uuid.uuid4().hex[:10]}"
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    except Exception as exc:  # pragma: no cover - depends on the host
        await admin.close()
        pytest.skip(f"Cannot create a scratch database: {exc}")

    dsn = f"{base_dsn.rsplit('/', 1)[0]}/{name}"
    engine = create_async_engine(
        dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await connection.run_sync(Base.metadata.create_all)
        yield ScratchDatabase(
            name=name,
            dsn=dsn,
            engine=engine,
            sessionmaker=async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            ),
        )
    finally:
        await engine.dispose()
        admin = await asyncpg.connect(base_dsn)
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await admin.close()


def _vector_text(values: Sequence[float], precision: int = 6) -> str:
    """Format a vector for a pgvector cast."""
    return "[" + ",".join(f"{float(value):.{precision}f}" for value in values) + "]"


async def _insert_user(dsn: str, user_id: uuid.UUID) -> None:
    """Create the owner row that the chunk foreign key requires."""
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(
            "INSERT INTO users (id, display_name) VALUES ($1, $2)",
            user_id,
            f"memory-cycle-user-{user_id.hex[:8]}",
        )
    finally:
        await connection.close()


async def _insert_session(
    dsn: str,
    user_id: uuid.UUID,
    turns: Sequence[str],
    created_at: datetime,
) -> uuid.UUID:
    """Insert one chat session with its turns and return the session id."""
    thread_id = uuid.uuid4()
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(
            "INSERT INTO threads (id, user_id, title, created_at, updated_at) "
            "VALUES ($1, $2, $3, $4, $4)",
            thread_id,
            user_id,
            "memory-cycle",
            created_at,
        )
        rows = []
        for index, content in enumerate(turns):
            role = "user" if index % 2 == 0 else "assistant"
            rows.append(
                (
                    uuid.uuid4(),
                    thread_id,
                    role,
                    content,
                    created_at + timedelta(seconds=index),
                )
            )
        await connection.executemany(
            "INSERT INTO messages (id, session_id, role, content, created_at) "
            "VALUES ($1, $2, $3, $4, $5)",
            rows,
        )
    finally:
        await connection.close()
    return thread_id


async def _insert_chunk(
    dsn: str,
    chunk_id: uuid.UUID,
    user_id: uuid.UUID,
    content: str,
    vector: Sequence[float],
    created_at: datetime,
) -> None:
    """Insert one memory chunk with a fixed vector and age."""
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(
            INSERT_CHUNK,
            chunk_id,
            user_id,
            content,
            _vector_text(vector),
            ACTIVE_MODEL,
            created_at,
        )
    finally:
        await connection.close()


async def _count_chunks(dsn: str, user_id: uuid.UUID) -> int:
    """Return the number of chunk rows of one user."""
    connection = await asyncpg.connect(dsn)
    try:
        return int(
            await connection.fetchval(
                "SELECT count(*) FROM memory_chunks WHERE user_id = $1", user_id
            )
        )
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_fact_recalled_next_session() -> None:
    """A fact indexed at run completion comes back in the top three later."""
    settings = Settings()
    user_id = uuid.uuid4()
    written_at = datetime.now(timezone.utc) - timedelta(days=1)

    async with _scratch_database() as scratch:
        await _insert_user(scratch.dsn, user_id)

        # One session writes the fact. The other sessions write unrelated facts.
        fact_session = await _insert_session(
            scratch.dsn,
            user_id,
            (FACT, f"Noted. {FACT}"),
            written_at,
        )
        other_sessions = [
            await _insert_session(
                scratch.dsn,
                user_id,
                (other, "Noted."),
                written_at + timedelta(hours=index + 1),
            )
            for index, other in enumerate(OTHER_FACTS)
        ]
        all_sessions = [fact_session, *other_sessions]

        # Each session completes a run, so each one enqueues its index job.
        async with scratch.sessionmaker() as session:
            memory = get_memory_service(session=session, settings=settings)
            jobs = JobsService(session=session, settings=settings)
            for thread_id in all_sessions:
                job = await memory.enqueue_index_job(thread_id)
                assert job.type == "index_memory"

            # The worker claims every job and runs the registered handler.
            for _ in all_sessions:
                claimed = await jobs.claim_next_job(worker_id="e2e-memory")
                assert claimed is not None
                assert claimed.type == "index_memory"
                handled = await dispatch_job(claimed, session, settings)
                assert handled is True
                await jobs.mark_completed(claimed.id)

        assert await _count_chunks(scratch.dsn, user_id) == len(all_sessions)

        # The later session asks the question. Retrieval reads the same user memory.
        later_session = await _insert_session(
            scratch.dsn,
            user_id,
            (QUESTION,),
            datetime.now(timezone.utc),
        )
        async with scratch.sessionmaker() as session:
            later = get_memory_service(session=session, settings=settings)
            results = await later.search_memory(
                user_id,
                QUESTION,
                scopes=[DEFAULT_SCOPE],
                top_n=3,
            )

        assert results, "The later session recalled nothing."
        assert len(results) <= 3
        positions = [
            position
            for position, result in enumerate(results)
            if FACT in result.content
        ]
        assert positions, (
            "The expected fact is not in the top three. "
            f"Recalled: {[result.content for result in results]}"
        )
        assert positions[0] < 3
        assert results[0].similarity >= settings.MEMORY_RETRIEVAL_MIN_SIMILARITY
        assert results[0].age_days >= 0.0

        # The stored chunk keeps the provenance of the session that wrote it.
        connection = await asyncpg.connect(scratch.dsn)
        try:
            source_ids = await connection.fetchval(
                "SELECT source_message_ids FROM memory_chunks "
                "WHERE user_id = $1 AND content LIKE $2",
                user_id,
                f"%{FACT}%",
            )
            indexed = await connection.fetchval(
                "SELECT count(*) FROM messages WHERE session_id = $1 "
                "AND indexed_at IS NOT NULL",
                fact_session,
            )
        finally:
            await connection.close()
        assert source_ids, "The fact chunk carries no source message."
        assert int(indexed) == 2, "The fact session rows are not marked indexed."
        assert later_session != fact_session


@pytest.mark.asyncio
async def test_recency_breaks_tie() -> None:
    """A newer chunk outranks an older chunk when the similarity is equal."""
    settings = Settings()
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # The query is the first axis. The chunk vector has a cosine of 0.6 with it,
    # which is under the override threshold, so the decay applies to both chunks.
    query = [0.0] * DIMENSIONS
    query[0] = 1.0
    chunk_vector = [0.0] * DIMENSIONS
    chunk_vector[0] = 0.6
    chunk_vector[1] = 0.8

    older_id = uuid.uuid4()
    newer_id = uuid.uuid4()
    older_content = "The older note records the spare key for the flat."
    newer_content = "The newer note records the spare key for the flat."

    async with _scratch_database() as scratch:
        await _insert_user(scratch.dsn, user_id)
        await _insert_chunk(
            scratch.dsn,
            older_id,
            user_id,
            older_content,
            chunk_vector,
            now - timedelta(days=100),
        )
        await _insert_chunk(
            scratch.dsn,
            newer_id,
            user_id,
            newer_content,
            chunk_vector,
            now,
        )

        async with scratch.sessionmaker() as session:
            memory = get_memory_service(session=session, settings=settings)
            results = await memory.search_decay_weighted(
                user_id,
                query,
                [DEFAULT_SCOPE],
                top_n=2,
            )

    assert [result.id for result in results] == [newer_id, older_id]
    assert abs(results[0].similarity - results[1].similarity) < 1e-6
    assert results[0].similarity < settings.MEMORY_RETRIEVAL_OVERRIDE_THRESHOLD
    assert results[0].age_days < results[1].age_days
    assert results[0].final_score > results[1].final_score
