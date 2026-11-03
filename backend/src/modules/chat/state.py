"""SQL and in-memory chat stores, the runs-module journal pattern applied to chat."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.exceptions import NotFoundError
from src.modules.chat.repository import (
    ChatMessageCreateDTO,
    ChatMessageDTO,
    ChatRepository,
    ChatSessionDTO,
)
from src.schemas.runtime import MessageModel, ThreadModel


CHAT_HISTORY_ROLES: tuple[str, ...] = ("user", "assistant")
"""Roles the chat view renders. Tool rows belong to run transcripts, not the thread view."""


class InMemoryChatRepository:
    """Process-local chat store, the test and harness stand-in for the SQL repository."""

    _sessions: dict[UUID, ChatSessionDTO]
    _messages: dict[UUID, list[ChatMessageDTO]]

    def __init__(self) -> None:
        self._sessions = {}
        self._messages = {}

    async def get_session(self, session_id: UUID) -> ChatSessionDTO | None:
        return self._sessions.get(session_id)

    async def list_user_sessions(
        self,
        user_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ChatSessionDTO]:
        user_sessions = [s for s in self._sessions.values() if s.user_id == user_id]
        user_sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return user_sessions[offset : offset + limit]

    async def create_session(
        self,
        user_id: UUID,
        title: str | None = None,
    ) -> ChatSessionDTO:
        session = ChatSessionDTO(
            session_id=uuid4(),
            user_id=user_id,
            title=title or "New Chat",
            created_at=_now(),
            updated_at=_now(),
            message_count=0,
        )
        self._sessions[session.session_id] = session
        self._messages[session.session_id] = []
        return session

    async def save_message(self, data: ChatMessageCreateDTO) -> ChatMessageDTO:
        msg = ChatMessageDTO(
            id=uuid4(),
            session_id=data.session_id,
            role=data.role,
            content=data.content,
            created_at=_now(),
        )
        self._messages.setdefault(data.session_id, []).append(msg)
        if data.session_id in self._sessions:
            old = self._sessions[data.session_id]
            self._sessions[data.session_id] = replace(
                old,
                updated_at=_now(),
                message_count=old.message_count + 1,
            )
        return msg

    async def get_history(
        self,
        session_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ChatMessageDTO]:
        rows = [
            msg
            for msg in self._messages.get(session_id, [])
            if msg.role in CHAT_HISTORY_ROLES
        ]
        return rows[offset : offset + limit]

    async def delete_session(self, session_id: UUID) -> bool:
        if session_id not in self._sessions:
            return False
        del self._sessions[session_id]
        self._messages.pop(session_id, None)
        return True


class SqlChatRepository:
    """Chat repository over the unified ``threads`` and ``messages`` tables.

    Both runs and chat write turn rows into the single ``messages`` table. The
    chat view therefore reads rows of a thread directly from the same table the
    RunLoop persists to, so a run-driven agent conversation and an interactive
    one share one history.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _session_dto(model: ThreadModel, message_count: int) -> ChatSessionDTO:
        return ChatSessionDTO(
            session_id=model.id,
            user_id=model.user_id,
            title=model.title,
            created_at=model.created_at,
            updated_at=model.updated_at,
            message_count=message_count,
        )

    @staticmethod
    def _message_dto(model: MessageModel) -> ChatMessageDTO:
        return ChatMessageDTO(
            id=model.id,
            session_id=model.session_id or uuid4(),  # always set for chat rows
            role=model.role,
            content=model.content or "",
            created_at=model.created_at,
        )

    async def get_session(self, session_id: UUID) -> ChatSessionDTO | None:
        model = await self.session.get(ThreadModel, session_id)
        if model is None:
            return None
        count = await self._message_count(session_id)
        return self._session_dto(model, count)

    async def list_user_sessions(
        self,
        user_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ChatSessionDTO]:
        count_sub = (
            select(func.count(MessageModel.id))
            .where(MessageModel.session_id == ThreadModel.id)
            .correlate(ThreadModel)
            .scalar_subquery()
        )
        result = await self.session.execute(
            select(ThreadModel, count_sub.label("message_count"))
            .where(ThreadModel.user_id == user_id)
            .order_by(ThreadModel.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return [
            self._session_dto(thread, int(count))
            for thread, count in result.all()
        ]

    async def create_session(
        self,
        user_id: UUID,
        title: str | None = None,
    ) -> ChatSessionDTO:
        model = ThreadModel(id=uuid4(), user_id=user_id, title=title or "New Chat")
        self.session.add(model)
        await self.session.flush()
        return self._session_dto(model, 0)

    async def save_message(self, data: ChatMessageCreateDTO) -> ChatMessageDTO:
        thread = await self.session.get(ThreadModel, data.session_id)
        if thread is None:
            raise NotFoundError(f"Chat session {data.session_id} not found")
        msg = MessageModel(
            id=uuid4(),
            session_id=data.session_id,
            run_id=None,
            role=data.role,
            content=data.content,
        )
        self.session.add(msg)
        thread.updated_at = _now()
        await self.session.flush()
        return self._message_dto(msg)

    @staticmethod
    def _history_filter(session_id: UUID) -> tuple[Any, ...]:
        """Return the WHERE terms the chat view applies to one thread.

        A user row is always history. An assistant row is history only when it
        carries prose: a row that requested tools, or that has no content yet,
        belongs to the run transcript.
        """
        no_tool_calls = or_(
            MessageModel.tool_calls.is_(None),
            func.jsonb_array_length(MessageModel.tool_calls) == 0,
        )
        has_content = and_(
            MessageModel.content.is_not(None),
            MessageModel.content != "",
        )
        return (
            MessageModel.session_id == session_id,
            MessageModel.role.in_(CHAT_HISTORY_ROLES),
            or_(
                MessageModel.role == "user",
                and_(MessageModel.role == "assistant", no_tool_calls, has_content),
            ),
        )

    async def get_history(
        self,
        session_id: UUID,
        limit: int = 200,
        offset: int = 0,
    ) -> list[ChatMessageDTO]:
        result = await self.session.execute(
            select(MessageModel)
            .where(*self._history_filter(session_id))
            .order_by(MessageModel.created_at.asc(), MessageModel.id.asc())
            .offset(offset)
            .limit(limit)
        )
        return [self._message_dto(m) for m in result.scalars().all()]

    async def delete_session(self, session_id: UUID) -> bool:
        model = await self.session.get(ThreadModel, session_id)
        if model is None:
            return False
        await self.session.delete(model)
        await self.session.flush()
        return True

    async def _message_count(self, session_id: UUID) -> int:
        result = await self.session.execute(
            select(func.count(MessageModel.id)).where(*self._history_filter(session_id))
        )
        return int(result.scalar_one())


def _now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "CHAT_HISTORY_ROLES",
    "InMemoryChatRepository",
    "SqlChatRepository",
]
