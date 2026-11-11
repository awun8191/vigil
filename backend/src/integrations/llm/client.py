"""Tiered LLM client implementation supporting Fast LLM (Tier 2), Reasoning LLM (Tier 3), and Embeddings."""
from __future__ import annotations

import base64
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
        # The fallback is for a missing key and for the test suite. A developer
        # who sets a real key in a development .env must reach the provider.
        api_key = self.settings.FAST_LLM_API_KEY if tier == "fast" else self.settings.REASONING_LLM_API_KEY
        if not api_key or self.settings.APP_ENV == "test":
            return self._fallback_complete(messages, tools, tier)

        base_url = (
            self.settings.FAST_LLM_BASE_URL if tier == "fast" else self.settings.REASONING_LLM_BASE_URL
        ).rstrip("/")
        model = self.settings.FAST_LLM_MODEL if tier == "fast" else self.settings.REASONING_LLM_MODEL

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
        if max_tokens:
            # Reasoning models (e.g., muse-spark) count reasoning tokens toward max_tokens.
            # Ensure a floor so reasoning tokens do not truncate the final response to null.
            effective_max_tokens = max(max_tokens, 1024) if "muse-spark" in model else max_tokens
            payload["max_tokens"] = effective_max_tokens

        client = self._client or httpx.AsyncClient(timeout=60.0)
        should_close = self._client is None
        try:
            res = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "vigil-agent/1.0",
                },
                json=payload,
            )
            res.raise_for_status()
            data = res.json()
            choice = data["choices"][0]["message"]
            tool_calls = []
            for tc in choice.get("tool_calls") or []:
                args = {}
                if "function" in tc and "arguments" in tc["function"]:
                    raw_args = tc["function"]["arguments"]
                    if isinstance(raw_args, dict):
                        args = raw_args
                    elif isinstance(raw_args, str):
                        try:
                            args = json.loads(raw_args)
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
        """Stream assistant response tokens incrementally.

        Calls the provider with ``stream=true`` and yields one chunk per
        ``delta.content`` fragment. Tool call fragments accumulate internally and
        are emitted once, complete, on the final chunk. Usage rides the last
        chunk when the provider sends ``stream_options.include_usage``.
        """
        api_key = self.settings.FAST_LLM_API_KEY if tier == "fast" else self.settings.REASONING_LLM_API_KEY
        if not api_key or self.settings.APP_ENV == "test":
            resp = self._fallback_complete(messages, tools, tier)
            text = resp.content or ""
            for word in text.split(" "):
                yield LLMStreamChunkDTO(delta_content=f"{word} ", tool_call_chunks=None, done=False, finish_reason=None, usage=None)
            yield LLMStreamChunkDTO(delta_content="", tool_call_chunks=None, done=True, finish_reason="stop", usage=None)
            return

        base_url = (
            self.settings.FAST_LLM_BASE_URL if tier == "fast" else self.settings.REASONING_LLM_BASE_URL
        ).rstrip("/")
        model = self.settings.FAST_LLM_MODEL if tier == "fast" else self.settings.REASONING_LLM_MODEL

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = tools
        if max_tokens:
            # Reasoning models (e.g., muse-spark) count reasoning tokens toward max_tokens.
            # Ensure a floor so reasoning tokens do not truncate the final response to null.
            effective_max_tokens = max(max_tokens, 1024) if "muse-spark" in model else max_tokens
            payload["max_tokens"] = effective_max_tokens

        client = self._client or httpx.AsyncClient(timeout=120.0)
        own_client = self._client is None
        call_fragments: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] = {}
        finish_reason = None
        try:
            async with client.stream(
                "POST",
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "vigil-agent/1.0",
                },
                json=payload,
            ) as res:
                res.raise_for_status()
                async for line in res.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if not data or data == "[DONE]":
                        continue
                    event = json.loads(data)
                    if event.get("usage"):
                        usage = event["usage"]
                    choice = (event.get("choices") or [{}])[0]
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
                    delta = choice.get("delta") or {}
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        yield LLMStreamChunkDTO(delta_content=text, tool_call_chunks=None, done=False, finish_reason=None, usage=None)
                    for fragment in delta.get("tool_calls") or []:
                        index = int(fragment.get("index", 0))
                        slot = call_fragments.setdefault(
                            index, {"id": "", "name": "", "arguments": ""}
                        )
                        if fragment.get("id"):
                            slot["id"] = fragment["id"]
                        fn = fragment.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if "arguments" in fn:
                            slot["arguments"] += str(fn["arguments"])
            for try_tool_call in sorted(call_fragments):
                slot = call_fragments[try_tool_call]
                raw_args = slot["arguments"]
                args: dict[str, Any]
                if isinstance(raw_args, dict):
                    args = raw_args
                else:
                    try:
                        args = json.loads(raw_args)
                    except Exception:
                        args = {}
                parsed = LLMToolCallDTO(
                    id=slot["id"] or f"call_{try_tool_call}",
                    name=slot["name"],
                    arguments=args,
                )
                yield LLMStreamChunkDTO(delta_content="", done=False, finish_reason=finish_reason, tool_call_chunks=[parsed.model_dump(mode='json')])
            yield LLMStreamChunkDTO(
                delta_content="",
                done=True,
                finish_reason=finish_reason,
                tool_call_chunks=None,
                usage=usage,
            )
        finally:
            if own_client:
                await client.aclose()

    async def describe_image(
        self,
        image_bytes: bytes,
        media_type: str,
        question: str | None = None,
        max_tokens: int = 1024,
    ) -> str:
        """Answer a question about one image through a vision capable model.

        Args:
            image_bytes: Raw image bytes.
            media_type: The image MIME type, such as ``image/png``.
            question: What to extract or answer. Defaults to a full description.
            max_tokens: Generation cap for the answer.

        Returns:
            The assistant text. An empty provider answer returns an empty string.

        Raises:
            RuntimeError: The vision tier has no key, or the provider call fails.
        """
        api_key = (
            self.settings.VISION_LLM_API_KEY
            or self.settings.FAST_LLM_API_KEY
            or self.settings.REASONING_LLM_API_KEY
        )
        if not api_key or self.settings.APP_ENV == "test":
            raise RuntimeError(
                "the vision model is not configured; set VISION_LLM_API_KEY "
                "or FAST_LLM_API_KEY to view images"
            )

        base_url = (
            self.settings.VISION_LLM_BASE_URL
            or self.settings.FAST_LLM_BASE_URL
        ).rstrip("/")
        model = self.settings.VISION_LLM_MODEL or self.settings.FAST_LLM_MODEL
        prompt = question or (
            "Describe this image in detail. Cover the visible content, any text, "
            "the layout, and anything a user would need to act on it."
        )
        data_url = (
            f"data:{media_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        )
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }

        client = self._client or httpx.AsyncClient(timeout=120.0)
        should_close = self._client is None
        try:
            res = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "vigil-agent/1.0",
                },
                json=payload,
            )
            res.raise_for_status()
            choice = res.json()["choices"][0]["message"]
            content = choice.get("content")
            return content if isinstance(content, str) else ""
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise RuntimeError(f"the vision request failed: {exc}") from exc
        finally:
            if should_close:
                await client.aclose()

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
