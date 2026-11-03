"""Model prices: the rates that turn tokens into money.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-03

``runs.cost_micros`` was always zero, because nothing knew a rate. This table
holds one row per model, in integer micros per million tokens.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the model_prices table."""
    op.create_table(
        "model_prices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("prompt_micros_per_mtok", sa.Integer(), nullable=False),
        sa.Column("completion_micros_per_mtok", sa.Integer(), nullable=False),
        sa.Column(
            "currency",
            sa.String(length=3),
            server_default="USD",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "prompt_micros_per_mtok >= 0",
            name="ck_model_prices_prompt_non_negative",
        ),
        sa.CheckConstraint(
            "completion_micros_per_mtok >= 0",
            name="ck_model_prices_completion_non_negative",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("model", name="uq_model_prices_model"),
    )


def downgrade() -> None:
    """Drop the model_prices table."""
    op.drop_table("model_prices")
