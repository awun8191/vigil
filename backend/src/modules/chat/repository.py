"""Chat repository protocol and data transfer objects."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID


@dataclass(frozen=True)
class ChatSessionDTO:
    """Represents an active interactive chat session."""
    session_id: UUID
    user_id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


@dataclass(frozen=True)
class ChatMessageDTO:
    """Represents a conversational message turn in a chat session."""
    id: UUID
    session_id: UUID
    role: str  # user, assistant, system
    content: str
    created_at: datetime
    run_id: UUID | None = None


@dataclass(frozen=True)
class ChatMessageCreateDTO:
    """Payload for appending a message to a chat session."""
    session_id: UUID
    role: str
    content: str


@runtime_checkable
class ChatRepository(Protocol):
    """Protocol contract for interactive chat sessions and history."""

    async def get_session(self, session_id: UUID) -> ChatSessionDTO | None:
        """
        Retrieve a chat session by its UUID.

        Args:
            session_id: UUID of the session.

        Returns:
            ChatSessionDTO if found, None otherwise.
        """
        ...

    async def list_user_sessions(
        self,
        user_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ChatSessionDTO]:
        """
        List all chat sessions belonging to a user.

        Args:
            user_id: Owner user UUID.
            limit: Maximum items to return.
            offset: Pagination offset.

        Returns:
            List of ChatSessionDTOs ordered by updated_at descending.
        """
        ...

    async def create_session(
        self,
        user_id: UUID,
        title: str | None = None,
    ) -> ChatSessionDTO:
        """
        Create a new chat session.

        Args:
            user_id: Owner user UUID.
            title: Optional title string.

        Returns:
            The created ChatSessionDTO.
        """
        ...

    async def save_message(self, data: ChatMessageCreateDTO) -> ChatMessageDTO:
        """
        Append a message to a session and bump updated_at.

        Args:
            data: Message creation DTO.

        Returns:
            The persisted ChatMessageDTO.
        """
        ...

    async def get_history(
        self,
        session_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ChatMessageDTO]:
        """
        Retrieve chronological message turns for a chat session.

        Args:
            session_id: UUID of the session.
            limit: Maximum messages to retrieve.
            offset: Pagination offset.

        Returns:
            List of ChatMessageDTOs ordered chronologically.
        """
        ...

    async def delete_session(self, session_id: UUID) -> bool:
        """
        Delete a chat session and cascade delete its messages.

        Args:
            session_id: UUID of the session to delete.

        Returns:
            True if deleted, False if session not found.
        """
        ...
