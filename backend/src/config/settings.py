"""Application settings management powered by Pydantic v2 BaseSettings."""
from functools import lru_cache
import json
from typing import Literal, Union
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly typed application configuration loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # --- Application & Server ---
    APP_NAME: str = "Virgil"
    APP_ENV: Literal["development", "staging", "production", "test"] = "development"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    API_V1_PREFIX: str = "/api/v1"

    # --- Database (PostgreSQL 16 + pgvector) ---
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/virgil"
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 10
    DATABASE_ECHO: bool = False

    # --- Cryptography & Security ---
    SECRET_KEY: str = Field(
        default="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        description="Master secret key for AES-256-GCM credential encryption (32 bytes or 64 hex characters)",
    )
    PAIRING_CODE_TTL_SECONDS: int = 300
    DEVICE_TOKEN_SECRET: str = "virgil-device-token-secret-change-in-production"
    CORS_ORIGINS: Union[list[str], str] = ["*"]

    @field_validator("CORS_ORIGINS", mode="after")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, list[str]]) -> list[str]:
        """Support both JSON-encoded string and comma-separated string for CORS origins."""
        if isinstance(v, str):
            v_stripped = v.strip()
            if v_stripped.startswith("[") and v_stripped.endswith("]"):
                try:
                    parsed = json.loads(v_stripped)
                    if isinstance(parsed, list):
                        return [str(item).strip() for item in parsed if str(item).strip()]
                except Exception:
                    pass
            return [origin.strip() for origin in v_stripped.split(",") if origin.strip()]
        return v

    # --- Tiered Model Architecture ---
    # Tier 1: Jev Decision Classifier
    JEV_BASE_URL: str = "https://api.typesafe.ai/v1"
    JEV_API_KEY: str = ""
    JEV_MODEL: str = "jev-classifier-v1"

    # Tier 2: Fast LLM (Triage Stage 2 metadata extractor & Compaction summarizer)
    FAST_LLM_BASE_URL: str = "https://api.openai.com/v1"
    FAST_LLM_API_KEY: str = ""
    FAST_LLM_MODEL: str = "gpt-4o-mini"

    # Tier 3: Reasoning LLM (Agent event loop execution & complex synthesis)
    REASONING_LLM_BASE_URL: str = "https://api.openai.com/v1"
    REASONING_LLM_API_KEY: str = ""
    REASONING_LLM_MODEL: str = "gpt-4o"

    # --- Local Embedding Model (bge-small-en-v1.5, int8 ONNX) ---
    # The embedding path is local and offline. No embedding API is called.
    EMBEDDING_BASE_URL: str = ""
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_MODEL: str = "bge-small-en-v1.5"
    EMBEDDING_DIMENSIONS: int = 384
    EMBEDDING_ARTIFACT_REPO: str = "Xenova/bge-small-en-v1.5"
    EMBEDDING_ARTIFACT_REVISION: str = "ea104dacec62c0de699686887e3f920caeb4f3e3"
    EMBEDDING_ARTIFACT_FILE: str = "onnx/model_int8.onnx"
    EMBEDDING_ARTIFACT_SHA256: str = (
        "bf64d05457cb391fa88d045faf5927a15ea36d96228ddf23ea970087afdc1197"
    )
    EMBEDDING_TOKENIZER_FILE: str = "tokenizer.json"
    EMBEDDING_MODEL_DIR: str = "~/.cache/virgil/embedding"
    EMBEDDING_MAX_TOKENS: int = 512
    EMBEDDING_DOCUMENT_PREFIX: str = ""
    EMBEDDING_QUERY_PREFIX: str = "Represent this sentence for searching relevant passages: "
    EMBEDDING_IDLE_UNLOAD_SECONDS: int = 300
    EMBEDDING_INTRA_OP_THREADS: int = 2

    # --- Docker Sandbox Runner ---
    DOCKER_HOST: str = "unix:///var/run/docker.sock"
    SANDBOX_IMAGE: str = "virgil-sandbox:latest"
    WORKSPACE_BASE_PATH: str = "/var/vigil/workspaces"

    # --- Logging Configuration ---
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    LOG_FORMAT: Literal["json", "console"] = "json"

    # --- Idempotency Configuration ---
    IDEMPOTENCY_TTL_SECONDS: int = 86400
    IDEMPOTENCY_HEADER_NAME: str = "Idempotency-Key"

    # --- Background Jobs & Worker ---
    JOB_POLL_INTERVAL_SECONDS: int = 5
    JOB_LOCK_TIMEOUT_SECONDS: int = 300

    # --- Notifications Subsystem ---
    NTFY_BASE_URL: str = "https://ntfy.sh"
    NTFY_TOPIC: str = "virgil-notifications"
    NTFY_AUTH_TOKEN: str = ""


@lru_cache
def get_settings() -> Settings:
    """Return cached singleton instance of application settings."""
    return Settings()
