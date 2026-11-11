"""Cards service implementing action card feed management and thread deduplication."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar
from uuid import UUID, uuid4
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.exceptions import NotFoundError
from src.config.settings import Settings
from src.modules.cards.repository import (
    CardActionDTO,
    CardCreateDTO,
    CardDTO,
    CardUpdateDTO,
)


class CardsService:
    """
    Action card feed service satisfying CardsRepository via duck typing.
    Constructor injection of session and settings.
    """

    _cards: ClassVar[dict[UUID, CardDTO]] = {}

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def get_card_by_id(self, card_id: UUID) -> CardDTO | None:
        """Fetch card by UUID."""
        return self._cards.get(card_id)

    async def list_cards_by_user(
        self,
        user_id: UUID,
        status: str | None = None,
        urgency: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CardDTO]:
        """Query user's cards ordered by urgency and due_at."""
        filtered = [c for c in self._cards.values() if c.user_id == user_id]
        if status:
            filtered = [c for c in filtered if c.status == status]
        if urgency:
            filtered = [c for c in filtered if c.urgency == urgency]
        return filtered[offset : offset + limit]

    async def create_card(self, data: CardCreateDTO) -> CardDTO:
        """Create new card record in database."""
        card_id = uuid4()
        now = datetime.now(timezone.utc)
        card = CardDTO(
            id=card_id,
            user_id=data.user_id,
            item_id=data.item_id,
            thread_id=data.thread_id,
            type=data.type,
            urgency=data.urgency,
            title=data.title,
            summary=data.summary,
            due_at=data.due_at,
            status=data.status,
            actions=data.actions,
            feedback=None,
            created_at=now,
            updated_at=now,
            proactivity_tag=data.proactivity_tag,
            verb=data.verb,
        )
        self._cards[card_id] = card
        return card

    async def update_card(self, card_id: UUID, data: CardUpdateDTO) -> CardDTO | None:
        """Update existing card."""
        existing = self._cards.get(card_id)
        if not existing:
            return None
        now = datetime.now(timezone.utc)
        updated = CardDTO(
            id=existing.id,
            user_id=existing.user_id,
            item_id=existing.item_id,
            thread_id=existing.thread_id,
            type=existing.type,
            urgency=data.urgency if data.urgency is not None else existing.urgency,
            title=data.title if data.title is not None else existing.title,
            summary=data.summary if data.summary is not None else existing.summary,
            due_at=data.due_at if data.due_at is not None else existing.due_at,
            status=data.status if data.status is not None else existing.status,
            actions=existing.actions,
            feedback=data.feedback if data.feedback is not None else existing.feedback,
            created_at=existing.created_at,
            updated_at=now,
            proactivity_tag=data.proactivity_tag if data.proactivity_tag is not None else existing.proactivity_tag,
            verb=data.verb if data.verb is not None else existing.verb,
        )
        self._cards[card_id] = updated
        return updated

    async def get_by_thread_id(self, thread_id: str) -> CardDTO | None:
        """Find existing card associated with conversation thread for deduplication."""
        for card in self._cards.values():
            if card.thread_id == thread_id:
                return card
        return None

    async def record_feedback(self, card_id: UUID, feedback: str) -> CardDTO | None:
        """Persist user feedback for card ranking calibration."""
        return await self.update_card(card_id, CardUpdateDTO(feedback=feedback))

    async def count_cards(
        self,
        user_id: UUID,
        status: str | None = None,
        urgency: str | None = None,
    ) -> int:
        """Count total cards matching criteria for pagination metadata."""
        filtered = [c for c in self._cards.values() if c.user_id == user_id]
        if status:
            filtered = [c for c in filtered if c.status == status]
        if urgency:
            filtered = [c for c in filtered if c.urgency == urgency]
        return len(filtered)

    # Domain Orchestration Methods
    async def upsert_card_for_thread(self, data: CardCreateDTO) -> CardDTO:
        """
        Deduplication pattern: if a card already exists for thread_id, update it;
        otherwise insert a new card.
        """
        if data.thread_id:
            existing = await self.get_by_thread_id(data.thread_id)
            if existing:
                updated = await self.update_card(
                    existing.id,
                    CardUpdateDTO(
                        summary=data.summary,
                        due_at=data.due_at,
                        proactivity_tag=data.proactivity_tag,
                        verb=data.verb,
                    ),
                )
                if updated:
                    return updated
        return await self.create_card(data)

    async def dismiss_card(self, card_id: UUID) -> CardDTO:
        """Shortcut to set card status to 'dismissed'."""
        updated = await self.update_card(card_id, CardUpdateDTO(status="dismissed"))
        if not updated:
            raise NotFoundError(f"Card {card_id} not found")
        return updated
