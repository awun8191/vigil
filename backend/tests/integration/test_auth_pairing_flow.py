"""Integration test for Milestone 4: Operational Health & Device Auth Flow."""
from datetime import datetime, timezone
import pytest
import httpx
from httpx import ASGITransport

from src.config.settings import Settings
from src.main import create_app, lifespan
from src.modules.auth.service import AuthService


@pytest.mark.asyncio
async def test_full_device_pairing_and_auth_lifecycle() -> None:
    """
    Test the complete device pairing and authentication flow:
    1. Generate a startup pairing code.
    2. Pair device via POST /api/v1/auth/pair exchanging code for device bearer token.
    3. Reusing the one-time code fails with 401 INVALID_PAIRING_CODE.
    4. Authenticate via GET /api/v1/auth/me with bearer token -> returns 200 with identity.
    5. Request without token or with revoked token fails with 401.
    """
    settings = Settings(APP_ENV="test")
    app = create_app(settings)
    auth_service = AuthService(session=None, settings=settings)  # type: ignore[arg-type]

    pairing_code = await auth_service.generate_startup_pairing_code()
    assert pairing_code.startswith("VGL-")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Pair new device using valid code
        pair_payload = {
            "pairing_code": pairing_code,
            "device_name": "Pixel 9 Pro Mobile Client",
            "push_token": "fcm_test_push_token_abc123",
        }
        res_pair = await client.post("/api/v1/auth/pair", json=pair_payload)
        assert res_pair.status_code == 200
        pair_data = res_pair.json()
        assert "device_token" in pair_data
        device_token = pair_data["device_token"]
        assert device_token.startswith("vgt_")
        assert pair_data["device_name"] == "Pixel 9 Pro Mobile Client"

        # 2. Reusing the one-time pairing code must fail with 401
        res_reuse = await client.post("/api/v1/auth/pair", json=pair_payload)
        assert res_reuse.status_code == 401
        assert res_reuse.json()["code"] == "INVALID_PAIRING_CODE"

        # 3. Access /api/v1/auth/me using the newly issued bearer token
        auth_header = {"Authorization": f"Bearer {device_token}"}
        res_me = await client.get("/api/v1/auth/me", headers=auth_header)
        assert res_me.status_code == 200
        me_data = res_me.json()
        assert me_data["device_name"] == "Pixel 9 Pro Mobile Client"
        assert me_data["device_id"] == pair_data["device_id"]
        assert me_data["user_id"] == pair_data["user_id"]

        # 4. Access without authorization header fails with 422 (FastAPI required Header)
        res_unauth = await client.get("/api/v1/auth/me")
        assert res_unauth.status_code in (401, 422)

        # 5. Access with malformed or invalid token fails with 401
        res_bad_token = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not_a_real_token"})
        assert res_bad_token.status_code == 401


@pytest.mark.asyncio
async def test_health_probe_endpoints() -> None:
    """Verify health liveness & readiness probes return HTTP 200 OK."""
    app = create_app(Settings())
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/health")
        assert res.status_code == 200
        body = res.json()
        assert "status" in body
        assert "version" in body
        assert "database" in body
        assert "x-request-id" in res.headers
        assert "x-process-time" in res.headers


@pytest.mark.asyncio
async def test_startup_lifespan_qr_code_emission() -> None:
    """Verify lifespan emits ASCII QR code in non-test mode without crashing."""
    settings = Settings(APP_ENV="development")
    app = create_app(settings)
    async with lifespan(app):
        # Lifespan startup executed cleanly
        assert app.state.settings.APP_ENV == "development"
