"""Approvals module dependencies."""
from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings, get_settings
from src.modules.approvals.service import ApprovalsService


def get_approvals_service(
    session: AsyncSession,
    settings: Settings = Depends(get_settings),
) -> ApprovalsService:
    """Dependency provider for ApprovalsService."""
    return ApprovalsService(session=session, settings=settings)
