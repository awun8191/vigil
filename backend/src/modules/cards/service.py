"""Cards service implementing action card feed management and thread deduplication."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.exceptions import NotFoundError
from src.config.settings import Settings
from src.modules.cards.repository import (
    CardActionDTO,
    CardCreateDTO,
    CardDTO,
    CardUpdateDTO,
)
from src.schemas.triage import CardActionModel, CardModel


class CardsService:
    """
    Action card feed service satisfying CardsRepository via duck typing.
    Uses Postgres when an AsyncSession is present and falls back to in-memory
    storage for sessionless tests.
    """

    _cards: ClassVar[dict[UUID, CardDTO]] = {}

    def __init__(self, session: AsyncSession | None, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def _is_db_session_active(self) -> bool:
        return isinstance(self.session, AsyncSession)

    @staticmethod
    def _to_dto(model: CardModel) -> CardDTO:
        return CardDTO(
            id=model.id,
            user_id=model.user_id,
            item_id=model.item_id,
            thread_id=model.thread_id,
            type=model.type,
            urgency=model.urgency,
            title=model.title,
            summary=model.summary,
            due_at=model.due_at,
            status=model.status,
            actions=[
                CardActionDTO(
                    id=a.action_key,
                    label=a.label,
                    action_type=a.action_type,
                    verb=a.verb,
                    payload=dict(a.payload or {}),
                )
                for a in sorted(model.actions, key=lambda x: x.sequence)
            ],
            feedback=model.feedback,
            created_at=model.created_at,
            updated_at=model.updated_at,
            proactivity_tag=model.proactivity_tag,
            verb=model.verb,
        )

    async def get_card_by_id(self, card_id: UUID) -> CardDTO | None:
        """Fetch card by UUID."""
        if self._is_db_session_active():
            assert self.session is not None
            model = await self.session.get(CardModel, card_id)
            return None if model is None else self._to_dto(model)
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
        if self._is_db_session_active():
            assert self.session is not None
            stmt = select(CardModel).where(CardModel.user_id == user_id)
            if status:
                stmt = stmt.where(CardModel.status == status)
            if urgency:
                stmt = stmt.where(CardModel.urgency == urgency)
            stmt = (
                stmt.order_by(CardModel.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            result = await self.session.execute(stmt)
            return [self._to_dto(m) for m in result.scalars().all()]

        filtered = [c for c in self._cards.values() if c.user_id == user_id]
        if status:
            filtered = [c for c in filtered if c.status == status]
        if urgency:
            filtered = [c for c in filtered if c.urgency == urgency]
        filtered.sort(key=lambda c: c.created_at, reverse=True)
        return filtered[offset : offset + limit]

    async def create_card(self, data: CardCreateDTO) -> CardDTO:
        """Create new card record in database."""
        card_id = uuid4()
        now = datetime.now(timezone.utc)
        if self._is_db_session_active():
            assert self.session is not None
            model = CardModel(
                id=card_id,
                user_id=data.user_id,
                item_id=data.item_id,
                thread_id=data.thread_id,
                type=data.type,
                urgency=data.urgency,
                proactivity_tag=data.proactivity_tag,
                verb=data.verb,
                title=data.title,
                summary=data.summary,
                due_at=data.due_at,
                status=data.status,
                feedback=None,
                created_at=now,
                updated_at=now,
            )
            for seq, act in enumerate(data.actions):
                model.actions.append(
                    CardActionModel(
                        id=uuid4(),
                        card_id=card_id,
                        action_key=act.id,
                        label=act.label,
                        action_type=act.action_type,
                        verb=act.verb,
                        payload=dict(act.payload),
                        sequence=seq,
                        created_at=now,
                    )
                )
            self.session.add(model)
            await self.session.flush()
            return self._to_dto(model)

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
            actions=list(data.actions),
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
        now = datetime.now(timezone.utc)
        if self._is_db_session_active():
            assert self.session is not None
            model = await self.session.get(CardModel, card_id)
            if model is None:
                return None
            if data.urgency is not None:
                model.urgency = data.urgency
            if data.title is not None:
                model.title = data.title
            if data.summary is not None:
                model.summary = data.summary
            if data.due_at is not None:
                model.due_at = data.due_at
            if data.status is not None:
                model.status = data.status
            if data.feedback is not None:
                model.feedback = data.feedback
            if data.proactivity_tag is not None:
                model.proactivity_tag = data.proactivity_tag
            if data.verb is not None:
                model.verb = data.verb
            model.updated_at = now
            await self.session.flush()
            return self._to_dto(model)

        existing = self._cards.get(card_id)
        if not existing:
            return None
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
        if self._is_db_session_active():
            assert self.session is not None
            result = await self.session.execute(
                select(CardModel)
                .where(CardModel.thread_id == thread_id)
                .order_by(CardModel.created_at.desc())
                .limit(1)
            )
            model = result.scalars().first()
            return None if model is None else self._to_dto(model)

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
        if self._is_db_session_active():
            assert self.session is not None
            stmt = select(func.count()).select_from(CardModel).where(CardModel.user_id == user_id)
            if status:
                stmt = stmt.where(CardModel.status == status)
            if urgency:
                stmt = stmt.where(CardModel.urgency == urgency)
            return int((await self.session.execute(stmt)).scalar_one() or 0)

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
            if existing and existing.user_id == data.user_id:
                updated = await self.update_card(
                    existing.id,
                    CardUpdateDTO(
                        title=data.title,
                        summary=data.summary,
                        due_at=data.due_at,
                        urgency=data.urgency,
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
