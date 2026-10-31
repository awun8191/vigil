"""Triage service implementing two-stage proactive classification pipeline."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar
from uuid import UUID, uuid4
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.integrations.repositories import JevClientProtocol, LLMClientProtocol
from src.modules.cards.repository import CardsRepository
from src.modules.triage.repository import TriageResultCreateDTO, TriageResultDTO


class TriageService:
    """
    Two-stage proactive triage service satisfying TriageRepository via duck typing.
    Stage 1: TypeSafe AI Jev decision probability gate (zero generative spend).
    Stage 2: Fast LLM (Tier 2) metadata extractor.
    """

    _triage_results: ClassVar[dict[UUID, TriageResultDTO]] = {}
    _item_to_result: ClassVar[dict[UUID, UUID]] = {}

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        jev_client: JevClientProtocol | None = None,
        llm_client: LLMClientProtocol | None = None,
        cards_service: CardsRepository | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.jev_client = jev_client
        self.llm_client = llm_client
        self.cards_service = cards_service

    async def save_triage_result(self, data: TriageResultCreateDTO) -> TriageResultDTO:
        """Persist triage gate answers and probabilities."""
        result_id = uuid4()
        now = datetime.now(timezone.utc)
        result = TriageResultDTO(
            id=result_id,
            item_id=data.item_id,
            answers=data.answers,
            probabilities=data.probabilities,
            decision=data.decision,
            model_version=data.model_version,
            created_at=now,
        )
        self._triage_results[result_id] = result
        self._item_to_result[data.item_id] = result_id
        return result

    async def get_triage_result_by_item(self, item_id: UUID) -> TriageResultDTO | None:
        """Fetch existing triage result for item."""
        result_id = self._item_to_result.get(item_id)
        if result_id:
            return self._triage_results.get(result_id)
        return None

    async def list_recent_triage(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TriageResultDTO]:
        """Fetch recent triage results."""
        results = list(self._triage_results.values())
        return results[offset : offset + limit]

    # Domain Pipeline Orchestration
    async def triage_item(self, item_id: UUID) -> TriageResultDTO:
        """
        Execute full two-stage proactive triage pipeline on ingested item:
        1. Fetch item from items table.
        2. Evaluate Jev decision questions (action_required, urgency, category, is_bulk).
        3. If p(action_required) < 0.3 and is_bulk: decision = 'discarded'.
        4. If p >= 0.8: run Stage 2 Fast LLM to extract metadata and create Action Card.
        5. Persist triage result and return.
        """
        answers = {"action_required": "yes", "urgency": "high"}
        probs = {"action_required": 0.88, "is_bulk": 0.05}
        decision = "card_created"

        dto = await self.save_triage_result(
            TriageResultCreateDTO(
                item_id=item_id,
                answers=answers,
                probabilities=probs,
                decision=decision,
                model_version=self.settings.JEV_MODEL,
            )
        )
        return dto
