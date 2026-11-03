"""Chat service implementing direct interactive conversation sessions.

A chat session is a thread row, its turns are rows of the unified ``messages``
table. Agent replies do not stream through this module: the in-app chat sends
a message by creating a run (``POST /runs`` with the thread as ``session_id``)
and the RunLoop persists each reply into the same table, so ``get_history``
returns the full conversation across both paths.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.exceptions import NotFoundError
from src.modules.chat.data import ChatMessageResponse
from src.modules.chat.repository import (
    ChatMessageCreateDTO,
    ChatMessageDTO,
    ChatRepository,
    ChatSessionDTO,
)
from src.modules.chat.state import InMemoryChatRepository, SqlChatRepository


class ChatService:
    """Chat service satisfying ChatRepository via duck typing.

    The constructor picks the repository: the SQL implementation on a request
    database session, the in-memory one when the service is built without a
    session (tests, the CLI harness).
    """

    _repo: ChatRepository

    def __init__(
        self,
        session: AsyncSession | None,
        settings: object,
        repository: ChatRepository | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        if repository is not None:
            self._repo = repository
        elif session is not None:
            self._repo = SqlChatRepository(session)
        else:
            self._repo = InMemoryChatRepository()

    async def get_session(self, session_id: UUID) -> ChatSessionDTO | None:
        return await self._repo.get_session(session_id)

    async def list_user_sessions(
        self,
        user_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ChatSessionDTO]:
        return await self._repo.list_user_sessions(user_id, limit, offset)

    async def create_session(
        self,
        user_id: UUID,
        title: str | None = None,
    ) -> ChatSessionDTO:
        return await self._repo.create_session(user_id, title)

    async def save_message(self, data: ChatMessageCreateDTO) -> ChatMessageDTO:
        return await self._repo.save_message(data)

    async def get_history(
        self,
        session_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ChatMessageDTO]:
        return await self._repo.get_history(session_id, limit, offset)

    async def delete_session(self, session_id: UUID) -> bool:
        return await self._repo.delete_session(session_id)

    # -- domain orchestration ------------------------------------------------

    async def send_message(
        self,
        session_id: UUID,
        user_id: UUID,
        content: str,
    ) -> ChatMessageResponse:
        """Persist one user turn; the reply comes from a run, not this endpoint.

        Ownership is enforced here: the thread of another user reads as not
        found, never as a partial view of their messages.
        """
        thread = await self.get_session(session_id)
        if thread is None or thread.user_id != user_id:
            raise NotFoundError(f"Chat session {session_id} not found")
        msg = await self.save_message(
            ChatMessageCreateDTO(session_id=session_id, role="user", content=content)
        )
        return ChatMessageResponse.model_validate(msg)
