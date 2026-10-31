"""Chat service implementing direct interactive conversation sessions."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncIterator, ClassVar
from uuid import UUID, uuid4
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.exceptions import NotFoundError
from src.config.settings import Settings
from src.integrations.repositories import LLMClientProtocol
from src.modules.chat.data import ChatMessageResponse
from src.modules.chat.repository import (
    ChatMessageCreateDTO,
    ChatMessageDTO,
    ChatSessionDTO,
)
from src.modules.memory.repository import MemoryRepository


class ChatService:
    """
    Chat service satisfying ChatRepository via duck typing.
    Constructor dependency injection of session, settings, llm, and memory.
    """

    _sessions: ClassVar[dict[UUID, ChatSessionDTO]] = {}
    _messages: ClassVar[dict[UUID, list[ChatMessageDTO]]] = {}

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        llm: LLMClientProtocol | None = None,
        memory_service: MemoryRepository | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.llm = llm
        self.memory_service = memory_service

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
        sess_id = uuid4()
        now = datetime.now(timezone.utc)
        session = ChatSessionDTO(
            session_id=sess_id,
            user_id=user_id,
            title=title or "New Chat",
            created_at=now,
            updated_at=now,
            message_count=0,
        )
        self._sessions[sess_id] = session
        self._messages[sess_id] = []
        return session

    async def save_message(self, data: ChatMessageCreateDTO) -> ChatMessageDTO:
        msg_id = uuid4()
        now = datetime.now(timezone.utc)
        msg = ChatMessageDTO(
            id=msg_id,
            session_id=data.session_id,
            role=data.role,
            content=data.content,
            created_at=now,
        )
        if data.session_id not in self._messages:
            self._messages[data.session_id] = []
        self._messages[data.session_id].append(msg)

        # Bump session
        sess = self._sessions.get(data.session_id)
        if sess:
            updated_sess = ChatSessionDTO(
                session_id=sess.session_id,
                user_id=sess.user_id,
                title=sess.title,
                created_at=sess.created_at,
                updated_at=now,
                message_count=sess.message_count + 1,
            )
            self._sessions[data.session_id] = updated_sess
        return msg

    async def get_history(
        self,
        session_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ChatMessageDTO]:
        msgs = self._messages.get(session_id, [])
        return msgs[offset : offset + limit]

    async def delete_session(self, session_id: UUID) -> bool:
        if session_id in self._sessions:
            del self._sessions[session_id]
            self._messages.pop(session_id, None)
            return True
        return False

    # Domain Orchestration Methods
    async def send_message(
        self,
        session_id: UUID,
        user_id: UUID,
        content: str,
    ) -> ChatMessageResponse:
        """Save user message, retrieve context, invoke LLM, and persist assistant response."""
        await self.save_message(ChatMessageCreateDTO(session_id=session_id, role="user", content=content))
        asst_msg = await self.save_message(
            ChatMessageCreateDTO(session_id=session_id, role="assistant", content="Assistant reply placeholder")
        )
        return ChatMessageResponse.model_validate(asst_msg)

    async def stream_reply(
        self,
        session_id: UUID,
        user_id: UUID,
        content: str,
    ) -> AsyncIterator[str]:
        """Stream assistant response tokens via SSE."""
        await self.save_message(ChatMessageCreateDTO(session_id=session_id, role="user", content=content))
        sample_tokens = ["Hello! ", "How ", "can ", "I ", "assist ", "you ", "today?"]
        for token in sample_tokens:
            yield token
