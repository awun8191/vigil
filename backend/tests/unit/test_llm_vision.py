"""Unit tests for the vision path of the LLM client."""
from __future__ import annotations

import base64
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest

from src.config.settings import Settings
from src.integrations.llm.client import LLMClient

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32

Handler = Callable[[httpx.Request], Awaitable[httpx.Response]]


def vision_settings(**overrides: object) -> Settings:
    """Build settings with a configured vision tier and no ambient keys."""
    values: dict[str, object] = {
        "APP_ENV": "development",
        "VISION_LLM_BASE_URL": "https://vision.test/v1",
        "VISION_LLM_API_KEY": "vision-key",
        "VISION_LLM_MODEL": "vision-model",
    }
    values.update(overrides)
    return Settings(**values)


def build_client(handler: Handler) -> tuple[LLMClient, httpx.AsyncClient]:
    """Build an LLM client over a mock transport, plus the transport client."""
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return LLMClient(vision_settings(), client=http_client), http_client


async def test_describe_image_sends_a_data_url_content_part() -> None:
    """The request carries the image as an OpenAI image_url data URL."""
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "A red square."}}]}
        )

    client, http_client = build_client(handler)
    try:
        answer = await client.describe_image(PNG_BYTES, "image/png", "What colour is it?")
    finally:
        await http_client.aclose()

    assert answer == "A red square."
    assert captured["url"] == "https://vision.test/v1/chat/completions"
    assert captured["authorization"] == "Bearer vision-key"

    payload = captured["payload"]
    assert payload["model"] == "vision-model"
    content = payload["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "What colour is it?"}
    expected = base64.b64encode(PNG_BYTES).decode("ascii")
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{expected}"},
    }


async def test_describe_image_defaults_to_a_full_description() -> None:
    """An omitted question asks for a complete description."""
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    client, http_client = build_client(handler)
    try:
        await client.describe_image(PNG_BYTES, "image/png")
    finally:
        await http_client.aclose()

    text = captured["payload"]["messages"][0]["content"][0]["text"]
    assert "Describe this image in detail" in text


async def test_describe_image_falls_back_to_the_fast_tier() -> None:
    """Empty vision settings reuse the fast tier key, URL, and model."""
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    settings = Settings(
        APP_ENV="development",
        VISION_LLM_BASE_URL="",
        VISION_LLM_API_KEY="",
        VISION_LLM_MODEL="",
        FAST_LLM_BASE_URL="https://fast.test/v1",
        FAST_LLM_API_KEY="fast-key",
        FAST_LLM_MODEL="fast-model",
    )
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = LLMClient(settings, client=http_client)
    try:
        await client.describe_image(PNG_BYTES, "image/png")
    finally:
        await http_client.aclose()

    assert captured["url"] == "https://fast.test/v1/chat/completions"
    assert captured["payload"]["model"] == "fast-model"


async def test_describe_image_requires_a_key() -> None:
    """With no key at all, describe_image refuses instead of fabricating an answer."""
    client = LLMClient(
        Settings(
            APP_ENV="development",
            VISION_LLM_API_KEY="",
            FAST_LLM_API_KEY="",
            REASONING_LLM_API_KEY="",
        )
    )

    with pytest.raises(RuntimeError, match="not configured"):
        await client.describe_image(PNG_BYTES, "image/png")


async def test_describe_image_reports_a_provider_failure() -> None:
    """A provider error becomes a RuntimeError with the cause."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "image too large"})

    client, http_client = build_client(handler)
    try:
        with pytest.raises(RuntimeError, match="vision request failed"):
            await client.describe_image(PNG_BYTES, "image/png")
    finally:
        await http_client.aclose()
