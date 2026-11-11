"""Provider credential validators used by the vault test endpoints.

A validator proves a key works against the provider that owns it. It never
stores the value and never returns it. The response always reports what
happened: ``valid`` is the provider's verdict, not an exception.
"""
from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field

from src.config.settings import Settings
from src.modules.settings.vault import normalize_key_name, normalize_secret_value

GROQ_API_KEY = "GROQ_API_KEY"


class ValidateSecretRequest(BaseModel):
    """Request payload to validate a key before storing it."""

    key: str = Field(..., min_length=1, description="Secret name/identifier")
    value: str = Field(..., min_length=1, description="Plaintext value to validate")


class VaultValidationResponse(BaseModel):
    """Result of one provider check. A bad key is a report, not a server error."""

    key_name: str
    provider: str
    valid: bool
    status_code: int | None = None
    message: str


async def validate_api_key(
    key: str,
    value: str,
    settings: Settings,
) -> VaultValidationResponse:
    """Validate one key against its provider, if a validator exists."""
    try:
        key_name = normalize_key_name(key)
    except ValueError as exc:
        return VaultValidationResponse(
            key_name=key.strip(),
            provider="unknown",
            valid=False,
            message=str(exc),
        )
    normalized_value = normalize_secret_value(key_name, value)

    if key_name.upper() == GROQ_API_KEY:
        return await _validate_groq(key_name, normalized_value, settings)

    return VaultValidationResponse(
        key_name=key_name,
        provider="unknown",
        valid=False,
        message=f"No key validator is configured for {key_name!r} yet.",
    )


async def _validate_groq(
    key_name: str,
    value: str,
    settings: Settings,
) -> VaultValidationResponse:
    """Check a Groq key against the OpenAI-compatible models endpoint."""
    if not value:
        return VaultValidationResponse(
            key_name=key_name,
            provider="groq",
            valid=False,
            message="The Groq key is empty.",
        )

    url = f"{settings.GROQ_BASE_URL.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {value}"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return VaultValidationResponse(
            key_name=key_name,
            provider="groq",
            valid=False,
            message=f"Could not reach Groq: {exc}",
        )

    if response.status_code == 200:
        return VaultValidationResponse(
            key_name=key_name,
            provider="groq",
            valid=True,
            status_code=200,
            message="Groq accepted the key.",
        )

    if response.status_code in (401, 403):
        return VaultValidationResponse(
            key_name=key_name,
            provider="groq",
            valid=False,
            status_code=response.status_code,
            message=(
                "Groq rejected the key "
                f"(HTTP {response.status_code}). Check that it is a Groq API key."
            ),
        )

    detail = _short_detail(response)
    return VaultValidationResponse(
        key_name=key_name,
        provider="groq",
        valid=False,
        status_code=response.status_code,
        message=f"Groq returned HTTP {response.status_code}.{detail}",
    )


def _short_detail(response: httpx.Response) -> str:
    """Return a short, non-secret error detail from a provider response."""
    try:
        payload: Any = response.json()
    except ValueError:
        return ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return f" {message[:200]}"
        if isinstance(error, str) and error:
            return f" {error[:200]}"
    return ""
