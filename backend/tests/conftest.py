"""Root test configuration and fixtures for vigil-backend."""
import sys
import pytest


@pytest.fixture(scope="session")
def python_version_info():
    """Return runtime Python version tuple."""
    return sys.version_info
