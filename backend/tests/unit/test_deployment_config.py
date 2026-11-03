"""Unit tests verifying deployment harness configuration, Dockerfile, and environment templates."""
from pathlib import Path
import yaml
import pytest

from src.config.settings import Settings


def test_dockerfile_exists_and_contains_uv() -> None:
    """Verify backend/Dockerfile exists and contains uv-based multi-stage installation."""
    dockerfile_path = Path(__file__).resolve().parents[2] / "Dockerfile"
    assert dockerfile_path.exists(), f"Dockerfile not found at {dockerfile_path}"
    content = dockerfile_path.read_text(encoding="utf-8")
    assert "FROM python:3.12" in content
    assert "astral-sh/uv" in content
    assert "uv sync --frozen --no-dev" in content
    assert "uvicorn" in content


def test_docker_compose_validity() -> None:
    """Verify deploy/docker-compose.yml defines postgres (pgvector), api, and worker."""
    compose_path = Path(__file__).resolve().parents[3] / "deploy" / "docker-compose.yml"
    assert compose_path.exists(), f"docker-compose.yml not found at {compose_path}"

    with open(compose_path, "r", encoding="utf-8") as f:
        compose = yaml.safe_load(f)

    assert "services" in compose
    services = compose["services"]

    # 1. Postgres service with pgvector
    assert "postgres" in services
    postgres = services["postgres"]
    assert "pgvector/pgvector:pg16" in postgres.get("image", "")
    assert "healthcheck" in postgres
    assert "pg_isready" in str(postgres["healthcheck"])

    # 2. FastAPI API service
    assert "api" in services
    api = services["api"]
    assert api.get("ports") == ["8000:8000"]
    assert "postgres" in api.get("depends_on", {})
    # The API has no Docker socket, so it must never poll the job queue. This
    # pin is the only thing that stops a second poller in the deployment.
    assert api.get("environment", {}).get("EMBED_WORKER") == "false"

    # 3. Background Worker service
    assert "worker" in services
    worker = services["worker"]
    assert "src.worker" in str(worker.get("command", []))
    assert "postgres" in worker.get("depends_on", {})


def test_env_example_matches_settings_fields() -> None:
    """Verify deploy/.env.example documents the core settings keys."""
    env_example_path = Path(__file__).resolve().parents[3] / "deploy" / ".env.example"
    assert env_example_path.exists(), f".env.example not found at {env_example_path}"

    content = env_example_path.read_text(encoding="utf-8")

    # Key settings fields that must be documented
    essential_keys = [
        "APP_NAME",
        "APP_ENV",
        "DATABASE_URL",
        "SECRET_KEY",
        "PAIRING_CODE_TTL_SECONDS",
        "JEV_BASE_URL",
        "FAST_LLM_BASE_URL",
        "REASONING_LLM_BASE_URL",
        "EMBEDDING_BASE_URL",
        "IDEMPOTENCY_TTL_SECONDS",
        "JOB_POLL_INTERVAL_SECONDS",
        "NTFY_BASE_URL",
    ]

    for key in essential_keys:
        assert f"{key}=" in content, f"Missing documentation for {key} in deploy/.env.example"
