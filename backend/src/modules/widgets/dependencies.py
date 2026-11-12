"""Widgets module dependencies."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.modules.widgets.service import WidgetsService


def get_widgets_service(
    session: AsyncSession,
    settings: Settings,
) -> WidgetsService:
    """Dependency provider for WidgetsService."""
    return WidgetsService(session=session, settings=settings)
