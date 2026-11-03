"""Run steps: the durable journal of one run.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-03

The loop recorded stages in process memory, so the step history and the SSE
resume point died with the worker. This table makes every stage a row.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the run_steps table."""
    op.create_table(
        "run_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "step_number", name="uq_run_steps_run_step"),
    )
    op.create_index("ix_run_steps_run_id", "run_steps", ["run_id"], unique=False)
    op.create_index(
        "ix_run_steps_run_step", "run_steps", ["run_id", "step_number"], unique=False
    )


def downgrade() -> None:
    """Drop the run_steps table."""
    op.drop_index("ix_run_steps_run_step", table_name="run_steps")
    op.drop_index("ix_run_steps_run_id", table_name="run_steps")
    op.drop_table("run_steps")
