"""Health service implementing database connectivity and system liveness probes."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.modules.health.data import HealthStatusResponse
from src.modules.health.repository import HealthCheckDTO

logger = logging.getLogger("virgil.health.service")


class HealthService:
    """
    Health check service satisfying HealthRepository via duck typing.
    Provides liveness and readiness verification with constructor dependency injection.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def check_database(self) -> bool:
        """
        Execute non-destructive 'SELECT 1' probe against PostgreSQL.
        Returns True if database is healthy and responsive, False otherwise.
        """
        try:
            result = await self.session.execute(text("SELECT 1"))
            return result.scalar() == 1
        except Exception as exc:
            logger.warning("Database health check probe failed: %s", exc)
            return False

    async def get_system_status(self) -> HealthCheckDTO:
        """
        Evaluate full system readiness probe, checking database and configuration.
        """
        db_ok = await self.check_database()
        status_val = "ok" if db_ok else "unhealthy"
        return HealthCheckDTO(
            status=status_val,
            version="0.1.0",
            timestamp=datetime.now(timezone.utc),
            database_connected=db_ok,
            details={"database": "connected" if db_ok else "error"},
        )

    async def check_liveness(self) -> HealthStatusResponse:
        """Fast event loop liveness probe."""
        return HealthStatusResponse(
            status="ok",
            version="0.1.0",
            database="connected",
            timestamp=datetime.now(timezone.utc),
            details=None,
        )

    async def check_readiness(self) -> HealthStatusResponse:
        """Readiness probe verifying DB connection and core dependencies."""
        dto = await self.get_system_status()
        return HealthStatusResponse(
            status="ok" if dto.database_connected else "degraded",
            version=dto.version,
            database="connected" if dto.database_connected else "error",
            timestamp=dto.timestamp,
            details=dto.details,
        )
