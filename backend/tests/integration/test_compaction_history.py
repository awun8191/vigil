"""Integration test for the compaction history loader.

The HTTP compaction endpoint reads the history of a run through
``load_run_history_for_compaction``. Before this test the method returned an
empty list, so the endpoint reported zero messages. The test seeds a run in a
scratch Postgres database and asserts a non-zero unit count.

When Postgres is absent the test skips with a reason.
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
from src.modules.compaction.service import CompactionService
from src.schemas.base import Base


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
    name = f"virgil_compaction_{uuid.uuid4().hex[:10]}"
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


async def _seed_run(dsn: str, turns: int) -> uuid.UUID:
    """Insert one run with alternating user and assistant messages."""
    user_id = uuid.uuid4()
    thread_id = uuid.uuid4()
    run_id = uuid.uuid4()
    started_at = datetime.now(timezone.utc) - timedelta(hours=2)
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(
            "INSERT INTO users (id, display_name) VALUES ($1, $2)",
            user_id,
            f"compaction-user-{user_id.hex[:8]}",
        )
        await connection.execute(
            "INSERT INTO threads (id, user_id, title) VALUES ($1, $2, $3)",
            thread_id,
            user_id,
            "compaction thread",
        )
        await connection.execute(
            "INSERT INTO runs (id, status, started_at) VALUES ($1, 'running', $2)",
            run_id,
            started_at,
        )
        for index in range(turns):
            role = "user" if index % 2 == 0 else "assistant"
            await connection.execute(
                "INSERT INTO messages (id, session_id, run_id, role, content, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                uuid.uuid4(),
                thread_id,
                run_id,
                role,
                f"Turn {index}: the release check is still in progress.",
                started_at + timedelta(minutes=index),
            )
    finally:
        await connection.close()
    return run_id


@pytest.mark.asyncio
async def test_the_run_history_loads_with_a_non_zero_unit_count() -> None:
    """The HTTP path reads the messages of the run and reports real units."""
    settings = Settings(APP_ENV="test")

    async with _scratch_database() as scratch:
        run_id = await _seed_run(scratch.dsn, turns=20)

        async with scratch.sessionmaker() as session:
            service = CompactionService(session=session, settings=settings)
            units = await service.load_run_history_for_compaction(run_id)
            messages = await service.load_messages(run_id)

        assert len(units) == 20, "the loader must return every message of the run"
        assert [unit.role for unit in units][:2] == ["user", "assistant"]
        for unit in units:
            assert unit.content, "the loader must keep the content of the row"
            assert unit.token_count > 0, "each unit must carry a real token count"
        # Tier 0 locks every user turn and the last six turns only, so the first
        # assistant turn of a long history is not locked.
        assert units[0].is_locked is True
        assert units[1].is_locked is False

        assert len(messages) == 20
        assert [message["role"] for message in messages][:2] == ["user", "assistant"]
        assert all(message["id"] for message in messages)

        # A run without messages has no units.
        async with scratch.sessionmaker() as session:
            service = CompactionService(session=session, settings=settings)
            empty = await service.load_run_history_for_compaction(uuid.uuid4())

        assert empty == []


@pytest.mark.asyncio
async def test_a_service_without_a_session_holds_no_history() -> None:
    """The in-memory service reports no units, because it reads no rows."""
    service = CompactionService(session=None, settings=Settings(APP_ENV="test"))

    assert await service.load_run_history_for_compaction(uuid.uuid4()) == []
