"""Widget schemas: one dashboard panel row owned by one user."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.schemas.base import Base


class WidgetModel(Base):
    """Postgres-backed agent dashboard panel scoped to one user.

    The model never sends raw cron: ``refresh_interval_minutes`` holds one of
    the allowlisted values and the server computes ``next_refresh_at``.
    """

    __tablename__ = "widgets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    widget_type: Mapped[str] = mapped_column(Text, nullable=False)
    data_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=sa.text("'{}'::jsonb"),
    )
    refresh_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    refresh_interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_refresh_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=sa.text("1"),
    )
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

    __table_args__ = (
        CheckConstraint(
            "widget_type in ('metric_card', 'data_table', 'task_list', 'status_feed')",
            name="ck_widgets_widget_type",
        ),
        CheckConstraint(
            "refresh_interval_minutes in (15, 60, 240, 1440)",
            name="ck_widgets_refresh_interval",
        ),
        Index("ix_widgets_user_id", "user_id"),
        Index("ix_widgets_user_refresh", "user_id", "next_refresh_at"),
    )
