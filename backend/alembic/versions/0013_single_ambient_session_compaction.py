"""Single ambient session and rolling compaction columns on threads.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-19 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add single-session kind, rolling summary, and compaction cursor columns to threads."""
    op.add_column(
        "threads",
        sa.Column(
            "kind",
            sa.String(length=32),
            nullable=False,
            server_default="thread",
        ),
    )
    op.add_column(
        "threads",
        sa.Column("rolling_summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "threads",
        sa.Column(
            "compacted_through_message_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "threads",
        sa.Column(
            "compacted_through_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    # Keep at most one ambient thread per user before adding the partial unique index.
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY user_id
                    ORDER BY updated_at DESC, created_at DESC, id DESC
                ) AS rn
            FROM threads
        )
        UPDATE threads
        SET kind = CASE WHEN ranked.rn = 1 THEN 'ambient' ELSE 'legacy' END
        FROM ranked
        WHERE threads.id = ranked.id
        """
    )
    op.create_index("ix_threads_kind", "threads", ["kind"], unique=False)
    op.create_index(
        "uq_threads_user_ambient",
        "threads",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'ambient'"),
    )


def downgrade() -> None:
    """Remove single-session and rolling compaction columns from threads."""
    op.drop_index("uq_threads_user_ambient", table_name="threads")
    op.drop_index("ix_threads_kind", table_name="threads")
    op.drop_column("threads", "compacted_through_at")
    op.drop_column("threads", "compacted_through_message_id")
    op.drop_column("threads", "rolling_summary")
    op.drop_column("threads", "kind")
