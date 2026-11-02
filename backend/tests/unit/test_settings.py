"""Unit tests verifying Settings configuration schema, defaults, parsing, and caching."""
import os
from unittest.mock import patch
import pytest
from src.config.settings import Settings, get_settings


def test_default_settings():
    """Verify default values and types for all core configuration fields."""
    settings = Settings()

    # Server defaults
    assert settings.APP_NAME == "Vigil"
    assert settings.APP_ENV == "development"
    assert settings.DEBUG is False
    assert settings.HOST == "0.0.0.0"
    assert settings.PORT == 8000
    assert settings.API_V1_PREFIX == "/api/v1"

    # Database defaults
    assert "postgresql+asyncpg://" in settings.DATABASE_URL
    assert settings.DATABASE_POOL_SIZE == 20
    assert settings.DATABASE_MAX_OVERFLOW == 10
    assert settings.DATABASE_ECHO is False

    # Security defaults
    assert len(settings.SECRET_KEY) >= 32
    assert settings.PAIRING_CODE_TTL_SECONDS == 300
    assert settings.CORS_ORIGINS == ["*"]

    # Tiered models defaults
    assert "typesafe.ai" in settings.JEV_BASE_URL
    assert settings.JEV_MODEL == "jev-classifier-v1"
    assert "openai.com" in settings.FAST_LLM_BASE_URL
    assert settings.FAST_LLM_MODEL == "gpt-4o-mini"
    assert "openai.com" in settings.REASONING_LLM_BASE_URL
    assert settings.REASONING_LLM_MODEL == "gpt-4o"
    assert settings.EMBEDDING_MODEL == "bge-small-en-v1.5"
    assert settings.EMBEDDING_DIMENSIONS == 384

    # Sandbox defaults
    assert "docker.sock" in settings.DOCKER_HOST
    assert settings.SANDBOX_IMAGE == "vigil-sandbox:latest"

    # Logging & Idempotency
    assert settings.LOG_LEVEL == "INFO"
    assert settings.LOG_FORMAT == "json"
    assert settings.IDEMPOTENCY_TTL_SECONDS == 86400
    assert settings.IDEMPOTENCY_HEADER_NAME == "Idempotency-Key"


def test_get_settings_lru_cache():
    """Verify get_settings returns identical cached singleton instance."""
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


def test_settings_env_override():
    """Verify OS environment variables override default settings."""
    env_overrides = {
        "APP_ENV": "production",
        "PORT": "9000",
        "DATABASE_URL": "postgresql+asyncpg://user:pass@db:5432/vigil_prod",
        "LOG_LEVEL": "WARNING",
        # A production boot now requires a real key, not the public default.
        "SECRET_KEY": "9f2c4e6a8b0d1f3a5c7e9b2d4f6a8c0e9f2c4e6a8b0d1f3a5c7e9b2d4f6a8c0e",
    }
    with patch.dict(os.environ, env_overrides, clear=False):
        settings = Settings()
        assert settings.APP_ENV == "production"
        assert settings.PORT == 9000
        assert settings.DATABASE_URL == "postgresql+asyncpg://user:pass@db:5432/vigil_prod"
        assert settings.LOG_LEVEL == "WARNING"


def test_cors_origins_comma_separated():
    """Verify comma-separated string in CORS_ORIGINS is parsed into a list."""
    with patch.dict(os.environ, {"CORS_ORIGINS": "https://vigil.app, https://admin.vigil.app"}):
        settings = Settings()
        assert settings.CORS_ORIGINS == ["https://vigil.app", "https://admin.vigil.app"]


def test_cors_origins_json_list():
    """Verify JSON array string in CORS_ORIGINS is parsed into a list."""
    with patch.dict(os.environ, {"CORS_ORIGINS": '["https://vigil.app"]'}):
        settings = Settings()
        assert settings.CORS_ORIGINS == ["https://vigil.app"]


def test_invalid_app_env_raises_error():
    """Verify invalid literal for APP_ENV raises a validation error."""
    with patch.dict(os.environ, {"APP_ENV": "invalid_environment"}):
        with pytest.raises(Exception):
            Settings()


def test_invalid_port_raises_error():
    """Verify non-integer string for PORT raises a validation error."""
    with patch.dict(os.environ, {"PORT": "not-a-port"}):
        with pytest.raises(Exception):
            Settings()


def test_extra_env_ignored():
    """Verify extra undeclared environment variables are ignored without error."""
    with patch.dict(os.environ, {"UNDECLARED_RANDOM_SETTING": "some_value"}):
        settings = Settings()
        assert not hasattr(settings, "UNDECLARED_RANDOM_SETTING")
