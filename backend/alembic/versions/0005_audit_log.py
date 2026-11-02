"""Audit log: the append-only record of a privileged action.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-02 12:00:00.000000

Section 5.9 of the architecture lists ``audit_log`` as a missing table. This
revision adds it with the six agreed columns. The table has no update path and
no delete path, because the application never edits a stored event. A
correction is a new row.

The index order matches the read path. ``GET /api/v1/audit`` returns a cursor
page newest first, and it breaks a timestamp tie on ``id``. The second index
serves the same order with the optional ``action`` filter.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the audit log table and its two read indexes."""
    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "actor IN ('user', 'system', 'worker')",
            name="ck_audit_log_actor",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
    )

    op.create_index(
        "ix_audit_log_created_id",
        "audit_log",
        [sa.text("created_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_audit_log_action_created_id",
        "audit_log",
        ["action", sa.text("created_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_audit_log_action",
        "audit_log",
        ["action"],
    )


def downgrade() -> None:
    """Drop the audit log table and its indexes."""
    op.drop_index("ix_audit_log_action", table_name="audit_log")
    op.drop_index("ix_audit_log_action_created_id", table_name="audit_log")
    op.drop_index("ix_audit_log_created_id", table_name="audit_log")
    op.drop_table("audit_log")
