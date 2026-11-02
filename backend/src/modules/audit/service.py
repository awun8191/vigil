"""Audit service: the append-only write path and the cursor page.

Two rules shape this module.

1. The log is append only. This module has no method that updates a stored row
   and no method that deletes one.
2. A failed audit write never fails the business action. ``record_event``
   catches every error, logs it, and returns. The service keeps its data.
"""
from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from datetime import datetime, timezone
import re
from typing import Any, ClassVar
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.exceptions import ValidationError
from src.config.logging import get_logger
from src.config.settings import Settings
from src.modules.audit.repository import (
    AuditEntryCreateDTO,
    AuditEventDTO,
    AuditPageDTO,
    AuditRepository,
)
from src.schemas.system import AUDIT_ACTORS, AuditLogModel


logger = get_logger("vigil.audit")


ACTION_APPROVAL_DECISION = "approval_decision"
"""Action of a user decision on an approval."""

ACTION_CONNECTOR_CONNECT = "connector_connect"
"""Action of a new connector account."""

ACTION_SETTINGS_CHANGE = "settings_change"
"""Action of a change to the user settings."""

DEFAULT_PAGE_SIZE = 50
"""Number of events in one page when the caller gives no limit."""

MAX_PAGE_SIZE = 200
"""Largest page that the API serves."""

REDACTED = "[REDACTED]"
"""Text that replaces a secret in ``details``."""

_SENSITIVE_KEY_MARKERS = (
    "token",
    "password",
    "passwd",
    "secret",
    "credential",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "authorization",
    "cookie",
    "bearer",
)
"""Key fragments that mark a value as a secret. The match is case-insensitive."""

_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
"""A bearer credential inside a string value."""

_TOKEN_PATTERN = re.compile(
    r"\b(?:vgt_[A-Za-z0-9_]+"
    r"|gh[pousr]_[A-Za-z0-9]+"
    r"|sk-[A-Za-z0-9_-]{8,}"
    r"|xox[baprs]-[A-Za-z0-9-]+"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+)\b"
)
"""A raw token inside a string value, matched by its known prefix."""


def _is_sensitive_key(key: str) -> bool:
    """Return True when a key name marks its value as a secret."""
    lowered = key.lower()
    return any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)


def _redact_string(value: str) -> str:
    """Replace a bearer credential or a raw token inside a string."""
    without_bearer = _BEARER_PATTERN.sub(REDACTED, value)
    return _TOKEN_PATTERN.sub(REDACTED, without_bearer)


