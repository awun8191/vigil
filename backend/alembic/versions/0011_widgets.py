"""Agent dashboard widgets.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-12 00:00:00.000000

One row per user dashboard panel. The model sends only a refresh interval
from an allowlist, the server computes ``next_refresh_at``. Free text stays
in ``prompt_hint`` and is never executed as code.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the widgets table with its checks and indexes."""
    op.create_table(
        "widgets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("widget_type", sa.Text(), nullable=False),
        sa.Column("data_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("refresh_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("refresh_interval_minutes", sa.Integer(), nullable=True),
        sa.Column("next_refresh_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
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
        sa.CheckConstraint(
            "widget_type in ('metric_card', 'data_table', 'task_list', 'status_feed')",
            name="ck_widgets_widget_type",
        ),
        sa.CheckConstraint(
            "refresh_interval_minutes in (15, 60, 240, 1440)",
            name="ck_widgets_refresh_interval",
        ),
    )
    op.create_index("ix_widgets_user_id", "widgets", ["user_id"], unique=False)
    op.create_index(
        "ix_widgets_user_refresh", "widgets", ["user_id", "next_refresh_at"], unique=False
    )


def downgrade() -> None:
    """Drop the widgets indexes, then the table."""
    op.drop_index("ix_widgets_user_refresh", table_name="widgets")
    op.drop_index("ix_widgets_user_id", table_name="widgets")
    op.drop_table("widgets")
