"""Voice transcription router factory integrating Groq Whisper STT with deterministic fallback."""
from __future__ import annotations

import logging
from typing import Any
from fastapi import APIRouter, Depends, File, UploadFile, status
import httpx

from src.config.exceptions import BadGatewayError, ServiceUnavailableError
from src.config.settings import Settings
from src.dependencies import get_current_user, get_key_vault_service
from src.modules.auth.repository import UserDTO
from src.modules.settings.vault import KeyVaultService

logger = logging.getLogger("vigil.voice")

GROQ_API_KEY_NAME = "GROQ_API_KEY"


async def _call_groq_whisper(
    url: str,
    headers: dict[str, str],
    files: dict[str, Any],
    data: dict[str, Any],
) -> tuple[int, dict[str, Any] | str]:
    """Execute HTTP POST to Groq Whisper API endpoint."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers=headers, files=files, data=data)
        if resp.status_code == 200:
            return resp.status_code, resp.json()
        return resp.status_code, resp.text


def build_voice_router(settings: Settings) -> APIRouter:
    """Build and configure voice APIRouter instance."""
    router = APIRouter(prefix="/voice", tags=["Voice"])

    @router.post(
        "/transcribe",
        status_code=status.HTTP_200_OK,
        summary="Transcribe Audio File",
    )
    async def transcribe(
        file: UploadFile = File(...),
        user: UserDTO = Depends(get_current_user),
        vault: KeyVaultService = Depends(get_key_vault_service),
    ) -> dict[str, Any]:
        """
        Transcribe uploaded audio file using Groq Whisper API (whisper-large-v3-turbo).

        The key is read from the user's vault first, then from the process
        environment. With no key the endpoint returns a deterministic mock.
        With a key it either returns the transcript or a real upstream error:
        a rejected key must not look like a successful transcription.
        """
        file_bytes = await file.read()

        vault_key = await vault.get_secret(user.id, GROQ_API_KEY_NAME)
        api_key = vault_key or settings.GROQ_API_KEY

        if not api_key:
            return {
                "text": "Simulated voice transcription test audio",
                "model": "whisper-large-v3-turbo-mock",
                "mock": True,
            }

        transcribe_url = f"{settings.GROQ_BASE_URL.rstrip('/')}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {api_key}"}
        files = {
            "file": (
                file.filename or "audio.webm",
                file_bytes,
                file.content_type or "audio/webm",
            )
        }
        data = {"model": "whisper-large-v3-turbo", "response_format": "json"}

        try:
            status_code, resp_data = await _call_groq_whisper(
                transcribe_url, headers, files, data
            )
        except httpx.TimeoutException as exc:
            raise ServiceUnavailableError(
                "Groq Whisper timed out. Try a shorter recording.",
                code="GROQ_TIMEOUT",
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning("Groq Whisper API request failed: %s", exc)
            raise BadGatewayError(
                "Could not reach Groq Whisper. Check the backend's network access.",
                code="GROQ_UNREACHABLE",
            ) from exc

        if status_code == 200 and isinstance(resp_data, dict):
            return {
                "text": resp_data.get("text", ""),
                "model": "whisper-large-v3-turbo",
                "mock": False,
            }

        logger.warning(
            "Groq Whisper API returned status %d: %s",
            status_code,
            str(resp_data)[:200],
        )

        if status_code in (401, 403):
            raise BadGatewayError(
                "Groq rejected the API key. Update GROQ_API_KEY in Settings → Connectors.",
                code="GROQ_AUTH_FAILED",
            )

        raise BadGatewayError(
            f"Groq Whisper returned HTTP {status_code}.",
            code="GROQ_UPSTREAM_ERROR",
        )

    return router
