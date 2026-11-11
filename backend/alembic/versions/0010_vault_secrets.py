"""Durable encrypted key vault.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-11 12:00:00.000000

One row per (user, key name). The value is the AES-256-GCM ciphertext produced
by ``config.security.encrypt_secret``; no plaintext column exists. The unique
constraint makes a re-save an update of the same logical secret.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the vault_secrets table."""
    op.create_table(
        "vault_secrets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key_name", sa.String(length=255), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "key_name", name="uq_vault_secrets_user_key"),
    )
    op.create_index(
        "ix_vault_secrets_user_id", "vault_secrets", ["user_id"], unique=False
    )


def downgrade() -> None:
    """Drop the vault_secrets table."""
    op.drop_index("ix_vault_secrets_user_id", table_name="vault_secrets")
    op.drop_table("vault_secrets")
