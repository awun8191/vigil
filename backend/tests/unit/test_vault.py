"""Unit tests for KeyVaultService AES-256-GCM encryption, masking, and REST endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4
import httpx
from httpx import ASGITransport
import pytest

from src.config.security import SecurityError
from src.config.settings import Settings
from src.dependencies import get_current_user, get_key_vault_service
from src.main import create_app
from src.modules.auth.repository import UserDTO
from src.modules.settings.vault import KeyVaultService, mask_secret_value
from src.modules.settings.validators import validate_api_key


@pytest.fixture(autouse=True)
def clean_vault_storage() -> None:
    """Clear in-memory storage before each test."""
    KeyVaultService.clear_all()
    yield
    KeyVaultService.clear_all()


def test_masking_rules() -> None:
    """Verify masking rules: len > 8 -> prefix(3) + '****' + suffix(4); len <= 8 -> '****'."""
    assert mask_secret_value("123456789") == "123****6789"
    assert mask_secret_value("fc-live-secret-token-12345") == "fc-****2345"
    assert mask_secret_value("12345678") == "****"
    assert mask_secret_value("short") == "****"
    assert mask_secret_value("") == "****"


@pytest.mark.asyncio
async def test_vault_service_aes_encryption_and_decryption() -> None:
    """Verify KeyVaultService uses AES-256-GCM authenticated encryption."""
    settings = Settings()
    vault = KeyVaultService(settings=settings)
    user_id = uuid4()

    raw_secret = "fc-live-prod-secret-key-9999"
    await vault.set_secret(user_id, "FIRECRAWL_API_KEY", raw_secret)

    # 1. Verify ciphertext is encrypted and does not contain plaintext
    stored_record = KeyVaultService._fallback_store.rows.get(
        (user_id, "FIRECRAWL_API_KEY")
    )
    assert stored_record is not None
    assert stored_record.ciphertext != raw_secret
    assert raw_secret not in stored_record.ciphertext

    # 2. Verify decryption returns original plaintext
    retrieved = await vault.get_secret(user_id, "FIRECRAWL_API_KEY")
    assert retrieved == raw_secret

    # 3. Verify another user cannot read the key by name alone
    assert await vault.get_secret(uuid4(), "FIRECRAWL_API_KEY") is None


@pytest.mark.asyncio
async def test_vault_user_isolation_and_deletion() -> None:
    """Verify tenants are isolated and deletion removes secrets properly."""
    settings = Settings()
    vault = KeyVaultService(settings=settings)
    user_a = uuid4()
    user_b = uuid4()

    await vault.set_secret(user_a, "API_KEY", "user-a-secret-value-1111")
    await vault.set_secret(user_b, "API_KEY", "user-b-secret-value-2222")

    # Each user gets their own secret
    assert await vault.get_secret(user_a, "API_KEY") == "user-a-secret-value-1111"
    assert await vault.get_secret(user_b, "API_KEY") == "user-b-secret-value-2222"

    # Delete secret for user A
    deleted = await vault.delete_secret(user_a, "API_KEY")
    assert deleted is True
    assert await vault.get_secret(user_a, "API_KEY") is None
    # User B's secret is untouched
    assert await vault.get_secret(user_b, "API_KEY") == "user-b-secret-value-2222"

    # Deleting non-existent secret returns False
    assert await vault.delete_secret(user_a, "API_KEY") is False


@pytest.mark.asyncio
async def test_list_secrets_never_returns_plaintext() -> None:
    """Verify list_secrets masks all values and never returns plaintext."""
    settings = Settings()
    vault = KeyVaultService(settings=settings)
    user_id = uuid4()

    plain_val = "groq-super-secret-production-token"
    await vault.set_secret(user_id, "GROQ_API_KEY", plain_val)
    await vault.set_secret(user_id, "PIN", "1234")

    secrets = await vault.list_secrets(user_id)
    assert len(secrets) == 2

    groq_secret = next(s for s in secrets if s.key_name == "GROQ_API_KEY")
    assert groq_secret.masked_value == "gro****oken"
    assert plain_val not in groq_secret.masked_value

    pin_secret = next(s for s in secrets if s.key_name == "PIN")
    assert pin_secret.masked_value == "****"


@pytest.mark.asyncio
async def test_vault_rest_endpoints() -> None:
    """Verify GET, POST, and DELETE /api/v1/settings/vault endpoints."""
    app = create_app()
    test_user_id = uuid4()
    now = datetime.now(timezone.utc)
    mock_user = UserDTO(
        id=test_user_id,
        display_name="Test User",
        settings={},
        created_at=now,
        updated_at=now,
    )
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_key_vault_service] = lambda: KeyVaultService(
        settings=Settings()
    )


    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Initially empty
        res = await client.get("/api/v1/settings/vault")
        assert res.status_code == 200
        assert res.json() == []

        # 2. Store secret
        res_post = await client.post(
            "/api/v1/settings/vault",
            json={"key": "FIRECRAWL_API_KEY", "value": "fc-live-key-xyz-12345"},
        )
        assert res_post.status_code == 200
        assert res_post.json()["ok"] is True

        # 3. List secrets
        res_list = await client.get("/api/v1/settings/vault")
        assert res_list.status_code == 200
        items = res_list.json()
        assert len(items) == 1
        assert items[0]["key_name"] == "FIRECRAWL_API_KEY"
        assert items[0]["masked_value"] == "fc-****2345"

        # 4. Delete secret
        res_del = await client.delete("/api/v1/settings/vault/FIRECRAWL_API_KEY")
        assert res_del.status_code == 204

        # 5. List again -> empty
        res_list2 = await client.get("/api/v1/settings/vault")
        assert res_list2.status_code == 200
        assert res_list2.json() == []

        # 6. Delete missing secret -> 404
        res_del404 = await client.delete("/api/v1/settings/vault/NONEXISTENT_KEY")
        assert res_del404.status_code == 404

        # 7. Whitespace-only key -> 422, not a server error
        res_blank = await client.post(
            "/api/v1/settings/vault",
            json={"key": "   ", "value": "value"},
        )
        assert res_blank.status_code == 422


@pytest.mark.asyncio
async def test_vault_normalizes_bearer_prefix_for_token_keys() -> None:
    """Token-shaped values lose a pasted Bearer prefix; passwords keep their bytes."""
    vault = KeyVaultService(settings=Settings())
    user_id = uuid4()

    await vault.set_secret(user_id, "GROQ_API_KEY", "  Bearer gsk_live_token_12345  ")
    assert await vault.get_secret(user_id, "GROQ_API_KEY") == "gsk_live_token_12345"

    await vault.set_secret(user_id, "POSTGRES_PASSWORD", "  spaced pass  ")
    assert await vault.get_secret(user_id, "POSTGRES_PASSWORD") == "  spaced pass  "


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


class _FakeGroqClient:
    """Stands in for httpx.AsyncClient in the validator tests."""

    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, *args: object, **kwargs: object) -> "_FakeGroqClient":
        return self

    async def __aenter__(self) -> "_FakeGroqClient":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
        self.calls.append((url, headers or {}))
        return self.response


@pytest.mark.asyncio
async def test_groq_validator_accepts_a_200(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 200 from the Groq models endpoint is a valid key."""
    client = _FakeGroqClient(_FakeResponse(200, {"data": []}))
    monkeypatch.setattr("src.modules.settings.validators.httpx.AsyncClient", client)

    result = await validate_api_key(
        "GROQ_API_KEY", "Bearer gsk_live_key_12345", Settings()
    )

    assert result.valid is True
    assert result.provider == "groq"
    assert result.status_code == 200
    assert client.calls[0][1]["Authorization"] == "Bearer gsk_live_key_12345"
    assert client.calls[0][0].endswith("/models")


