"""Integration tests for liveness and readiness probe status codes (V-03)."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config.settings import Settings
from src.dependencies import get_health_service
from src.main import create_app
from src.modules.health.repository import HealthCheckDTO
from src.modules.health.service import HealthService

# Port 1 on the loopback interface accepts no connections, so the database is
# unreachable without any network dependency or a slow timeout.
UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:1/virgil"


class _HealthySession:
    """Async session stub that answers the SELECT 1 probe."""

    async def execute(self, *args, **kwargs):
        class _Result:
            def scalar(self) -> int:
                return 1

        return _Result()


class _ConnectedHealthService:
    """Health provider that reports a responsive database."""

    async def check_database(self) -> bool:
        return True

    async def get_system_status(self) -> HealthCheckDTO:
        return HealthCheckDTO(
            status="ok",
            version="0.1.0",
            timestamp=datetime.now(timezone.utc),
            database_connected=True,
            details={"database": "connected"},
        )


def _build_client(service) -> httpx.AsyncClient:
    """Build an ASGI client with the health provider overridden."""
    app = create_app(Settings(APP_ENV="test"))
    app.dependency_overrides[get_health_service] = lambda: service
    return httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )


@pytest.fixture
async def unreachable_health_service():
    """Yield a real HealthService bound to a session on an unreachable database."""
    engine = create_async_engine(UNREACHABLE_DATABASE_URL, pool_size=1, max_overflow=0)
    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield HealthService(session=session, settings=Settings(APP_ENV="test"))
    await engine.dispose()


@pytest.mark.asyncio
async def test_readiness_returns_503_when_database_unreachable(
    unreachable_health_service,
) -> None:
    """Readiness fails closed so a load balancer stops routing to this node."""
    async with _build_client(unreachable_health_service) as client:
        res = await client.get("/api/v1/health/ready")

    assert res.status_code == 503
    body = res.json()
    assert body["status"] == "degraded"
    assert body["database"] == "error"


@pytest.mark.asyncio
async def test_liveness_returns_200_when_database_unreachable(
    unreachable_health_service,
) -> None:
    """Liveness stays 200 because the process still serves requests."""
    async with _build_client(unreachable_health_service) as client:
        res = await client.get("/api/v1/health/live")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["details"]["database_checked"] is False


@pytest.mark.asyncio
async def test_readiness_returns_200_when_database_reachable() -> None:
    """Readiness returns 200 when the database answers the probe."""
    service = HealthService(session=_HealthySession(), settings=Settings(APP_ENV="test"))

    async with _build_client(service) as client:
        res = await client.get("/api/v1/health/ready")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"


@pytest.mark.asyncio
async def test_readiness_returns_200_with_connected_provider() -> None:
    """A connected provider keeps readiness at 200, independent of the session."""
    async with _build_client(_ConnectedHealthService()) as client:
        res = await client.get("/api/v1/health/ready")

    assert res.status_code == 200


@pytest.mark.asyncio
async def test_status_report_keeps_its_shape_and_200_code() -> None:
    """The legacy status report keeps its body and 200 code."""
    async with _build_client(_ConnectedHealthService()) as client:
        res = await client.get("/api/v1/health")

    assert res.status_code == 200
    body = res.json()
    assert set(body) >= {"status", "version", "database", "timestamp"}
