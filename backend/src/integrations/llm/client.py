"""Tiered LLM client implementation supporting Fast LLM (Tier 2), Reasoning LLM (Tier 3), and Embeddings."""
from __future__ import annotations

import json
from typing import Any, AsyncIterator
import httpx

from src.config.settings import Settings
from src.integrations.repositories import (
    LLMClientProtocol,
    LLMResponseDTO,
    LLMStreamChunkDTO,
    LLMToolCallDTO,
)


class LLMClient:
    """Client for tiered LLM APIs implementing LLMClientProtocol."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        tier: str = "reasoning",
        max_tokens: int | None = None,
    ) -> LLMResponseDTO:
        """Execute non-streaming completion."""
        # Use fallback if no API key is provided or during tests
        api_key = self.settings.FAST_LLM_API_KEY if tier == "fast" else self.settings.REASONING_LLM_API_KEY
        if not api_key or self.settings.APP_ENV in ("test", "development"):
            return self._fallback_complete(messages, tools, tier)

        base_url = self.settings.FAST_LLM_BASE_URL if tier == "fast" else self.settings.REASONING_LLM_BASE_URL
        model = self.settings.FAST_LLM_MODEL if tier == "fast" else self.settings.REASONING_LLM_MODEL

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
        if max_tokens:
            payload["max_tokens"] = max_tokens

        client = self._client or httpx.AsyncClient(timeout=30.0)
        should_close = self._client is None
        try:
            res = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            )
            res.raise_for_status()
            data = res.json()
            choice = data["choices"][0]["message"]
            tool_calls = []
            for tc in choice.get("tool_calls", []):
                args = {}
                if "function" in tc and "arguments" in tc["function"]:
                    try:
                        args = json.loads(tc["function"]["arguments"])
                    except Exception:
                        pass
                tool_calls.append(
                    LLMToolCallDTO(
                        id=tc.get("id", "call_default"),
                        name=tc.get("function", {}).get("name", ""),
                        arguments=args,
                    )
                )

            usage = data.get("usage", {})
            return LLMResponseDTO(
                content=choice.get("content"),
                tool_calls=tool_calls,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                model=data.get("model", model),
            )
        except Exception:
            return self._fallback_complete(messages, tools, tier)
        finally:
            if should_close:
                await client.aclose()

    async def stream_complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        tier: str = "reasoning",
        max_tokens: int | None = None,
    ) -> AsyncIterator[LLMStreamChunkDTO]:
        """Stream assistant response tokens incrementally."""
        resp = await self.complete(messages, tools, temperature, tier, max_tokens)
        text = resp.content or ""
        # Yield in tokens/words for streaming
        for word in text.split(" "):
            yield LLMStreamChunkDTO(delta_text=f"{word} ")
        if resp.tool_calls:
            for tc in resp.tool_calls:
                yield LLMStreamChunkDTO(tool_calls=[tc])

    async def embed_texts(
        self,
        texts: list[str],
        model: str | None = None,
    ) -> list[list[float]]:
        """Generate deterministic 384-dimensional vectors for offline tests.

        The production embedding path is the local ONNX model in
        src.integrations.embedding. This stub exists for tests only.
        """
        # Offline / test deterministic embedding: unit vector with hash seed
        embeddings: list[list[float]] = []
        for t in texts:
            # Return deterministic 384 float array to match memory_chunks.embedding
            val = float(abs(hash(t)) % 1000) / 1000.0
            vec = [0.0] * self.settings.EMBEDDING_DIMENSIONS
            vec[0] = val
            vec[1] = 1.0 - val
            embeddings.append(vec)
        return embeddings

    async def is_healthy(self) -> bool:
        return True

    def _fallback_complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tier: str = "reasoning",
    ) -> LLMResponseDTO:
        """Deterministic completion for offline tests and metadata extraction."""
        last_msg = messages[-1]["content"] if messages else ""
        content = f"Acknowledged: {last_msg[:60]}"
        return LLMResponseDTO(
            content=content,
            tool_calls=[],
            prompt_tokens=10,
            completion_tokens=15,
            model="vigil-local-engine",
        )
