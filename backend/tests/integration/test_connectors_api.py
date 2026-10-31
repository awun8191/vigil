"""Integration tests for connectors module API and encrypted credential lifecycle."""
from uuid import uuid4
import pytest
import httpx
from httpx import ASGITransport

from src.config.security import decrypt_secret
from src.config.settings import Settings
from src.main import create_app
from src.modules.connectors.service import ConnectorsService


@pytest.mark.asyncio
async def test_connector_registration_and_sync_api() -> None:
    """Verify registration with credential encryption, account listing, and sync trigger."""
    settings = Settings(APP_ENV="test")
    app = create_app(settings)
    auth_headers = {"Authorization": "Bearer vgt_testtoken"}

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Register Gmail account
        reg_payload = {
            "connector_id": "gmail",
            "account_label": "work-gmail@company.com",
            "raw_credentials": {
                "client_id": "client_abc123",
                "client_secret": "secret_xyz789",
                "refresh_token": "refresh_token_sample",
            },
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        }
        res_reg = await client.post("/api/v1/connectors/accounts", json=reg_payload, headers=auth_headers)
        assert res_reg.status_code == 201
        acc_data = res_reg.json()
        assert acc_data["connector_id"] == "gmail"
        assert acc_data["account_label"] == "work-gmail@company.com"
        assert acc_data["status"] == "active"
        account_id = acc_data["id"]

        # 2. Verify account is listed
        res_list = await client.get("/api/v1/connectors/accounts", headers=auth_headers)
        assert res_list.status_code == 200
        accounts = res_list.json()
        assert any(a["id"] == account_id for a in accounts)

        # 3. Get single account
        res_get = await client.get(f"/api/v1/connectors/accounts/{account_id}", headers=auth_headers)
        assert res_get.status_code == 200
        assert res_get.json()["id"] == account_id

        # 4. Trigger sync
        res_sync = await client.post(f"/api/v1/connectors/accounts/{account_id}/sync", headers=auth_headers)
        assert res_sync.status_code == 200
        sync_data = res_sync.json()
        assert sync_data["account_id"] == account_id
        assert sync_data["status"] == "queued"
        assert "job_id" in sync_data


@pytest.mark.asyncio
async def test_connector_credentials_are_encrypted_at_rest() -> None:
    """Verify raw credentials cannot be read from memory/DB without decrypt_secret."""
    settings = Settings(APP_ENV="test")
    service = ConnectorsService(session=None, settings=settings)  # type: ignore[arg-type]
    user_id = uuid4()

    from src.modules.connectors.data import RegisterConnectorRequest
    raw_creds = {"secret_api_key": "super_sensitive_token_999"}
    req = RegisterConnectorRequest(
        connector_id="custom_webhook",
        account_label="Webhook Integration",
        raw_credentials=raw_creds,
        scopes=[],
    )
    account = await service.register_account(user_id, req)

    # Stored encrypted string must not contain the sensitive plaintext
    assert "super_sensitive_token_999" not in account.encrypted_credentials

    # Decrypting with master secret restores plaintext
    decrypted_json = decrypt_secret(account.encrypted_credentials, settings.SECRET_KEY)
    assert "super_sensitive_token_999" in decrypted_json
