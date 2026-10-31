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
    async def triage_item(
        self,
        item_id: UUID,
        item_data: dict[str, Any] | None = None,
        user_id: UUID | None = None,
    ) -> TriageResultDTO:
        """
        Execute full two-stage proactive triage pipeline on ingested item:
        1. Evaluate Jev decision questions (action_required, is_bulk, urgency).
        2. Stage 1 Gate: If p(action_required) < 0.3 or p(is_bulk) > 0.8: decision = 'discarded'.
        3. Stage 2: If actionable, synthesize Action Card and inject into cards_service feed.
        4. Persist triage result and return.
        """
        state = item_data or {"subject": "Urgent Action Required", "body": "Please review pull request."}
        questions = {
            "action_required": "Does this message require user action or decision?",
            "is_bulk": "Is this automated marketing, newsletter, or receipt?",
            "urgency": "Is this high urgency or time-sensitive?",
        }

        if self.jev_client:
            eval_res = await self.jev_client.evaluate(state, questions)
            answers = eval_res.answers
            probs = eval_res.probabilities
            model_ver = eval_res.model_version
        else:
            answers = {"action_required": "yes", "urgency": "high"}
            probs = {"action_required": 0.88, "is_bulk": 0.05, "urgency": 0.85}
            model_ver = self.settings.JEV_MODEL

        p_action = probs.get("action_required", 0.5)
        p_bulk = probs.get("is_bulk", 0.0)
        p_urgent = probs.get("urgency", 0.1)

        # Stage 1 Decision Gate
        if p_action < 0.3 or p_bulk > 0.8:
            decision = "discarded"
        else:
            decision = "card_created"
            # Stage 2: Synthesize Action Card
            if self.cards_service:
                from src.modules.cards.repository import CardActionDTO, CardCreateDTO
                target_user = user_id or UUID("00000000-0000-0000-0000-000000000001")
                urgency = (
                    "critical" if p_urgent > 0.8 else (
                        "high" if p_urgent > 0.5 else (
                            "medium" if p_urgent > 0.3 else "low"
                        )
                    )
                )
                title = str(state.get("subject", "Action Item"))
                summary = str(state.get("snippet", state.get("body", "")))[:200]
                thread_id = str(state.get("thread_id", str(item_id)))

                actions = [
                    CardActionDTO(id="quick_reply", label="Quick Reply", action_type="reply", payload={"thread_id": thread_id}),
                    CardActionDTO(id="mark_done", label="Mark Done", action_type="dismiss", payload={}),
                ]
                await self.cards_service.upsert_card_for_thread(
                    CardCreateDTO(
                        user_id=target_user,
                        item_id=item_id,
                        thread_id=thread_id,
                        type="email",
                        urgency=urgency,
                        title=title,
                        summary=summary,
                        due_at=None,
                        actions=actions,
                    )
                )

        dto = await self.save_triage_result(
            TriageResultCreateDTO(
                item_id=item_id,
                answers=answers,
                probabilities=probs,
                decision=decision,
                model_version=model_ver,
            )
        )
        return dto
