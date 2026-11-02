"""Skills registry: a durable row for each installed skill.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-02 12:00:00.000000

S-01 moves the skill registry out of the process and into PostgreSQL. A row
holds the manifest fields, the source, the pinned commit, the enabled flag, and
the extracted ``storage_path``. ``name`` is unique and the pair
``(name, version)`` is unique. Enable and disable change ``enabled`` only.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the skills table, its constraints, and its index."""
    op.create_table(
        "skills",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'builtin'"),
        ),
        sa.Column("commit", sa.String(length=64), nullable=True),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("storage_path", sa.Text(), nullable=False),
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
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_skills_name_not_blank"),
        sa.CheckConstraint(
            "source IN ('builtin', 'git', 'upload')",
            name="ck_skills_source_allowed",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_skills"),
        sa.UniqueConstraint("name", name="uq_skills_name"),
        sa.UniqueConstraint("name", "version", name="uq_skills_name_version"),
    )
    op.create_index("ix_skills_enabled", "skills", ["enabled"])


def downgrade() -> None:
    """Drop the skills index and table."""
    op.drop_index("ix_skills_enabled", table_name="skills")
    op.drop_table("skills")
