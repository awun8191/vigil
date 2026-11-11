"""Unit tests for Voice transcription router and Groq Whisper STT integration."""
from __future__ import annotations

from datetime import datetime, timezone
import io
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from httpx import ASGITransport
import httpx
import pytest

from src.config.settings import Settings
from src.dependencies import get_current_user, get_key_vault_service
from src.main import create_app
from src.modules.auth.repository import UserDTO
from src.modules.settings.vault import KeyVaultService, InMemoryVaultStore

AUTH_HEADERS = {"Authorization": "Bearer dev"}


def isolate_vault(app) -> None:
    """Keep the dev database vault out of a test that assumes no stored key."""
    app.dependency_overrides[get_key_vault_service] = lambda: KeyVaultService(
        settings=Settings(), store=InMemoryVaultStore()
    )


def _voice_files(name: str = "test_recording.webm", content: bytes = b"RIFF....WAVEfmt ....data...."):
    return {"file": (name, io.BytesIO(content), "audio/webm")}


@pytest.mark.asyncio
async def test_transcribe_fallback_without_groq_key() -> None:
    """Verify POST /api/v1/voice/transcribe returns deterministic mock when no key is configured."""
    app = create_app(Settings(GROQ_API_KEY=None))
    isolate_vault(app)
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/v1/voice/transcribe", files=_voice_files(), headers=AUTH_HEADERS
        )
        assert res.status_code == 200
        data = res.json()
        assert data["text"] == "Simulated voice transcription test audio"
        assert data["model"] == "whisper-large-v3-turbo-mock"
        assert data["mock"] is True


@pytest.mark.asyncio
async def test_transcribe_groq_api_success() -> None:
    """Verify the env key is used and a 200 body is returned unchanged."""
    custom_settings = Settings(
        GROQ_API_KEY="gsk_test_groq_key_12345",
        GROQ_BASE_URL="https://api.groq.com/openai/v1",
    )
    app = create_app(custom_settings)
    isolate_vault(app)
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    fake_result = (200, {"text": "Schedule a meeting with Sarah tomorrow at 10 AM"})

    with patch(
        "src.modules.voice.router._call_groq_whisper",
        new_callable=AsyncMock,
        return_value=fake_result,
    ) as mock_call:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(
                "/api/v1/voice/transcribe",
                files=_voice_files("voice_note.webm", b"webm-voice-bytes"),
                headers=AUTH_HEADERS,
            )
            assert res.status_code == 200
            data = res.json()
            assert data["text"] == "Schedule a meeting with Sarah tomorrow at 10 AM"
            assert data["model"] == "whisper-large-v3-turbo"
            assert data["mock"] is False
            assert mock_call.called
            assert mock_call.call_args.args[1]["Authorization"] == (
                "Bearer gsk_test_groq_key_12345"
            )


@pytest.mark.asyncio
async def test_transcribe_uses_vault_key_when_env_is_unset() -> None:
    """A key saved in the vault is used even when GROQ_API_KEY is unset."""
    custom_settings = Settings(GROQ_API_KEY=None)
    app = create_app(custom_settings)
    isolate_vault(app)
    user_id = uuid4()
    now = datetime.now(timezone.utc)
    mock_user = UserDTO(
        id=user_id,
        display_name="Vault User",
        settings={},
        created_at=now,
        updated_at=now,
    )
    vault = KeyVaultService(settings=custom_settings, store=InMemoryVaultStore())
    await vault.set_secret(user_id, "GROQ_API_KEY", "gsk_vault_key_12345")

    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_key_vault_service] = lambda: vault

    fake_result = (200, {"text": "transcribed with the vault key"})
    with patch(
        "src.modules.voice.router._call_groq_whisper",
        new_callable=AsyncMock,
        return_value=fake_result,
    ) as mock_call:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(
                "/api/v1/voice/transcribe", files=_voice_files(), headers=AUTH_HEADERS
            )

    assert res.status_code == 200
    assert res.json()["text"] == "transcribed with the vault key"
    assert res.json()["mock"] is False
    assert mock_call.call_args.args[1]["Authorization"] == "Bearer gsk_vault_key_12345"


@pytest.mark.asyncio
async def test_transcribe_upstream_401_is_a_real_error() -> None:
    """A rejected key must not be disguised as a successful mock transcription."""
    custom_settings = Settings(
        GROQ_API_KEY="gsk_failing_key",
        GROQ_BASE_URL="https://api.groq.com/openai/v1",
    )
    app = create_app(custom_settings)
    isolate_vault(app)
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    with patch(
        "src.modules.voice.router._call_groq_whisper",
        new_callable=AsyncMock,
        return_value=(401, {"error": {"message": "Invalid API Key"}}),
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(
                "/api/v1/voice/transcribe", files=_voice_files(), headers=AUTH_HEADERS
            )

    assert res.status_code == 502
    body = res.json()
    assert body["code"] == "GROQ_AUTH_FAILED"
    assert "rejected" in body["message"].lower()
    assert "mock" not in body


@pytest.mark.asyncio
async def test_transcribe_upstream_500_is_a_real_error() -> None:
    """Other upstream failures also surface instead of returning a fake transcript."""
    custom_settings = Settings(
        GROQ_API_KEY="gsk_configured_key",
        GROQ_BASE_URL="https://api.groq.com/openai/v1",
    )
    app = create_app(custom_settings)
    isolate_vault(app)
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    with patch(
        "src.modules.voice.router._call_groq_whisper",
        new_callable=AsyncMock,
        return_value=(500, "upstream exploded"),
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(
                "/api/v1/voice/transcribe", files=_voice_files(), headers=AUTH_HEADERS
            )

    assert res.status_code == 502
    assert res.json()["code"] == "GROQ_UPSTREAM_ERROR"


@pytest.mark.asyncio
async def test_transcribe_missing_file_returns_422() -> None:
    """Verify POST /api/v1/voice/transcribe without a file produces 422 Unprocessable Entity."""
    app = create_app()
    isolate_vault(app)
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/v1/voice/transcribe", json={}, headers=AUTH_HEADERS
        )
        assert res.status_code == 422
