"""System schemas: background jobs, idempotency records, and the audit log."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

import sqlalchemy as sa
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from src.schemas.base import Base


class JobModel(Base):
    """Postgres-backed atomic background job record for worker claiming and retries."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=sa.text("'{}'::jsonb"),
    )
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa.text("0"),
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=5,
        server_default=sa.text("5"),
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default=sa.text("'pending'"),
        index=True,
    )
    locked_by: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa.text("0"),
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index(
            "ix_jobs_claim_pending",
            priority.desc(),
            "run_at",
            postgresql_where=sa.text("status = 'pending'"),
        ),
        Index("ix_jobs_stale_heartbeat", "status", "heartbeat_at"),
    )


class IdempotencyRecordModel(Base):
    """Cached response for mutating HTTP endpoints enforcing request deduplication."""

    __tablename__ = "idempotency_records"

    key: Mapped[str] = mapped_column(String(256), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_headers: Mapped[list[list[str]] | None] = mapped_column(
        JSONB, nullable=True
    )
    response_body: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


AUDIT_ACTORS = ("user", "system", "worker")
"""The only actors an audit row can name. The database enforces this set."""


class AuditLogModel(Base):
    """Append-only record of one privileged action.

    There is no update path and no delete path. The table has no ``updated_at``
    column, and neither the service nor the router can modify a stored row. A
    correction is a new row, never an edit.
    """

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    actor: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=sa.text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )

    __table_args__ = (
        CheckConstraint(
            "actor IN ('user', 'system', 'worker')",
            name="audit_log_actor",
        ),
        # The cursor page reads newest first, and it breaks a timestamp tie on
        # the primary key. This index serves that exact ordering.
        Index("ix_audit_log_created_id", created_at.desc(), id.desc()),
        # The optional action filter and the same ordering in one index.
        Index(
            "ix_audit_log_action_created_id",
            "action",
            created_at.desc(),
            id.desc(),
        ),
    )
