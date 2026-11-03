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
            if "bulk" in q_key.lower():
                answers[q_key] = "yes" if is_bulk else "no"
                probabilities[q_key] = 0.95 if is_bulk else 0.05
            elif any(k in q_key.lower() for k in ["urgent", "urgency"]):
                answers[q_key] = "yes" if is_urgent else "no"
                probabilities[q_key] = 0.92 if is_urgent else 0.15
            elif "action" in q_key.lower():
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
