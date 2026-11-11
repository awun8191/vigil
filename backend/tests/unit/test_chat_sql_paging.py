"""The chat history is counted and paged in SQL, not in Python.

A thread of 300 rows must return exactly the requested page, in the SQL order,
and the session must report the count without loading the thread. The threads
that are not history (a tool row, an assistant row that requested tools, an
assistant row with no prose) must not be counted or paged.

The test runs against the local Postgres and cleans up the thread it created.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config.settings import Settings
from src.modules.auth.state import DEFAULT_USER_ID
from src.modules.chat.state import SqlChatRepository
from src.schemas.runtime import MessageModel, ThreadModel

HISTORY_ROWS = 300
"""Rows the chat view renders: 150 user turns and 150 assistant replies."""


class _RecordingSession:
    """Delegate to a real session and keep the SQL text of every statement."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.statements: list[str] = []

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        self.statements.append(str(statement))
        return await self._session.execute(statement, *args, **kwargs)

    async def get(self, *args: Any, **kwargs: Any) -> Any:
        return await self._session.get(*args, **kwargs)


def _engine() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Return an engine for the configured database, skipping when it is absent."""
    engine = create_async_engine(Settings(DEBUG=True).DATABASE_URL)
    return engine, async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def _seed_thread(
    session: AsyncSession,
    session_id: UUID,
    started: datetime,
) -> None:
    """Insert one thread with 300 history rows plus rows the view must skip."""
    session.add(ThreadModel(id=session_id, user_id=DEFAULT_USER_ID, title="Paging"))
    rows: list[MessageModel] = []
    for index in range(HISTORY_ROWS):
        rows.append(
            MessageModel(
                id=uuid4(),
                session_id=session_id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"history {index}",
                created_at=started + timedelta(seconds=index),
            )
        )
    skipped = [
        MessageModel(
            id=uuid4(),
            session_id=session_id,
            role="assistant",
            content="calling a tool",
            tool_calls=[{"id": "call-1", "type": "function"}],
            created_at=started + timedelta(seconds=HISTORY_ROWS + 1),
        ),
        MessageModel(
            id=uuid4(),
            session_id=session_id,
            role="assistant",
            content=None,
            created_at=started + timedelta(seconds=HISTORY_ROWS + 2),
        ),
        MessageModel(
            id=uuid4(),
            session_id=session_id,
            role="assistant",
            content="",
            created_at=started + timedelta(seconds=HISTORY_ROWS + 3),
        ),
        MessageModel(
            id=uuid4(),
            session_id=session_id,
            role="tool",
            content="tool output",
            created_at=started + timedelta(seconds=HISTORY_ROWS + 4),
        ),
    ]
    session.add_all(rows + skipped)
    await session.commit()


@pytest.mark.asyncio
async def test_history_page_and_count_come_from_sql() -> None:
    """A 300-message thread pages at the requested window and counts 300."""
    engine, factory = _engine()
    session_id = uuid4()
    started = datetime.now(timezone.utc) - timedelta(days=1)
    try:
        try:
            async with factory() as session:
                await _seed_thread(session, session_id, started)
        except OperationalError as exc:  # pragma: no cover - depends on the host
            await engine.dispose()
            pytest.skip(f"PostgreSQL is not available: {exc}")

        async with factory() as session:
            recorder = _RecordingSession(session)
            repo = SqlChatRepository(recorder)  # type: ignore[arg-type]

            page = await repo.get_history(session_id, limit=25, offset=100)
            assert [m.content for m in page] == [
                f"history {index}" for index in range(100, 125)
            ]
            assert len(page) == 25
            assert page[0].created_at < page[-1].created_at

            # The last page is short: the offset arithmetic is the query's.
            tail = await repo.get_history(session_id, limit=25, offset=290)
            assert [m.content for m in tail] == [
                f"history {index}" for index in range(290, HISTORY_ROWS)
            ]

            thread = await repo.get_session(session_id)
            assert thread is not None
            assert thread.message_count == HISTORY_ROWS

            count_statements = [s for s in recorder.statements if "count(" in s.lower()]
            assert count_statements, "the message count must be a SQL aggregate"
            paged = [s for s in recorder.statements if "LIMIT" in s.upper()]
            assert paged, "the history page must be limited in the query"
            assert all("OFFSET" in s.upper() for s in paged)
    finally:
        async with factory() as session:
            await session.execute(
                text("delete from threads where id = cast(:id as uuid)"),
                {"id": str(session_id)},
            )
            await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_history_handles_scalar_jsonb_tool_calls() -> None:
    """Rows with 'null'::jsonb or empty arrays in tool_calls must not crash jsonb_array_length."""
    engine, factory = _engine()
    session_id = uuid4()
    user_id = DEFAULT_USER_ID
    started = datetime.now(timezone.utc)
    try:
        try:
            async with factory() as session:
                session.add(ThreadModel(id=session_id, user_id=user_id, title="Scalar JSONB Test"))
                # 1. User message
                session.add(
                    MessageModel(
                        id=uuid4(),
                        session_id=session_id,
                        role="user",
                        content="Hello",
                        created_at=started,
                    )
                )
                # 2. Assistant message with SQL NULL tool_calls
                session.add(
                    MessageModel(
                        id=uuid4(),
                        session_id=session_id,
                        role="assistant",
                        content="Hi there (null)",
                        tool_calls=None,
                        created_at=started + timedelta(seconds=1),
                    )
                )
                # 3. Assistant message with empty array []
                session.add(
                    MessageModel(
                        id=uuid4(),
                        session_id=session_id,
                        role="assistant",
                        content="Hi there (empty array)",
                        tool_calls=[],
                        created_at=started + timedelta(seconds=2),
                    )
                )
                await session.commit()

                # Raw SQL insert for assistant with scalar 'null'::jsonb
                scalar_msg_id = uuid4()
                await session.execute(
                    text(
                        "INSERT INTO messages (id, session_id, role, content, tool_calls, created_at) "
                        "VALUES (:id, :session_id, 'assistant', 'Hi there (jsonb null)', 'null'::jsonb, :created_at)"
                    ),
                    {
                        "id": scalar_msg_id,
                        "session_id": session_id,
                        "created_at": started + timedelta(seconds=3),
                    },
                )
                # Tool call row that should be excluded from history
                await session.execute(
                    text(
                        "INSERT INTO messages (id, session_id, role, content, tool_calls, created_at) "
                        "VALUES (:id, :session_id, 'assistant', 'Calling tool', '[{\"id\": \"1\"}]'::jsonb, :created_at)"
                    ),
                    {
                        "id": uuid4(),
                        "session_id": session_id,
                        "created_at": started + timedelta(seconds=4),
                    },
                )
                await session.commit()
        except OperationalError as exc:
            await engine.dispose()
            pytest.skip(f"PostgreSQL is not available: {exc}")

        async with factory() as session:
            repo = SqlChatRepository(session)
            thread = await repo.get_session(session_id)
            assert thread is not None
            # 1 user + 3 prose assistants = 4 history messages
            assert thread.message_count == 4

            history = await repo.get_history(session_id)
            assert len(history) == 4
            assert [m.content for m in history] == [
                "Hello",
                "Hi there (null)",
                "Hi there (empty array)",
                "Hi there (jsonb null)",
            ]
    finally:
        async with factory() as session:
            await session.execute(
                text("delete from threads where id = cast(:id as uuid)"),
                {"id": str(session_id)},
            )
            await session.commit()
        await engine.dispose()
