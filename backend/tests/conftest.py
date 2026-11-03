"""Root test configuration and fixtures for vigil-backend."""
import asyncio
import sys

import pytest

# Tests must not read a developer .env. Without this, a local file changes what
# the suite asserts about defaults, and a key in it reaches a real provider.
from src.config.settings import Settings

Settings.model_config["env_file"] = None

# Deterministic device tokens owned by the test suite. No source module knows
# these values any more. AuthService.seed_test_devices() writes them into the
# in-memory device store, and only when APP_ENV == "test".
TEST_DEVICE_TOKENS = (
    "vgt_testtoken",
    "vgt_test123",
    "vgt_test_override_token",
    "vgt_test_token",
)

# The token that most tests send as a valid credential.
SEEDED_DEVICE_TOKEN = "vgt_testtoken"


@pytest.fixture(scope="session")
def python_version_info():
    """Return runtime Python version tuple."""
    return sys.version_info


async def _seed_db_auth() -> None:
    """Write the deterministic test user and devices into the test database.

    Request-level auth now reads the SQL store, so HTTP tests authenticate with
    rows, not with class dicts. The insert is idempotent: the token hashes are
    unique keys, and every value uses on-conflict-do-nothing.
    """
    from datetime import datetime, timezone

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from src.config.security import hash_token
    from src.modules.auth.state import DEFAULT_USER_ID

    engine = create_async_engine(Settings(DEBUG=True).DATABASE_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into users (id, display_name) "
                    "values (cast(:id as uuid), 'Default User') on conflict (id) do nothing"
                ),
                {"id": DEFAULT_USER_ID},
            )
            now = datetime.now(timezone.utc)
            for raw in TEST_DEVICE_TOKENS:
                await conn.execute(
                    text(
                        "insert into devices (id, user_id, name, token_hash, last_seen_at) "
                        "values (gen_random_uuid(), cast(:user_id as uuid), 'Test Device', "
                        ":token_hash, :now) on conflict (token_hash) do nothing"
                    ),
                    {"user_id": DEFAULT_USER_ID, "token_hash": hash_token(raw), "now": now},
                )
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def seeded_test_devices():
    """Seed the deterministic test devices for every test.

    Two tiers, both idempotent. The database rows back the request path, whose
    AuthService reads the SQL store. A database that is offline degrades to the
    dict store, which still serves the unit tests that build a sessionless
    service. A development or production process never reaches this code, the
    seeding functions keep their APP_ENV=test gate.
    """
    from src.config.settings import Settings
    from src.modules.auth.service import AuthService

    AuthService(session=None, settings=Settings(APP_ENV="test")).seed_test_devices()
    try:
        asyncio.run(_seed_db_auth())
    except Exception:  # noqa: BLE001 - offline database: the dict tier still holds
        pass
    yield


@pytest.fixture
def seeded_device_token() -> str:
    """Return a bearer token for a device seeded by seeded_test_devices."""
    return SEEDED_DEVICE_TOKEN
