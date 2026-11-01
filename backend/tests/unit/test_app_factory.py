"""Unit and integration tests for FastAPI application factory create_app."""
import httpx
from httpx import ASGITransport
import pytest
from fastapi import FastAPI
from src.config.exceptions import NotFoundError
from src.config.settings import Settings
from src.main import create_app


@pytest.mark.asyncio
async def test_create_app_instance() -> None:
    """Verify application attributes and settings assignment."""
    settings = Settings(APP_NAME="Vigil Test App", DEBUG=True)
    app = create_app(settings)
    assert isinstance(app, FastAPI)
    assert app.title == "Vigil Test App"
    assert app.docs_url == "/docs"
    assert app.state.settings == settings


@pytest.mark.asyncio
async def test_create_app_production_disables_docs() -> None:
    """Verify OpenAPI documentation is hidden when DEBUG=False."""
    settings = Settings(DEBUG=False)
    app = create_app(settings)
    assert app.docs_url is None


@pytest.mark.asyncio
async def test_create_app_middleware_headers_injected() -> None:
    """Verify X-Request-ID and X-Process-Time response headers are injected by middleware stack."""
    settings = Settings()
    app = create_app(settings)

    @app.get("/test-probe")
    async def probe():
        return {"probe": "ok"}

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/test-probe")
        assert response.status_code == 200
        assert response.json() == {"probe": "ok"}

        # Verify X-Request-ID injected by RequestIdMiddleware
        assert "x-request-id" in response.headers
        req_id = response.headers["x-request-id"]
        assert len(req_id) >= 10

        # Verify X-Process-Time injected by TimingMiddleware
        assert "x-process-time" in response.headers
        assert response.headers["x-process-time"].endswith("s")


@pytest.mark.asyncio
async def test_create_app_propagates_incoming_request_id() -> None:
    """Verify incoming X-Request-ID is echoed in response headers."""
    app = create_app(Settings())

    @app.get("/test-echo-id")
    async def echo():
        return {"echo": "ok"}

    custom_id = "client-req-9999"
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/test-echo-id", headers={"X-Request-ID": custom_id})
        assert response.headers["x-request-id"] == custom_id


@pytest.mark.asyncio
async def test_create_app_app_exception_contract() -> None:
    """Verify AppException returns uniform JSON schema and proper status code."""
    app = create_app(Settings())

    @app.get("/trigger-not-found")
    async def trigger():
        raise NotFoundError("Card not found", code="CARD_NOT_FOUND", details={"card_id": 42})

    async with httpx.AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
        response = await client.get("/trigger-not-found")
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "CARD_NOT_FOUND"
        assert data["message"] == "Card not found"
        assert data["details"] == {"card_id": 42}
        assert "request_id" in data
        assert response.headers["x-request-id"] == data["request_id"]
        assert "x-process-time" in response.headers


@pytest.mark.asyncio
async def test_create_app_unhandled_exception_returns_500() -> None:
    """Verify unhandled exception returns safe 500 error without leaking tracebacks."""
    app = create_app(Settings())

    @app.get("/trigger-crash")
    async def crash():
        raise RuntimeError("Unexpected failure")

    async with httpx.AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
        response = await client.get("/trigger-crash")
        assert response.status_code == 500
        data = response.json()
        assert data["code"] == "INTERNAL_SERVER_ERROR"
        assert data["message"] == "An unexpected error occurred"
        assert "request_id" in data
        assert response.headers["x-request-id"] == data["request_id"]
        assert "x-process-time" in response.headers
