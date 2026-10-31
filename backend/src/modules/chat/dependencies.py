"""Chat module dependencies."""
from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings, get_settings
from src.modules.chat.service import ChatService


def get_chat_service(
    session: AsyncSession,
    settings: Settings = Depends(get_settings),
) -> ChatService:
    """Dependency provider for ChatService."""
    return ChatService(session=session, settings=settings)
