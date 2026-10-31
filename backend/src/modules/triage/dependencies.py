"""Triage module dependencies."""
from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings, get_settings
from src.modules.triage.service import TriageService


def get_triage_service(
    session: AsyncSession,
    settings: Settings = Depends(get_settings),
) -> TriageService:
    """Dependency provider for TriageService with Jev and Cards integration."""
    from src.integrations.jev.jev import JevClient
    from src.modules.cards.service import CardsService
    jev = JevClient(settings=settings)
    cards = CardsService(session=session, settings=settings)
    return TriageService(session=session, settings=settings, jev_client=jev, cards_service=cards)