def redact_details(value: Any) -> Any:
    """
    Return a JSON-safe copy of ``details`` with every secret removed.

    A key that names a token, a password, or a credential is replaced as a
    whole. A string value also loses a bearer header or a raw token. A nested
    mapping and a list are visited, so a secret cannot hide one level down.

    Args:
        value: Any value of a ``details`` payload.

    Returns:
        The redacted copy. The input is not modified.
    """
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            redacted[key] = REDACTED if _is_sensitive_key(key) else redact_details(raw_value)
        return redacted
    if isinstance(value, (list, tuple, set)):
        return [redact_details(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def _utc(moment: datetime) -> datetime:
    """Return a timezone-aware UTC datetime."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def encode_cursor(event: AuditEventDTO) -> str:
    """Return the opaque cursor of one event."""
    raw = f"{_utc(event.created_at).isoformat()}|{event.id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """
    Return the timestamp and the id of a cursor.

    Args:
        cursor: A value that :func:`encode_cursor` returned.

    Returns:
        The pair that the page filter compares against.

    Raises:
        ValidationError: The cursor is not a value of this log.
    """
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        created_text, separator, id_text = raw.partition("|")
        if not separator:
            raise ValueError("missing separator")
        created_at = _utc(datetime.fromisoformat(created_text))
        event_id = UUID(id_text)
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise ValidationError("Invalid audit cursor") from exc
    return created_at, event_id


class AuditService:
    """Append-only audit store that satisfies :class:`AuditRepository`.

    The service writes to Postgres when the session is live. It keeps a class
    level list when the session is absent, so a unit test and the offline API
    stay usable. Neither path can rewrite a stored event.
    """

    _events: ClassVar[list[AuditEventDTO]] = []

    def __init__(self, session: AsyncSession | None, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    # -- Internal helpers ---------------------------------------------------

    def _is_db_session_active(self) -> bool:
        """Return True when the session can run database queries."""
        if self.session is None:
            return False
        return not type(self.session).__name__.startswith("_Fallback")

    def _to_dto(self, model: AuditLogModel) -> AuditEventDTO:
        """Convert an audit row to its transfer object."""
        return AuditEventDTO(
            id=model.id,
            actor=model.actor,
            action=model.action,
            subject=model.subject,
            details=model.details,
            created_at=model.created_at,
        )

    async def rollback_quietly(self) -> None:
        """Discard a failed transaction without masking the original error."""
        try:
            if self.session is not None:
                await self.session.rollback()
        except Exception:  # noqa: BLE001 - a cleanup error must not surface
            pass

    # -- Repository operations ----------------------------------------------

    async def append(self, data: AuditEntryCreateDTO) -> AuditEventDTO:
        """
        Append one event. Redaction runs before any write.

        Args:
            data: The event to store.

        Returns:
            The stored event.

        Raises:
            ValueError: The actor is not user, system, or worker.
        """
        if data.actor not in AUDIT_ACTORS:
            raise ValueError(
                f"Audit actor must be one of {list(AUDIT_ACTORS)}, got {data.actor!r}"
            )

        details = redact_details(data.details)
        created_at = datetime.now(timezone.utc)
        event_id = uuid4()

        if self._is_db_session_active():
            try:
                self.session.add(  # type: ignore[union-attr]
                    AuditLogModel(
                        id=event_id,
                        actor=data.actor,
                        action=data.action,
                        subject=data.subject,
                        details=details,
                        created_at=created_at,
                    )
                )
                await self.session.commit()  # type: ignore[union-attr]
            except Exception:
                await self.rollback_quietly()
                raise

        event = AuditEventDTO(
            id=event_id,
            actor=data.actor,
            action=data.action,
            subject=data.subject,
            details=details,
            created_at=created_at,
        )
        self._events.append(event)
        return event

    async def list_page(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
        action: str | None = None,
    ) -> AuditPageDTO:
        """
        Read one page of events, newest first.

        The page is ordered by ``created_at`` descending and breaks a tie on
        ``id`` descending. A cursor holds the last event of the previous page,
        so a new event that arrives during the read does not shift the page.

        Args:
            limit: Maximum number of events to return.
            cursor: Cursor of the last event of the previous page.
            action: Optional action name to filter on.

        Returns:
            The page and the next cursor, or None at the end of the log.
        """
        if self._is_db_session_active():
            return await self._list_page_from_db(
                limit=limit, cursor=cursor, action=action
            )
        return self._list_page_from_memory(limit=limit, cursor=cursor, action=action)

    async def _list_page_from_db(
        self,
        *,
        limit: int,
        cursor: str | None,
        action: str | None,
    ) -> AuditPageDTO:
        """Read one page from Postgres."""
        statement = select(AuditLogModel)
        if action is not None:
            statement = statement.where(AuditLogModel.action == action)
        if cursor is not None:
            created_at, event_id = decode_cursor(cursor)
            statement = statement.where(
                or_(
                    AuditLogModel.created_at < created_at,
                    and_(
                        AuditLogModel.created_at == created_at,
                        AuditLogModel.id < event_id,
                    ),
                )
            )
        statement = statement.order_by(
            AuditLogModel.created_at.desc(), AuditLogModel.id.desc()
        ).limit(limit + 1)

        result = await self.session.execute(statement)  # type: ignore[union-attr]
        rows = list(result.scalars().all())
        has_more = len(rows) > limit
        items = [self._to_dto(row) for row in rows[:limit]]
        next_cursor = encode_cursor(items[-1]) if has_more and items else None
        return AuditPageDTO(items=items, next_cursor=next_cursor)

    def _list_page_from_memory(
        self,
        *,
        limit: int,
        cursor: str | None,
        action: str | None,
    ) -> AuditPageDTO:
        """Read one page from the in-process list."""
        events = list(self._events)
        if action is not None:
            events = [event for event in events if event.action == action]
        if cursor is not None:
            created_at, event_id = decode_cursor(cursor)
            events = [
                event
                for event in events
                if (_utc(event.created_at), event.id) < (created_at, event_id)
            ]
        events.sort(key=lambda event: (_utc(event.created_at), event.id), reverse=True)
        has_more = len(events) > limit
        items = events[:limit]
        next_cursor = encode_cursor(items[-1]) if has_more and items else None
        return AuditPageDTO(items=items, next_cursor=next_cursor)


async def record_event(
    audit: AuditRepository | None,
    *,
    actor: str,
    action: str,
    subject: str,
    details: Mapping[str, Any] | None = None,
) -> None:
    """
    Append one audit event, and never fail the caller.

    A business action is more important than its audit row. When the write
    fails, this function logs the error, discards the broken transaction, and
    returns. The caller continues as if the write succeeded. The one cost is a
    missing row, and the log records why.

    Args:
        audit: The audit store, or None when the caller has none.
        actor: Who acted: user, system, or worker.
        action: Event name.
        subject: Identifier of the object that changed.
        details: Event payload. Secrets are removed before the write.
    """
    if audit is None:
        return
    try:
        await audit.append(
            AuditEntryCreateDTO(
                actor=actor,
                action=action,
                subject=subject,
                details=dict(details or {}),
            )
        )
    except Exception:  # noqa: BLE001 - the business action must still succeed
        logger.exception(
            "Audit write failed and the business action continued",
            action=action,
            subject=subject,
            actor=actor,
        )
        rollback = getattr(audit, "rollback_quietly", None)
        if callable(rollback):
            await rollback()
