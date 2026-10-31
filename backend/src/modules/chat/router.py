"""Chat router factory providing conversational session endpoints and token streaming."""
from uuid import UUID
from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse

from src.config.settings import Settings
from src.dependencies import get_chat_service, get_current_user
from src.modules.auth.repository import UserDTO
from src.modules.chat.data import (
    ChatMessageResponse,
    ChatSessionResponse,
    CreateChatSessionRequest,
    SendChatMessageRequest,
)
from src.modules.chat.repository import ChatRepository


def build_chat_router(settings: Settings) -> APIRouter:
    """
    Build and configure chat APIRouter instance.
    Endpoints:
      - GET /api/v1/chat/sessions
      - POST /api/v1/chat/sessions
      - GET /api/v1/chat/sessions/{id}/messages
      - POST /api/v1/chat/sessions/{id}/messages
      - GET /api/v1/chat/sessions/{id}/stream
    """
    router = APIRouter(prefix="/chat", tags=["Chat"])

    @router.get(
        "/sessions",
        response_model=list[ChatSessionResponse],
        status_code=status.HTTP_200_OK,
        summary="List Chat Sessions",
    )
    async def list_sessions(
        service: ChatRepository = Depends(get_chat_service),
        user: UserDTO = Depends(get_current_user),
    ) -> list[ChatSessionResponse]:
        sessions = await service.list_user_sessions(user.id)
        return [ChatSessionResponse.model_validate(s) for s in sessions]

    @router.post(
        "/sessions",
        response_model=ChatSessionResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Create Chat Session",
    )
    async def create_session(
        payload: CreateChatSessionRequest,
        service: ChatRepository = Depends(get_chat_service),
        user: UserDTO = Depends(get_current_user),
    ) -> ChatSessionResponse:
        session = await service.create_session(user.id, title=payload.title)  # type: ignore[attr-defined]
        return ChatSessionResponse.model_validate(session)

    @router.get(
        "/sessions/{session_id}/messages",
        response_model=list[ChatMessageResponse],
        status_code=status.HTTP_200_OK,
        summary="Get Chat History",
    )
    async def get_messages(
        session_id: UUID,
        service: ChatRepository = Depends(get_chat_service),
        _user: UserDTO = Depends(get_current_user),
    ) -> list[ChatMessageResponse]:
        messages = await service.get_history(session_id, limit=50)
        return [ChatMessageResponse.model_validate(m) for m in messages]

    @router.post(
        "/sessions/{session_id}/messages",
        response_model=ChatMessageResponse,
        status_code=status.HTTP_200_OK,
        summary="Send Chat Message",
    )
    async def send_message(
        session_id: UUID,
        payload: SendChatMessageRequest,
        service: ChatRepository = Depends(get_chat_service),
        user: UserDTO = Depends(get_current_user),
    ) -> ChatMessageResponse:
        return await service.send_message(session_id, user.id, payload.content)  # type: ignore[attr-defined]

    @router.get(
        "/sessions/{session_id}/stream",
        summary="Stream Assistant Chat Reply via SSE",
    )
    async def stream_chat(
        session_id: UUID,
        q: str,
        service: ChatRepository = Depends(get_chat_service),
        user: UserDTO = Depends(get_current_user),
    ) -> StreamingResponse:
        async def stream_generator():
            async for token in service.stream_reply(session_id, user.id, q):  # type: ignore[attr-defined]
                yield f"data: {token}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream_generator(), media_type="text/event-stream")

    return router
