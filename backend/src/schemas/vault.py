"""Vault schemas: encrypted per-user credential storage."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
import uuid

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.schemas.base import Base

if TYPE_CHECKING:
    from src.schemas.identity import UserModel


class VaultSecretModel(Base):
    """One AES-256-GCM ciphertext, owned by one user and one key name.

    The plaintext never reaches this table. ``ciphertext`` is the base64url
    payload that ``src.config.security.encrypt_secret`` produces, which already
    carries its own nonce and authentication tag.
    """

    __tablename__ = "vault_secrets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key_name: Mapped[str] = mapped_column(String(255), nullable=False)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
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

    user: Mapped[UserModel] = relationship("UserModel", back_populates="vault_secrets")

    __table_args__ = (
        UniqueConstraint("user_id", "key_name", name="uq_vault_secrets_user_key"),
    )
