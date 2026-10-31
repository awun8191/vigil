"""Health repository protocol and data transfer objects."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class HealthCheckDTO:
    """Represents a system or database health check snapshot."""
    status: str  # "ok", "degraded", "unhealthy"
    version: str
    timestamp: datetime
    database_connected: bool
    details: dict[str, Any] | None = None


@runtime_checkable
class HealthRepository(Protocol):
    """Protocol contract for health check persistence and probe operations."""

    async def check_database(self) -> bool:
        """
        Verify database connectivity and query responsiveness.

        Returns:
            True if the database is reachable and responsive, False otherwise.
        """
        ...

    async def get_system_status(self) -> HealthCheckDTO:
        """
        Evaluate full system health status including core components.

        Returns:
            HealthCheckDTO containing overall status, version, and component states.
        """
        ...
