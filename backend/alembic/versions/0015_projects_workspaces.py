"""Create projects table and add runs.project_id foreign key.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-19 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: Union[str, Sequence[str], None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create projects workspace table and link runs.project_id."""
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("workspace_path", sa.String(length=512), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "slug", name="uq_projects_user_slug"),
    )
    op.create_index("ix_projects_user_id", "projects", ["user_id"], unique=False)

    op.add_column(
        "runs",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_runs_project_id", "runs", ["project_id"], unique=False)
    op.create_foreign_key(
        "fk_runs_project_id",
        "runs",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Drop runs.project_id and projects table."""
    op.drop_constraint("fk_runs_project_id", "runs", type_="foreignkey")
    op.drop_index("ix_runs_project_id", table_name="runs")
    op.drop_column("runs", "project_id")
    op.drop_index("ix_projects_user_id", table_name="projects")
    op.drop_table("projects")
