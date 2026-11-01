"""Regression tests for the removed vgt_test bearer-token prefix bypass (V-03)."""
from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport
from pydantic import ValidationError

from src.config.exceptions import UnauthorizedError
from src.config.settings import DEFAULT_SECRET_KEY, Settings
from src.main import create_app
from src.modules.auth.service import AuthService


def _client(app) -> httpx.AsyncClient:
    """Return an ASGI client that surfaces server exceptions as responses."""
    return httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )


@pytest.mark.asyncio
async def test_prefix_token_is_rejected() -> None:
    """A bearer token that only shares the vgt_test prefix must get 401.

    APP_ENV is development, the exact environment where the bypass used to fire.
    """
    app = create_app(Settings(APP_ENV="development"))
    async with _client(app) as client:
        res = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer vgt_test_anything"},
        )

    assert res.status_code == 401
    assert res.json()["code"] == "INVALID_TOKEN"


@pytest.mark.asyncio
async def test_seeded_device_token_is_accepted(seeded_device_token: str) -> None:
    """A device seeded by the test fixture under APP_ENV=test authenticates."""
    app = create_app(Settings(APP_ENV="test"))
    async with _client(app) as client:
        res = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {seeded_device_token}"},
        )

    assert res.status_code == 200
    assert res.json()["device_name"] == "Test Device"


@pytest.mark.asyncio
async def test_service_rejects_prefix_token_in_development() -> None:
    """The service layer rejects the prefix token without any HTTP layer."""
    service = AuthService(session=None, settings=Settings(APP_ENV="development"))
    with pytest.raises(UnauthorizedError):
        await service.authenticate_token("vgt_test_anything")


def test_seeding_requires_test_env() -> None:
    """The seeding entry point refuses to run outside APP_ENV=test."""
    service = AuthService(session=None, settings=Settings(APP_ENV="development"))
    with pytest.raises(RuntimeError, match="APP_ENV='test'"):
        service.seed_test_devices()


def test_production_rejects_default_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """A production boot with the public default key raises ValidationError."""
    monkeypatch.delenv("SECRET_KEY", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(APP_ENV="production")

    assert "SECRET_KEY" in str(exc_info.value)


def test_production_accepts_unique_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """A production boot with a unique key succeeds."""
    monkeypatch.delenv("SECRET_KEY", raising=False)

    settings = Settings(APP_ENV="production", SECRET_KEY="a3f1c9d2" * 8)

    assert settings.APP_ENV == "production"
    assert settings.SECRET_KEY != DEFAULT_SECRET_KEY


def test_create_app_refuses_unsafe_production_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_app refuses a production Settings object that carries the default key."""
    monkeypatch.delenv("SECRET_KEY", raising=False)
    settings = Settings(APP_ENV="production", SECRET_KEY="b7e2d4f8" * 8)
    settings.SECRET_KEY = DEFAULT_SECRET_KEY

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(settings)