@pytest.mark.asyncio
async def test_groq_validator_reports_a_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 401 from Groq is a reported result, not an exception."""
    client = _FakeGroqClient(
        _FakeResponse(401, {"error": {"message": "Invalid API Key"}})
    )
    monkeypatch.setattr("src.modules.settings.validators.httpx.AsyncClient", client)

    result = await validate_api_key("GROQ_API_KEY", "gsk_bad", Settings())

    assert result.valid is False
    assert result.status_code == 401
    assert "rejected" in result.message.lower()


@pytest.mark.asyncio
async def test_unknown_key_has_no_validator() -> None:
    """A key with no provider validator says so instead of pretending to pass."""
    result = await validate_api_key("SOME_UNKNOWN_KEY", "value", Settings())
    assert result.valid is False
    assert result.provider == "unknown"
    assert "No key validator" in result.message


@pytest.mark.asyncio
async def test_whitespace_key_is_a_report_not_a_crash() -> None:
    """A whitespace-only key is reported, never raised out of the validator."""
    result = await validate_api_key("   ", "value", Settings())
    assert result.valid is False
    assert "empty" in result.message.lower()


@pytest.mark.asyncio
async def test_vault_persists_across_service_instances() -> None:
    """A saved secret is read back by a second service over a second session."""
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from src.modules.auth.state import DEFAULT_USER_ID
    from src.schemas.vault import VaultSecretModel

    settings = Settings(APP_ENV="test")
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
                user_exists = await connection.scalar(
                    text("select count(*) from users where id = cast(:id as uuid)"),
                    {"id": str(DEFAULT_USER_ID)},
                )
        except Exception:
            pytest.skip("Postgres is not available for the persistence test")
        if not user_exists:
            pytest.skip("the seeded test user is not in the database")

        async with session_factory() as session:
            vault = KeyVaultService(session=session, settings=settings)
            await vault.set_secret(
                DEFAULT_USER_ID, "PERSISTENCE_PROBE", "durable-value-123"
            )
            await session.commit()

        async with session_factory() as session:
            vault = KeyVaultService(session=session, settings=settings)
            assert (
                await vault.get_secret(DEFAULT_USER_ID, "PERSISTENCE_PROBE")
                == "durable-value-123"
            )
            row = (
                await session.execute(
                    select(VaultSecretModel).where(
                        VaultSecretModel.key_name == "PERSISTENCE_PROBE"
                    )
                )
            ).scalars().first()
            assert row is not None
            assert "durable-value-123" not in row.ciphertext

            await vault.delete_secret(DEFAULT_USER_ID, "PERSISTENCE_PROBE")
            await session.commit()

        async with session_factory() as session:
            vault = KeyVaultService(session=session, settings=settings)
            assert (
                await vault.get_secret(DEFAULT_USER_ID, "PERSISTENCE_PROBE") is None
            )
    finally:
        await engine.dispose()
