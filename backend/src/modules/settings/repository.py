"""Settings repository protocol and data transfer objects."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID


@dataclass(frozen=True)
class UserSettingsDTO:
    """Represents user-specific runtime preferences and limits."""
    user_id: UUID
    spending_cap_usd_monthly: float
    polling_interval_seconds: int
    approval_defaults: dict[str, str]
    preferred_model_tier: str


@dataclass(frozen=True)
class AuditLogDTO:
    """Represents a structured system audit log entry."""
    id: UUID
    actor: str  # user, system, worker
    action: str  # approval_decision, connector_connect, settings_change
    subject: str
    details: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class AuditLogCreateDTO:
    """Payload for recording a system audit entry."""
    actor: str
    action: str
    subject: str
    details: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SettingsRepository(Protocol):
    """Protocol contract for user preferences and audit log persistence."""

    async def get_user_settings(self, user_id: UUID) -> UserSettingsDTO | None:
        """
        Retrieve runtime settings and spending caps for a user.

        Args:
            user_id: Owner user UUID.

        Returns:
            UserSettingsDTO if found, None otherwise.
        """
        ...

    async def update_user_settings(
        self,
        user_id: UUID,
        settings: dict[str, Any],
    ) -> UserSettingsDTO:
        """
        Update user settings JSONB values.

        Args:
            user_id: Owner user UUID.
            settings: Dictionary of updated preference keys and values.

        Returns:
            Updated UserSettingsDTO.
        """
        ...

    async def record_audit_entry(self, data: AuditLogCreateDTO) -> None:
        """
        Append a structured security audit log entry.

        Args:
            data: Audit entry creation DTO.
        """
        ...

    async def list_audit_entries(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditLogDTO]:
        """
        List historical audit entries ordered chronologically descending.

        Args:
            limit: Maximum items to return.
            offset: Pagination offset.

        Returns:
            List of AuditLogDTOs.
        """
        ...
