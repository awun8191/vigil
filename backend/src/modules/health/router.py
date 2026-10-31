"""Health router factory providing liveness and readiness probe endpoints."""
from fastapi import APIRouter, Depends, status
from src.config.settings import Settings
from src.modules.health.data import HealthStatusResponse
from src.modules.health.repository import HealthRepository
from src.dependencies import get_health_service


def build_health_router(settings: Settings) -> APIRouter:
    """
    Build and configure health APIRouter instance.
    Endpoints:
      - GET /api/v1/health (and GET /api/v1/health/)
    """
    router = APIRouter(prefix="/health", tags=["Health"])

    @router.get(
        "",
        response_model=HealthStatusResponse,
        status_code=status.HTTP_200_OK,
        summary="System Liveness and Readiness Probe",
        description="Verifies API responsiveness and PostgreSQL database connectivity.",
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

    return router
