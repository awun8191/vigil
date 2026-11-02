"""Connectors schemas: linked accounts, ingested items, and delta sync state."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
import uuid

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.schemas.base import Base

if TYPE_CHECKING:
    from src.schemas.identity import UserModel
    from src.schemas.triage import CardModel, TriageDecisionModel


class ConnectedAccountModel(Base):
    """External third-party service account (e.g. Gmail) linked by a user."""

    __tablename__ = "connected_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    connector_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    account_label: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_credentials: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=sa.text("'[]'::jsonb"),
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="active",
        server_default=sa.text("'active'"),
        index=True,
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
        UniqueConstraint(
            "user_id",
            "connector_id",
            "account_label",
            name="uq_connected_accounts_user_conn_label",
        ),
    )

    user: Mapped[UserModel] = relationship(
        "UserModel", back_populates="connected_accounts"
    )
    tools: Mapped[list[ConnectorToolModel]] = relationship(
        "ConnectorToolModel", back_populates="account", cascade="all, delete-orphan"
    )
    items: Mapped[list[ConnectorItemModel]] = relationship(
        "ConnectorItemModel", back_populates="account", cascade="all, delete-orphan"
    )
    sync_state: Mapped[ConnectorSyncStateModel | None] = relationship(
        "ConnectorSyncStateModel",
        back_populates="account",
        uselist=False,
        cascade="all, delete-orphan",
    )


class ConnectorToolModel(Base):
    """Tool exposed by a connected account, with its approval level."""

    __tablename__ = "connector_tools"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    connector_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("connected_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=sa.text("true"),
    )

    __table_args__ = (
        UniqueConstraint(
            "connector_account_id",
            "tool_name",
            name="uq_connector_tools_account_tool",
        ),
        # The "ck" naming convention adds the ck_connector_tools_ prefix.
        CheckConstraint("level IN ('read', 'local', 'external')", name="level"),
    )

    account: Mapped[ConnectedAccountModel] = relationship(
        "ConnectedAccountModel", back_populates="tools"
    )


class ConnectorItemModel(Base):
    """Raw ingested external message, event, or thread item from a connector."""

    __tablename__ = "connector_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    connector_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("connected_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    sender: Mapped[str] = mapped_column(String(255), nullable=False)
    recipients: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=sa.text("'[]'::jsonb"),
    )
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    labels: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=sa.text("'[]'::jsonb"),
    )
    raw_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "connector_account_id",
            "external_id",
            name="uq_connector_items_account_external_id",
        ),
        Index(
            "ix_connector_items_account_received",
            "connector_account_id",
            "received_at",
        ),
    )

    account: Mapped[ConnectedAccountModel] = relationship(
        "ConnectedAccountModel", back_populates="items"
    )
    triage_decision: Mapped[TriageDecisionModel | None] = relationship(
        "TriageDecisionModel",
        back_populates="item",
        uselist=False,
        cascade="all, delete-orphan",
    )
    cards: Mapped[list[CardModel]] = relationship(
        "CardModel", back_populates="item"
    )


class ConnectorSyncStateModel(Base):
    """High-water mark cursor for incremental delta sync tracking."""

    __tablename__ = "connector_sync_state"

    connector_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("connected_accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    cursor: Mapped[str] = mapped_column(String(255), nullable=False)
    last_success_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    account: Mapped[ConnectedAccountModel] = relationship(
        "ConnectedAccountModel", back_populates="sync_state"
    )


class MCPServerModel(Base):
    """Third party MCP server attached by a user over stdio or SSE."""

    __tablename__ = "mcp_servers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    transport: Mapped[str] = mapped_column(String(16), nullable=False)
    command: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=sa.text("true"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint("transport IN ('stdio', 'sse')", name="transport"),
    )
