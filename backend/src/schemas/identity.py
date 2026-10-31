"""Identity schemas: users and devices."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
import uuid

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.schemas.base import Base

if TYPE_CHECKING:
    from src.schemas.connectors import ConnectedAccountModel
    from src.schemas.memory import MemoryChunkModel
    from src.schemas.runtime import ThreadModel
    from src.schemas.triage import CardModel


class UserModel(Base):
    """Top-level user account and identity settings."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(
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
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    devices: Mapped[list[DeviceModel]] = relationship(
        "DeviceModel", back_populates="user", cascade="all, delete-orphan"
    )
    connected_accounts: Mapped[list[ConnectedAccountModel]] = relationship(
        "ConnectedAccountModel", back_populates="user", cascade="all, delete-orphan"
    )
    cards: Mapped[list[CardModel]] = relationship(
        "CardModel", back_populates="user", cascade="all, delete-orphan"
    )
    threads: Mapped[list[ThreadModel]] = relationship(
        "ThreadModel", back_populates="user", cascade="all, delete-orphan"
    )
    memory_chunks: Mapped[list[MemoryChunkModel]] = relationship(
        "MemoryChunkModel", back_populates="user", cascade="all, delete-orphan"
    )


class DeviceModel(Base):
    """Client device paired with a user, authenticated via long-lived bearer token hash."""

    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    push_token: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    user: Mapped[UserModel] = relationship("UserModel", back_populates="devices")
