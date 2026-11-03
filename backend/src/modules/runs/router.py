"""Runs router factory providing agent execution harness endpoints and SSE streams."""
from uuid import UUID
from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import StreamingResponse

from src.config.exceptions import NotFoundError
from src.config.settings import Settings
from src.dependencies import get_chat_service, get_current_device, get_current_user, get_runs_service
from src.modules.auth.repository import DeviceDTO, UserDTO
from src.modules.chat.service import ChatService
from src.modules.runs.data import (
    AgentConfigResponse,
    RunCancelResponse,
    RunCreateRequest,
    RunMessageResponse,
    RunResponse,
    RunStepResponse,
)
from src.modules.runs.service import RunsService
from src.modules.runs.stream import (
    SSE_HEADERS,
    parse_last_event_id,
    stream_run_events,
)


def build_runs_router(settings: Settings) -> APIRouter:
    """
    Build and configure runs APIRouter instance.
    Endpoints:
      - POST /api/v1/runs
      - GET /api/v1/runs/configs/default
      - GET /api/v1/runs/{id}
      - GET /api/v1/runs/{id}/steps
      - GET /api/v1/runs/{id}/messages
      - GET /api/v1/runs/{id}/events
      - POST /api/v1/runs/{id}/cancel
    """
    router = APIRouter(prefix="/runs", tags=["Runs"])

    @router.get(
        "/configs/default",
        response_model=AgentConfigResponse,
        status_code=status.HTTP_200_OK,
        summary="Get or Create the Default Agent Config",
    )
    async def default_config(
        service: RunsService = Depends(get_runs_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> AgentConfigResponse:
        """Return the pinned ``vigil-default`` config, creating it on first call.

        A GET has no write side effect: the row is inserted only when it is
        absent, and an existing row (including a ``approval_policy`` or
        ``system_prompt`` a client saved) comes back unchanged.

        Declared before the ``/{run_id}`` route so URL parsing never sends
        ``configs`` to the UUID path handler.
        """
        config = await service.get_or_create_default_agent_config()
        return AgentConfigResponse.model_validate(config)

    @router.get(
        "/latest",
        response_model=RunResponse,
        status_code=status.HTTP_200_OK,
        summary="Get the Newest Run of a Chat Session",
    )
    async def latest_run(
        session_id: UUID = Query(...),
        service: RunsService = Depends(get_runs_service),
        chat: ChatService = Depends(get_chat_service),
        user: UserDTO = Depends(get_current_user),
    ) -> RunResponse:
        """Client rehydration: after a reload the chat re-subscribes to the newest run."""
        thread = await chat.get_session(session_id)
        if thread is None or thread.user_id != user.id:
            raise NotFoundError(f"Chat session {session_id} not found")
        run = await service.latest_run_for_session(session_id)
        if run is None:
            raise NotFoundError(f"Chat session {session_id} has no runs")
        return RunResponse.model_validate(run)

    @router.post(
        "",
        response_model=RunResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Start Agent Execution Run",
    )
    async def create_run(
        payload: RunCreateRequest,
        service: RunsService = Depends(get_runs_service),
        chat: ChatService = Depends(get_chat_service),
        user: UserDTO = Depends(get_current_user),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> RunResponse:
        """Queue a run, and only into a thread the caller owns.

        The read routes (``GET /runs/latest``, ``GET /chat/sessions/{id}/messages``)
        already treat another user's session as 404. The write route matches
        them, so a run cannot be attached to a stranger's thread.
        """
        if payload.session_id is not None:
            thread = await chat.get_session(payload.session_id)
            if thread is None or thread.user_id != user.id:
                raise NotFoundError(f"Chat session {payload.session_id} not found")
        run = await service.start_run(
            config_id=payload.config_id,
            card_id=payload.card_id,
            initial_prompt=payload.initial_prompt,
            session_id=payload.session_id,
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
        service: RunsService = Depends(get_runs_service),
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
        service: RunsService = Depends(get_runs_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> list[RunStepResponse]:
        steps = await service.get_steps_since(run_id, since_step)
        return [RunStepResponse.model_validate(s) for s in steps]

    @router.get(
        "/{run_id}/messages",
        response_model=list[RunMessageResponse],
        status_code=status.HTTP_200_OK,
        summary="Get Run Transcript",
    )
    async def get_messages(
        run_id: UUID,
        service: RunsService = Depends(get_runs_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> list[RunMessageResponse]:
        run = await service.get_run_by_id(run_id)
        if not run:
            raise NotFoundError(f"Run {run_id} not found")
        messages = await service.get_messages_for_run(run_id)
        return [RunMessageResponse.model_validate(m) for m in messages]

    @router.get(
        "/{run_id}/events",
        summary="Stream Run Step Events via SSE",
    )
    async def stream_events(
        run_id: UUID,
        since_step: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        service: RunsService = Depends(get_runs_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> StreamingResponse:
        """
        Stream run steps after the client cursor.

        The `Last-Event-ID` header from a reconnect wins over the `since_step`
        query parameter. The stream closes after the run completes or fails.
        """
        run = await service.get_run_by_id(run_id)
        if not run:
            raise NotFoundError(f"Run {run_id} not found")

        cursor = parse_last_event_id(last_event_id, fallback=since_step)
        return StreamingResponse(
            stream_run_events(service, run_id, cursor),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    @router.post(
        "/{run_id}/cancel",
        response_model=RunCancelResponse,
        status_code=status.HTTP_200_OK,
        summary="Cancel Run",
    )
    async def cancel_run(
        run_id: UUID,
        service: RunsService = Depends(get_runs_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> RunCancelResponse:
        run = await service.cancel_run(run_id)
        return RunCancelResponse(
            run_id=run.id,
            status=run.status,
            cancelled_at=run.ended_at or run.started_at,
        )

    return router
