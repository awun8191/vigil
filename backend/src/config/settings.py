"""Application settings management powered by Pydantic v2 BaseSettings."""
from functools import lru_cache
import json
from typing import Literal, Union
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Public values that must never protect a deployment. The first value is the
# historical default. The second is the placeholder in deploy/.env.example.
DEFAULT_SECRET_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
ENV_EXAMPLE_SECRET_KEY = "CHANGEME-generate-with-openssl-rand-hex-32"
UNSAFE_SECRET_KEYS = frozenset({DEFAULT_SECRET_KEY, ENV_EXAMPLE_SECRET_KEY})


def is_unsafe_secret_key(value: str) -> bool:
    """Return True when a secret key is empty, public, or an example placeholder."""
    return not value.strip() or value in UNSAFE_SECRET_KEYS


class Settings(BaseSettings):
    """Strongly typed application configuration loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # --- Application & Server ---
    APP_NAME: str = "Vigil"
    APP_ENV: Literal["development", "staging", "production", "test"] = "development"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    API_V1_PREFIX: str = "/api/v1"

    # --- Database (PostgreSQL 16 + pgvector) ---
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/vigil"
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 10
    DATABASE_ECHO: bool = False

    # --- Cryptography & Security ---
    SECRET_KEY: str = Field(
        default=DEFAULT_SECRET_KEY,
        description="Master secret key for AES-256-GCM credential encryption (32 bytes or 64 hex characters)",
    )
    PAIRING_CODE_TTL_SECONDS: int = 300
    DEVICE_TOKEN_SECRET: str = "vigil-device-token-secret-change-in-production"
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

    @model_validator(mode="after")
    def reject_unsafe_secret_key_in_production(self) -> "Settings":
        """Refuse a production configuration that keeps a published secret key.

        The default key and the .env.example placeholder are public. A deployment
        that encrypts credentials with either key protects nothing.
        """
        if self.APP_ENV == "production" and is_unsafe_secret_key(self.SECRET_KEY):
            raise ValueError(
                "SECRET_KEY is the public default or the .env.example placeholder. "
                "Set SECRET_KEY to a unique value before booting in production. "
                "Generate one with: openssl rand -hex 32"
            )
        return self

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
    # ONNX Runtime keeps the attention buffers of one run. A 200-message index
    # job makes 50 windows, so one run holds at most four windows and at most
    # 1024 tokens. The peak resident set then stays under 350 MB.
    EMBEDDING_MAX_BATCH: int = 4
    EMBEDDING_MAX_BATCH_TOKENS: int = 1024
    # A child process holds the ONNX session and exits after the run, so the
    # pages of the run go back to the operating system and the worker keeps
    # only the tokenizer. The protocol stays the same: LocalEmbeddingModel
    # still satisfies EmbeddingModel.
    EMBEDDING_ENCODE_SUBPROCESS: bool = True

    # --- Memory Retrieval (cosine similarity with exponential time decay) ---
    # The override threshold is measured on the local model: correct answers score
    # 0.679 to 0.801, the best wrong answers 0.561 to 0.615.
    MEMORY_RETRIEVAL_HALF_LIFE_DAYS: float = 30.0
    MEMORY_RETRIEVAL_FLOOR: float = 0.35
    MEMORY_RETRIEVAL_OVERRIDE_THRESHOLD: float = 0.65
    MEMORY_RETRIEVAL_MIN_SIMILARITY: float = 0.25
    MEMORY_RETRIEVAL_CANDIDATE_K: int = 50
    MEMORY_RETRIEVAL_TOP_N: int = 8
    MEMORY_RETRIEVAL_EF_SEARCH: int = 200
    MEMORY_RETRIEVAL_ITERATIVE_SCAN: Literal[
        "off", "relaxed_order", "strict_order"
    ] = "relaxed_order"
    # The measured crossover between the exact scan and the tenant graph. The
    # exact scan answers up to and including this tenant row count. Section 3.5
    # of docs/BENCHMARKS.md records the measurement that set this value: the
    # exact scan is faster at 5,000 and 10,000 rows, and the tenant graph is
    # faster at 20,000 and 50,000 rows.
    MEMORY_RETRIEVAL_EXACT_SCAN_MAX_ROWS: int = 20000
    # The measured width of the tenant graph. At 50,000 rows the graph reaches
    # recall@50 of 0.6216 at 200 and 0.9796 at 1000, which is the maximum that
    # pgvector accepts.
    MEMORY_RETRIEVAL_TENANT_GRAPH_EF_SEARCH: int = 1000

    # --- Docker Sandbox Runner ---
    DOCKER_HOST: str = "unix:///var/run/docker.sock"
    SANDBOX_IMAGE: str = "vigil-sandbox:latest"
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
    NTFY_TOPIC: str = "vigil-notifications"
    NTFY_AUTH_TOKEN: str = ""


@lru_cache
def get_settings() -> Settings:
    """Return cached singleton instance of application settings."""
    return Settings()
