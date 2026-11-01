"""Health router factory providing liveness, readiness, and status probe endpoints."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response, status

from src.config.settings import Settings
from src.modules.health.data import HealthStatusResponse
from src.modules.health.repository import HealthRepository
from src.dependencies import get_health_service

APP_VERSION = "0.1.0"


def build_health_router(settings: Settings) -> APIRouter:
    """
    Build and configure health APIRouter instance.

    Endpoints:
      - GET /api/v1/health        Full status report. Always 200 while the API serves.
      - GET /api/v1/health/live   Liveness probe. Never checks the database.
      - GET /api/v1/health/ready  Readiness probe. 503 when the database check fails.

    Load balancers and container healthchecks must use /live and /ready. The
    /health report is informational and does not signal routing decisions.
    """
    router = APIRouter(prefix="/health", tags=["Health"])

    @router.get(
        "",
        response_model=HealthStatusResponse,
        status_code=status.HTTP_200_OK,
        summary="System Health Status Report",
        description="Reports API responsiveness and PostgreSQL connectivity as a status body.",
    )
    @router.get(
        "/",
        response_model=HealthStatusResponse,
        status_code=status.HTTP_200_OK,
        include_in_schema=False,
    )
    async def get_health(
        service: HealthRepository = Depends(get_health_service),
    ) -> HealthStatusResponse:
        status_dto = await service.get_system_status()
        return HealthStatusResponse(
            status="ok" if status_dto.database_connected else "degraded",
            version=status_dto.version,
            database="connected" if status_dto.database_connected else "error",
            timestamp=status_dto.timestamp,
            details=status_dto.details,
        )

    @router.get(
        "/live",
        response_model=HealthStatusResponse,
        status_code=status.HTTP_200_OK,
        summary="Liveness Probe",
        description=(
            "Returns 200 while the process serves requests. "
            "The probe does not check the database."
        ),
    )
    async def get_liveness() -> HealthStatusResponse:
        """Liveness probe. A failing dependency must not restart the process."""
        return HealthStatusResponse(
            status="ok",
            version=APP_VERSION,
            database="disabled",
            timestamp=datetime.now(timezone.utc),
            details={"probe": "liveness", "database_checked": False},
        )

    @router.get(
        "/ready",
        response_model=HealthStatusResponse,
        status_code=status.HTTP_200_OK,
        responses={503: {"description": "Database check failed"}},
        summary="Readiness Probe",
        description=(
            "Returns 200 when the database responds, and 503 when the check fails. "
            "Load balancers must route on this probe."
        ),
    )
    async def get_readiness(
        response: Response,
        service: HealthRepository = Depends(get_health_service),
    ) -> HealthStatusResponse:
        """Readiness probe. Fail closed so no traffic reaches a broken node."""
        status_dto = await service.get_system_status()
        ready = bool(status_dto.database_connected)
        if not ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthStatusResponse(
            status="ok" if ready else "degraded",
            version=status_dto.version,
            database="connected" if ready else "error",
            timestamp=status_dto.timestamp,
            details=status_dto.details,
        )

    return router
