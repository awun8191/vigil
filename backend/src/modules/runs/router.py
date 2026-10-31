"""Runs router factory providing agent execution harness endpoints and SSE streams."""
import json
from uuid import UUID
from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse

from src.config.exceptions import NotFoundError
from src.config.settings import Settings
from src.dependencies import get_current_device, get_runs_service
from src.modules.auth.repository import DeviceDTO
from src.modules.runs.data import (
    RunCancelResponse,
    RunCreateRequest,
    RunResponse,
    RunStepResponse,
)
from src.modules.runs.repository import RunsRepository


def build_runs_router(settings: Settings) -> APIRouter:
    """
    Build and configure runs APIRouter instance.
    Endpoints:
      - POST /api/v1/runs
      - GET /api/v1/runs/{id}
      - GET /api/v1/runs/{id}/steps
      - GET /api/v1/runs/{id}/events
      - POST /api/v1/runs/{id}/cancel
    """
    router = APIRouter(prefix="/runs", tags=["Runs"])

    @router.post(
        "",
        response_model=RunResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Start Agent Execution Run",
    )
    async def create_run(
        payload: RunCreateRequest,
        service: RunsRepository = Depends(get_runs_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> RunResponse:
        run = await service.start_run(  # type: ignore[attr-defined]
            config_id=payload.config_id,
            card_id=payload.card_id,
            initial_prompt=payload.initial_prompt,
        )
        return RunResponse.model_validate(run)

    @router.get(
        "/{run_id}",
        response_model=RunResponse,
        status_code=status.HTTP_200_OK,
        summary="Get Run Status",
    )
    async def get_run(
        run_id: UUID,
        service: RunsRepository = Depends(get_runs_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> RunResponse:
        run = await service.get_run_by_id(run_id)
        if not run:
            raise NotFoundError(f"Run {run_id} not found")
        return RunResponse.model_validate(run)

    @router.get(
        "/{run_id}/steps",
        response_model=list[RunStepResponse],
        status_code=status.HTTP_200_OK,
        summary="Get Run Step History",
    )
    async def get_steps(
        run_id: UUID,
        since_step: int = Query(default=0, ge=0),
        service: RunsRepository = Depends(get_runs_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> list[RunStepResponse]:
        steps = await service.get_steps_since(run_id, since_step)
        return [RunStepResponse.model_validate(s) for s in steps]

    @router.get(
        "/{run_id}/events",
        summary="Stream Run Step Events via SSE",
    )
    async def stream_events(
        run_id: UUID,
        since_step: int = Query(default=0, ge=0),
        service: RunsRepository = Depends(get_runs_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> StreamingResponse:
        async def event_generator():
            async for step in service.stream_steps(run_id, since_step):  # type: ignore[attr-defined]
                data = json.dumps(
                    {
                        "step_number": step.step_number,
                        "event_type": step.event_type,
                        "payload": step.payload,
                    }
                )
                yield f"id: {step.step_number}\nevent: {step.event_type}\ndata: {data}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @router.post(
        "/{run_id}/cancel",
        response_model=RunCancelResponse,
        status_code=status.HTTP_200_OK,
        summary="Cancel Run",
    )
    async def cancel_run(
        run_id: UUID,
        service: RunsRepository = Depends(get_runs_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> RunCancelResponse:
        run = await service.cancel_run(run_id)  # type: ignore[attr-defined]
        return RunCancelResponse(
            run_id=run.id,
            status=run.status,
            cancelled_at=run.ended_at or run.started_at,
        )

    return router
