"""Cards module dependencies."""
from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings, get_settings
from src.modules.cards.service import CardsService


def get_cards_service(
    session: AsyncSession,
    settings: Settings = Depends(get_settings),
) -> CardsService:
    """Dependency provider for CardsService."""
    return CardsService(session=session, settings=settings)
