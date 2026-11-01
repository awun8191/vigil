"""Integration tests for the two index_memory producers.

V-14 decision 1 puts an index job on the queue twice: when a run completes and
when a chat session goes idle. Neither path embeds. Both paths write a queue
row, and this file proves the row with a scratch Postgres database.

When Postgres is absent the tests skip with a reason.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

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
from src.modules.runs.service import RunsService
from src.schemas.base import Base
from tests.unit.loop_fakes import FakeToolExecutor, ScriptedModel, agent_config, text_response


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
    name = f"virgil_producers_{uuid.uuid4().hex[:10]}"
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    except Exception as exc:  # pragma: no cover - depends on the host
        await admin.close()
        pytest.skip(f"Cannot create a scratch database: {exc}")

    dsn = f"{base_dsn.rsplit('/', 1)[0]}/{name}"
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
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


async def _seed_user_and_thread(dsn: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Insert one user and one thread, and return their ids."""
    user_id = uuid.uuid4()
    thread_id = uuid.uuid4()
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(
            "INSERT INTO users (id, display_name) VALUES ($1, $2)",
            user_id,
            f"producer-user-{user_id.hex[:8]}",
        )
        await connection.execute(
            "INSERT INTO threads (id, user_id, title) VALUES ($1, $2, $3)",
            thread_id,
            user_id,
            "producer thread",
        )
    finally:
        await connection.close()
    return user_id, thread_id


async def _insert_turns(
    dsn: str,
    thread_id: uuid.UUID,
    created_at: datetime,
    indexed_at: datetime | None = None,
) -> None:
    """Insert one user turn and one assistant turn with the given timestamps."""
    connection = await asyncpg.connect(dsn)
    try:
        for role, content in (("user", "Where is the spare key?"), ("assistant", "In the blue pot.")):
            await connection.execute(
                "INSERT INTO messages (id, session_id, role, content, created_at, indexed_at) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                uuid.uuid4(),
                thread_id,
                role,
                content,
                created_at,
                indexed_at,
            )
    finally:
        await connection.close()


async def _count_index_jobs(dsn: str, thread_id: uuid.UUID) -> int:
    """Count the index_memory rows that name one session."""
    connection = await asyncpg.connect(dsn)
    try:
        return int(
            await connection.fetchval(
                "SELECT count(*) FROM jobs "
                "WHERE type = 'index_memory' AND payload->>'session_id' = $1",
                str(thread_id),
            )
        )
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_idle_timeout_enqueues_one_index_job() -> None:
    """A session with unindexed turns and no new turn inside the window enqueues one job."""
    settings = Settings(APP_ENV="test")
    idle_at = datetime.now(timezone.utc) - timedelta(hours=2)

    async with _scratch_database() as scratch:
        _user_id, idle_thread = await _seed_user_and_thread(scratch.dsn)
        await _insert_turns(scratch.dsn, idle_thread, idle_at)
        _other_user, fresh_thread = await _seed_user_and_thread(scratch.dsn)
        await _insert_turns(scratch.dsn, fresh_thread, datetime.now(timezone.utc))

        async with scratch.sessionmaker() as session:
            memory = get_memory_service(session=session, settings=settings)
            enqueued = await memory.enqueue_idle_session_index_jobs(idle_seconds=900)

        assert len(enqueued) == 1
        assert enqueued[0].type == "index_memory"
        assert enqueued[0].payload["session_id"] == str(idle_thread)
        assert await _count_index_jobs(scratch.dsn, idle_thread) == 1
        assert await _count_index_jobs(scratch.dsn, fresh_thread) == 0

        # A second sweep sees the open job and does not add a second row.
        async with scratch.sessionmaker() as session:
            memory = get_memory_service(session=session, settings=settings)
            again = await memory.enqueue_idle_session_index_jobs(idle_seconds=900)

        assert again == []
        assert await _count_index_jobs(scratch.dsn, idle_thread) == 1


@pytest.mark.asyncio
async def test_run_completion_enqueues_one_index_job() -> None:
    """A run that completes on a session enqueues one index job for that session."""
    settings = Settings(APP_ENV="test")

    async with _scratch_database() as scratch:
        user_id, thread_id = await _seed_user_and_thread(scratch.dsn)

        model = ScriptedModel([text_response("The spare key is in the blue pot.")])
        tools = FakeToolExecutor()
        async with scratch.sessionmaker() as session:
            service = RunsService(
                session=session, settings=settings, llm=model, tools=tools
            )
            config = agent_config()
            await service.save_agent_config(config)
            run = await service.start_run(
                config.id,
                initial_prompt="Remember that the spare key is in the blue pot.",
                session_id=thread_id,
            )
            finished = await service.process(run.id)

            assert finished.status == "completed"
            rows = await service.get_messages_for_run(run.id)

        assert len(rows) == 2, "the run must write the prompt and the final answer"
        assert rows[0].role == "user"
        assert {row.session_id for row in rows} == {thread_id}, (
            "the loop must copy the session of the run onto every row"
        )
        assert await _count_index_jobs(scratch.dsn, thread_id) == 1

        # The run path embeds nothing: no chunk rows exist yet.
        connection = await asyncpg.connect(scratch.dsn)
        try:
            chunks = await connection.fetchval(
                "SELECT count(*) FROM memory_chunks WHERE user_id = $1", user_id
            )
        finally:
            await connection.close()
        assert chunks == 0
