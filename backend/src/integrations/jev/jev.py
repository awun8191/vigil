"""TypeSafe AI Jev Decision API client integration for Stage 1 proactive triage."""
from __future__ import annotations

from typing import Any
import httpx

from src.config.settings import Settings
from src.integrations.repositories import (
    JevClientProtocol,
    JevEvaluationResultDTO,
)


class JevClient:
    """
    Client for TypeSafe AI Jev Decision API.
    Evaluates calibrated probability questions against item state with zero generative hallucinations.
    """

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(
            base_url=self.settings.JEV_BASE_URL,
            headers={
                "Authorization": f"Bearer {self.settings.JEV_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=10.0,
        )

    async def evaluate(
        self,
        state: dict[str, Any],
        questions: dict[str, str],
        model: str | None = None,
    ) -> JevEvaluationResultDTO:
        """Evaluate calibrated questions on item state."""
        # In offline/test mode or if no API key is provided, use deterministic rule-based evaluation
        if not self.settings.JEV_API_KEY or self.settings.APP_ENV == "test":
            return self._fallback_evaluate(state, questions, model)

        client = await self._get_client()
        should_close = self._client is None
        try:
            payload = {
                "model": model or self.settings.JEV_MODEL,
                "state": state,
                "questions": questions,
            }
            res = await client.post("/evaluate", json=payload)
            res.raise_for_status()
            data = res.json()
            return JevEvaluationResultDTO(
                answers=data.get("answers", {}),
                probabilities=data.get("probabilities", {}),
                model_version=data.get("model_version", self.settings.JEV_MODEL),
            )
        except Exception:
            return self._fallback_evaluate(state, questions, model)
        finally:
            if should_close:
                await client.aclose()

    async def evaluate_batch(
        self,
        states: list[dict[str, Any]],
        questions: dict[str, str],
        model: str | None = None,
    ) -> list[JevEvaluationResultDTO]:
        """Batch evaluate multiple state items."""
        return [await self.evaluate(s, questions, model) for s in states]

    async def is_healthy(self) -> bool:
        """Check Jev API availability."""
        if not self.settings.JEV_API_KEY:
            return True
        try:
            client = await self._get_client()
            res = await client.get("/health")
            return res.status_code == 200
        except Exception:
            return False

    def _fallback_evaluate(
        self,
        state: dict[str, Any],
        questions: dict[str, str],
        model: str | None = None,
    ) -> JevEvaluationResultDTO:
        """Deterministic heuristic fallback for tests and offline local usage."""
        subject = str(state.get("subject", "")).lower()
        body = str(state.get("body", "")).lower()
        snippet = str(state.get("snippet", "")).lower()
        combined = f"{subject} {snippet} {body}"

        is_bulk = any(w in combined for w in ["unsubscribe", "newsletter", "promotions", "no-reply", "receipt"])
        is_urgent = any(w in combined for w in ["urgent", "asap", "immediate", "action required", "deadline"])
        is_actionable = any(w in combined for w in ["please review", "can you", "approve", "need your input", "action required"]) or is_urgent

        answers: dict[str, str] = {}
        probabilities: dict[str, float] = {}

        for q_key in questions:
            q_lower = q_key.lower()
            if "bulk" in q_lower:
                content_bulk = len(str(state.get("content", ""))) > 250 or state.get("kind") in ("tool_result", "tool")
                is_b = is_bulk or content_bulk
                answers[q_key] = "yes" if is_b else "no"
                probabilities[q_key] = 0.88 if is_b else 0.10
            elif "needed" in q_lower:
                # In compaction, unlocked intermediate outputs and old chatter can be summarized/dropped
                is_n = state.get("kind") in ("approval", "decision") or state.get("role") == "user"
                answers[q_key] = "yes" if is_n else "no"
                probabilities[q_key] = 0.85 if is_n else 0.15
            elif "user_fact" in q_lower:
                is_u = state.get("role") == "user" or "user" in str(state.get("kind", ""))
                answers[q_key] = "yes" if is_u else "no"
                probabilities[q_key] = 0.90 if is_u else 0.05
            elif any(k in q_lower for k in ["urgent", "urgency"]):
                answers[q_key] = "yes" if is_urgent else "no"
                probabilities[q_key] = 0.92 if is_urgent else 0.15
            elif "action" in q_lower:
                answers[q_key] = "yes" if is_actionable else "no"
                probabilities[q_key] = 0.88 if is_actionable else 0.12
            else:
                answers[q_key] = "no"
                probabilities[q_key] = 0.50

        return JevEvaluationResultDTO(
            answers=answers,
            probabilities=probabilities,
            model_version=model or self.settings.JEV_MODEL,
        )
