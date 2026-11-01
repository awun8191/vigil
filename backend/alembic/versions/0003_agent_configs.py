"""Agent configurations: the template table that ``runs.config_id`` points at.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-01 12:00:00.000000

Decision 7 of V-06 adds ``agent_configs`` and the foreign key from
``runs.config_id``. The table holds the reusable agent template: prompt, model,
tools, skills, memory scopes, approval policy and limits. ``limits`` must hold
``max_cost_micros``, and the check constraint enforces that key.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LIMITS_DEFAULT = "'{\"max_cost_micros\": 0}'::jsonb"


def upgrade() -> None:
    """Create the agent configuration table and the run foreign key."""
    op.create_table(
        "agent_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column(
            "tools",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "skills",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "memory_scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "approval_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "limits",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text(LIMITS_DEFAULT),
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
        sa.CheckConstraint(
            "limits ? 'max_cost_micros'",
            name="ck_agent_configs_limits_max_cost_micros",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_configs"),
        sa.UniqueConstraint("name", name="uq_agent_configs_name"),
    )

    op.create_foreign_key(
        "fk_runs_config_id_agent_configs",
        "runs",
        "agent_configs",
        ["config_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Drop the run foreign key and the agent configuration table."""
    op.drop_constraint("fk_runs_config_id_agent_configs", "runs", type_="foreignkey")
    op.drop_table("agent_configs")
