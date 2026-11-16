"""Triage service implementing two-stage proactive classification pipeline."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any, ClassVar
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.integrations.repositories import JevClientProtocol, LLMClientProtocol
from src.modules.cards.repository import CardActionDTO, CardCreateDTO, CardsRepository
from src.modules.triage.repository import TriageResultCreateDTO, TriageResultDTO
from src.schemas.triage import TriageDecisionModel


class TriageService:
    """
    Two-stage proactive triage service satisfying TriageRepository via duck typing.
    Stage 1: TypeSafe AI Jev decision probability gate (zero generative spend).
    Stage 2: Fast LLM (Tier 2) Hark Pro metadata & pre-draft extractor.
    """

    _triage_results: ClassVar[dict[UUID, TriageResultDTO]] = {}
    _item_to_result: ClassVar[dict[UUID, UUID]] = {}

    def __init__(
        self,
        session: AsyncSession | None,
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

    def _is_db_session_active(self) -> bool:
        return isinstance(self.session, AsyncSession)

    @staticmethod
    def _to_dto(model: TriageDecisionModel) -> TriageResultDTO:
        return TriageResultDTO(
            id=model.id,
            item_id=model.item_id,
            answers=dict(model.answers or {}),
            probabilities=dict(model.probabilities or {}),
            decision=model.decision,
            model_version=model.model_version,
            created_at=model.created_at,
        )

    async def save_triage_result(self, data: TriageResultCreateDTO) -> TriageResultDTO:
        """Persist triage gate answers and probabilities."""
        result_id = uuid4()
        now = datetime.now(timezone.utc)
        if self._is_db_session_active():
            assert self.session is not None
            stmt = (
                pg_insert(TriageDecisionModel)
                .values(
                    id=result_id,
                    item_id=data.item_id,
                    answers=dict(data.answers),
                    probabilities=dict(data.probabilities),
                    decision=data.decision,
                    model_version=data.model_version,
                    created_at=now,
                )
                .on_conflict_do_update(
                    constraint="uq_triage_decisions_item_model",
                    set_={
                        "answers": dict(data.answers),
                        "probabilities": dict(data.probabilities),
                        "decision": data.decision,
                    },
                )
                .returning(TriageDecisionModel.id)
            )
            persisted_id = (await self.session.execute(stmt)).scalar_one()
            await self.session.flush()
            model = await self.session.get(TriageDecisionModel, persisted_id)
            if model is not None:
                return self._to_dto(model)

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
        if self._is_db_session_active():
            assert self.session is not None
            res = await self.session.execute(
                select(TriageDecisionModel)
                .where(TriageDecisionModel.item_id == item_id)
                .order_by(TriageDecisionModel.created_at.desc())
                .limit(1)
            )
            model = res.scalars().first()
            return None if model is None else self._to_dto(model)

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
        if self._is_db_session_active():
            assert self.session is not None
            res = await self.session.execute(
                select(TriageDecisionModel)
                .order_by(TriageDecisionModel.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return [self._to_dto(m) for m in res.scalars().all()]

        results = list(self._triage_results.values())
        return results[offset : offset + limit]

    async def _synthesize_stage2_with_llm(
        self, state: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Use Fast LLM (Tier 2) to synthesize Hark Pro observation, verb, and pre-drafted reply."""
        if self.llm_client is None:
            return None
        subject = str(state.get("subject") or "")
        sender = str(state.get("sender") or "")
        body = str(state.get("body") or state.get("snippet") or "")[:3000]
        prompt = (
            "You are a proactive executive assistant (Hark Pro style). "
            "Analyze this incoming message and return ONLY valid JSON with keys:\n"
            '- "verb": one short active verb ("Draft", "Reply", "Accept", "RSVP", "Review", "Pay", "Schedule")\n'
            '- "proactivity_tag": one of ("Urgent Action", "Decision Needed", "Meeting Invite", "Follow Up")\n'
            '- "title": concise actionable observation (max 90 chars, e.g. "Reply to Boardy about Tuesday 9 AM intro")\n'
            '- "summary": 1-2 sentence executive context\n'
            '- "urgency": one of ("critical", "high", "medium", "low")\n'
            '- "suggested_draft": null OR {"to": ["..."], "subject": "Re: ...", "body": "Rich markdown reply draft..."}\n\n'
            f"From: {sender}\nSubject: {subject}\nBody:\n{body}"
        )
        try:
            resp = await self.llm_client.complete(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
                tier="fast",
                max_tokens=700,
            )
            raw_text = (resp.content or "").strip()
            if not raw_text or raw_text == "Mock LLM completion":
                return None
            match = re.search(r"\{.*\}", raw_text, re.DOTALL)
            if not match:
                return None
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

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
        3. Stage 2: If actionable, synthesize Action Card (with optional pre-draft) and inject into cards_service feed.
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
                target_user = user_id or UUID("00000000-0000-0000-0000-000000000001")
                urgency = (
                    "critical" if p_urgent > 0.8 else (
                        "high" if p_urgent > 0.5 else (
                            "medium" if p_urgent > 0.3 else "low"
                        )
                    )
                )
                title = str(state.get("subject", "Action Item"))
                sender = str(state.get("sender", ""))
                summary = str(state.get("snippet", state.get("body", "")))[:200]
                thread_id = str(state.get("thread_id", str(item_id)))
                external_id = str(state.get("external_id", state.get("id", "")))

                proactivity_tag = (
                    "Urgent Action" if p_urgent > 0.8 else (
                        "Decision Needed" if p_action > 0.8 else "Autonomous Observation"
                    )
                )
                primary_verb = "Review" if state.get("type") != "calendar" else "Prepare"

                synth = await self._synthesize_stage2_with_llm(state)
                suggested_draft: dict[str, Any] | None = None
                if synth:
                    primary_verb = str(synth.get("verb") or primary_verb)[:32]
                    proactivity_tag = str(synth.get("proactivity_tag") or proactivity_tag)[:64]
                    title = str(synth.get("title") or title)[:250]
                    summary = str(synth.get("summary") or summary)[:400]
                    if synth.get("urgency") in ("critical", "high", "medium", "low"):
                        urgency = str(synth["urgency"])
                    if isinstance(synth.get("suggested_draft"), dict):
                        suggested_draft = synth["suggested_draft"]

                seed_text = (
                    f"Help me handle this email from {sender or 'sender'} "
                    f"regarding '{title}' (thread_id: {thread_id})."
                )
                if synth:
                    draft_hint = (
                        f" Use this pre-drafted starting point if helpful: {json.dumps(suggested_draft)}"
                        if suggested_draft
                        else ""
                    )
                    actions = [
                        CardActionDTO(
                            id="primary_run",
                            label=f"{primary_verb} Now",
                            action_type="run",
                            verb=primary_verb,
                            payload={
                                "initial_prompt": (
                                    f"Read Gmail thread {thread_id} (message {external_id}, subject '{title}' from {sender}) "
                                    f"and prepare the appropriate action or rich-formatted reply draft for my review.{draft_hint}"
                                ),
                                "thread_id": thread_id,
                                "suggested_draft": suggested_draft,
                            },
                        ),
                        CardActionDTO(
                            id="quick_reply",
                            label="Quick Reply",
                            action_type="reply",
                            verb="Reply",
                            payload={
                                "thread_id": thread_id,
                                "seed": seed_text,
                                "suggested_draft": suggested_draft,
                            },
                        ),
                        CardActionDTO(
                            id="mark_done",
                            label="Mark Done",
                            action_type="dismiss",
                            verb="Dismiss",
                            payload={},
                        ),
                    ]
                else:
                    actions = [
                        CardActionDTO(
                            id="quick_reply",
                            label="Quick Reply",
                            action_type="reply",
                            verb="Reply",
                            payload={"thread_id": thread_id, "seed": seed_text},
                        ),
                        CardActionDTO(
                            id="mark_done",
                            label="Mark Done",
                            action_type="dismiss",
                            verb="Dismiss",
                            payload={},
                        ),
                    ]

                await self.cards_service.upsert_card_for_thread(
                    CardCreateDTO(
                        user_id=target_user,
                        item_id=item_id,
                        thread_id=thread_id,
                        type="email",
                        urgency=urgency,
                        proactivity_tag=proactivity_tag,
                        verb=primary_verb,
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
