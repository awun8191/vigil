"""Audit module dependencies."""
from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings, get_settings
from src.modules.audit.service import AuditService


def get_audit_service(
    session: AsyncSession,
    settings: Settings = Depends(get_settings),
) -> AuditService:
    """Dependency provider for AuditService."""
    return AuditService(session=session, settings=settings)
