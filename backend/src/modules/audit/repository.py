"""Audit repository protocol and data transfer objects."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID


@dataclass(frozen=True)
class AuditEventDTO:
    """One stored audit event. A stored event is immutable."""

    id: UUID
    actor: str
    action: str
    subject: str
    details: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class AuditEntryCreateDTO:
    """Payload for one new audit event."""

    actor: str
    action: str
    subject: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuditPageDTO:
    """One cursor page of audit events, newest first."""

    items: list[AuditEventDTO]
    next_cursor: str | None


@runtime_checkable
class AuditRepository(Protocol):
    """Protocol contract for the append-only audit log.

    The contract has no update method and no delete method on purpose. A
    caller can add an event and read a page. Nothing else is possible.
    """

    async def append(self, data: AuditEntryCreateDTO) -> AuditEventDTO:
        """
        Append one audit event and return the stored row.

        Args:
            data: The event to store.

        Returns:
            The stored event, with the redacted details.
        """
        ...

    async def list_page(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        action: str | None = None,
    ) -> AuditPageDTO:
        """
        Read one page of events, newest first.

        Args:
            limit: Maximum number of events to return.
            cursor: Opaque cursor of the last event of the previous page.
            action: Optional action name to filter on.

        Returns:
            The page and the cursor of the next page, or None at the end.
        """
        ...
