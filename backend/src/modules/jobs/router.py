"""Jobs router factory providing queue inspection and manual job dispatch endpoints."""
from uuid import UUID
from fastapi import APIRouter, Depends, status

from src.config.exceptions import NotFoundError
from src.config.settings import Settings
from src.dependencies import get_current_device, get_jobs_service
from src.modules.auth.repository import DeviceDTO
from src.modules.jobs.data import EnqueueJobRequest, JobResponse, QueueStatsResponse
from src.modules.jobs.repository import JobCreateDTO, JobsRepository


def build_jobs_router(settings: Settings) -> APIRouter:
    """
    Build and configure jobs APIRouter instance.
    Endpoints:
      - GET /api/v1/jobs
      - GET /api/v1/jobs/{id}
      - POST /api/v1/jobs/enqueue
    """
    router = APIRouter(prefix="/jobs", tags=["Jobs"])

    @router.get(
        "",
        response_model=QueueStatsResponse,
        status_code=status.HTTP_200_OK,
        summary="Get Queue Statistics",
    )
    async def get_stats(
        service: JobsRepository = Depends(get_jobs_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> QueueStatsResponse:
        stats = await service.get_queue_stats()
        return QueueStatsResponse.model_validate(stats)

    @router.get(
        "/{job_id}",
        response_model=JobResponse,
        status_code=status.HTTP_200_OK,
        summary="Get Job Details",
    )
    async def get_job(
        job_id: UUID,
        service: JobsRepository = Depends(get_jobs_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> JobResponse:
        job = await service.get_by_id(job_id)
        if not job:
            raise NotFoundError(f"Job {job_id} not found")
        return JobResponse.model_validate(job)

    @router.post(
        "/enqueue",
        response_model=JobResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Enqueue Background Job",
    )
    async def enqueue_job(
        payload: EnqueueJobRequest,
        service: JobsRepository = Depends(get_jobs_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> JobResponse:
        dto = JobCreateDTO(
            type=payload.type,
            payload=payload.payload,
            run_at=payload.run_at,
            max_attempts=payload.max_attempts,
        )
        job = await service.enqueue(dto)
        return JobResponse.model_validate(job)

    return router
