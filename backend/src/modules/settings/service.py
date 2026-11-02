"""Settings service implementing user preferences and structured audit trail."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar
from uuid import UUID, uuid4
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.modules.audit.repository import AuditRepository
from src.modules.audit.service import (
    ACTION_SETTINGS_CHANGE,
    AuditService,
    record_event,
)
from src.modules.settings.data import SystemLimitsResponse
from src.modules.settings.repository import (
    AuditLogCreateDTO,
    AuditLogDTO,
    UserSettingsDTO,
)


class SettingsService:
    """
    Settings and audit trail service satisfying SettingsRepository via duck typing.
    Constructor injection of session and settings.
    """

    _user_settings: ClassVar[dict[UUID, UserSettingsDTO]] = {}
    _audit_logs: ClassVar[list[AuditLogDTO]] = []

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        audit: AuditRepository | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.audit = audit or AuditService(session=session, settings=settings)

    async def get_user_settings(self, user_id: UUID) -> UserSettingsDTO | None:
        if user_id in self._user_settings:
            return self._user_settings[user_id]
        default = UserSettingsDTO(
            user_id=user_id,
            spending_cap_usd_monthly=50.0,
            polling_interval_seconds=300,
            approval_defaults={"read": "auto", "external": "ask"},
            preferred_model_tier="reasoning",
        )
        self._user_settings[user_id] = default
        return default

    async def update_user_settings(self, user_id: UUID, settings: dict[str, Any]) -> UserSettingsDTO:
        existing = await self.get_user_settings(user_id)
        current_spending = existing.spending_cap_usd_monthly if existing else 50.0
        current_polling = existing.polling_interval_seconds if existing else 300
        current_approvals = existing.approval_defaults if existing else {"read": "auto", "external": "ask"}
        current_tier = existing.preferred_model_tier if existing else "reasoning"

        updated = UserSettingsDTO(
            user_id=user_id,
            spending_cap_usd_monthly=settings.get("spending_cap_usd_monthly", current_spending),
            polling_interval_seconds=settings.get("polling_interval_seconds", current_polling),
            approval_defaults=settings.get("approval_defaults", current_approvals),
            preferred_model_tier=settings.get("preferred_model_tier", current_tier),
        )
        self._user_settings[user_id] = updated

        # The new settings are stored now. The audit row names the user and the
        # keys that changed. record_event logs a failure and returns, so the
        # change still succeeds when the log is unavailable.
        await record_event(
            self.audit,
            actor="user",
            action=ACTION_SETTINGS_CHANGE,
            subject=str(user_id),
            details={
                "changed": sorted(str(key) for key in settings),
                "values": dict(settings),
            },
        )
        return updated

    async def record_audit_entry(self, data: AuditLogCreateDTO) -> None:
        """Insert security audit entry into audit_log table."""
        entry_id = uuid4()
        now = datetime.now(timezone.utc)
        dto = AuditLogDTO(
            id=entry_id,
            actor=data.actor,
            action=data.action,
            subject=data.subject,
            details=data.details,
            created_at=now,
        )
        self._audit_logs.append(dto)

    async def list_audit_entries(self, limit: int = 50, offset: int = 0) -> list[AuditLogDTO]:
        sorted_logs = sorted(self._audit_logs, key=lambda a: a.created_at, reverse=True)
        return sorted_logs[offset : offset + limit]

    # Domain Orchestration Methods
    async def log_audit(
        self,
        actor: str,
        action: str,
        subject: str,
        details: dict[str, Any],
    ) -> None:
        """Convenience method to write structured audit event."""
        dto = AuditLogCreateDTO(
            actor=actor,
            action=action,
            subject=subject,
            details=details,
        )
        await self.record_audit_entry(dto)

    def get_system_limits(self) -> SystemLimitsResponse:
        """Return configured system-wide safety caps."""
        return SystemLimitsResponse()
