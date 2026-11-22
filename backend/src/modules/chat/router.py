"""Chat router factory providing conversational session endpoints.

The assistant reply does not stream through chat: an in-app message creates a
run (``POST /runs`` with the thread as ``session_id``) and the RunLoop persists
the reply into the same ``messages`` table this module reads.
"""
from uuid import UUID
from fastapi import APIRouter, Depends, Query, status

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
    """
    router = APIRouter(prefix="/chat", tags=["Chat"])

    @router.get(
        "/primary",
        response_model=ChatSessionResponse,
        status_code=status.HTTP_200_OK,
        summary="Get or Create Primary Ambient Session",
    )
    async def get_primary_session(
        service: ChatRepository = Depends(get_chat_service),
        user: UserDTO = Depends(get_current_user),
    ) -> ChatSessionResponse:
        getter = getattr(service, "get_or_create_primary_session", None)
        if callable(getter):
            session = await getter(user.id)
        else:
            existing = await service.list_user_sessions(user.id, limit=1)
            session = existing[0] if existing else await service.create_session(user.id, title="Primary Stream")
        return ChatSessionResponse.model_validate(session)

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
        limit: int = Query(default=100, ge=1, le=500),
        before_id: UUID | None = Query(default=None),
        service: ChatRepository = Depends(get_chat_service),
        user: UserDTO = Depends(get_current_user),
    ) -> list[ChatMessageResponse]:
        thread = await service.get_session(session_id)
        if thread is None or thread.user_id != user.id:
            from src.config.exceptions import NotFoundError

            raise NotFoundError(f"Chat session {session_id} not found")
        recent_fn = getattr(service, "get_recent_history", None)
        if callable(recent_fn):
            messages = await recent_fn(session_id, limit=limit, before_id=before_id)
        else:
            messages = await service.get_history(session_id, limit=limit)
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

    return router
