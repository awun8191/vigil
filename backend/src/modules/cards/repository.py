"""Cards repository protocol and data transfer objects."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID


@dataclass(frozen=True)
class CardActionDTO:
    """Action button definition attached to a card."""
    id: str
    label: str
    action_type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CardDTO:
    """Represents a proactive action card surfacing an event or task."""
    id: UUID
    user_id: UUID
    item_id: UUID | None
    thread_id: str | None
    type: str  # inquiry, billing, deadline, subscription, meeting, security, general
    urgency: str  # low, medium, high, critical
    title: str
    summary: str | None
    due_at: datetime | None
    status: str  # new, seen, in_progress, awaiting_approval, done, dismissed, expired
    actions: list[CardActionDTO]
    feedback: str | None  # useful, not_useful, dismissed
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CardCreateDTO:
    """Payload for creating a new action card."""
    user_id: UUID
    type: str
    urgency: str
    title: str
    item_id: UUID | None = None
    thread_id: str | None = None
    summary: str | None = None
    due_at: datetime | None = None
    status: str = "new"
    actions: list[CardActionDTO] = field(default_factory=list)


@dataclass(frozen=True)
class CardUpdateDTO:
    """Payload for modifying an existing card."""
    status: str | None = None
    feedback: str | None = None
    summary: str | None = None
    due_at: datetime | None = None
    urgency: str | None = None
    title: str | None = None


@runtime_checkable
class CardsRepository(Protocol):
    """Protocol contract for Action Card feed management."""

    async def get_card_by_id(self, card_id: UUID) -> CardDTO | None:
        """
        Retrieve a single card by its ID.

        Args:
            card_id: UUID of the card.

        Returns:
            CardDTO if found, None otherwise.
        """
        ...

    async def list_cards_by_user(
        self,
        user_id: UUID,
        status: str | None = None,
        urgency: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CardDTO]:
        """
        List cards belonging to a user, with optional filtering and pagination.

        Args:
            user_id: Owner user UUID.
            status: Optional status filter.
            urgency: Optional urgency filter.
            limit: Maximum items to return.
            offset: Pagination offset.

        Returns:
            List of matching CardDTOs ordered by urgency and due date.
        """
        ...

    async def create_card(self, data: CardCreateDTO) -> CardDTO:
        """
        Create and persist a new action card.

        Args:
            data: Card creation DTO.

        Returns:
            The created CardDTO.
        """
        ...

    async def update_card(self, card_id: UUID, data: CardUpdateDTO) -> CardDTO | None:
        """
        Update mutable fields of an existing card.

        Args:
            card_id: UUID of the card to update.
            data: DTO containing fields to update.

        Returns:
            Updated CardDTO, or None if the card does not exist.
        """
        ...

    async def get_by_thread_id(self, thread_id: str) -> CardDTO | None:
        """
        Retrieve the latest card associated with a specific conversation thread.

        Args:
            thread_id: External thread identifier (e.g. Gmail threadId).

        Returns:
            CardDTO if an active thread card exists, None otherwise.
        """
        ...

    async def record_feedback(self, card_id: UUID, feedback: str) -> CardDTO | None:
        """
        Record user feedback on an action card.

        Args:
            card_id: UUID of the target card.
            feedback: Feedback tag ("useful", "not_useful", "dismissed").

        Returns:
            Updated CardDTO, or None if not found.
        """
        ...

    async def count_cards(
        self,
        user_id: UUID,
        status: str | None = None,
        urgency: str | None = None,
    ) -> int:
        """
        Count total cards matching criteria for pagination metadata.

        Args:
            user_id: Owner user UUID.
            status: Optional status filter.
            urgency: Optional urgency filter.

        Returns:
            Integer total count.
        """
        ...
