"""Root test configuration and fixtures for virgil-backend."""
import sys
import pytest

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


@pytest.fixture(autouse=True)
def seeded_test_devices():
    """Seed the deterministic test devices for every test.

    This fixture is the only seeding caller in the suite. It builds Settings
    with APP_ENV="test", so the gate inside seed_test_devices() accepts the call.
    A development or production process never reaches this code.
    """
    from src.config.settings import Settings
    from src.modules.auth.service import AuthService

    service = AuthService(session=None, settings=Settings(APP_ENV="test"))
    service.seed_test_devices()
    yield


@pytest.fixture
def seeded_device_token() -> str:
    """Return a bearer token for a device seeded by seeded_test_devices."""
    return SEEDED_DEVICE_TOKEN
