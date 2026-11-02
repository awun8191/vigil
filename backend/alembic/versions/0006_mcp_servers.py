"""External MCP servers: the registry table for third party tool providers.

Revision ID: 0006
Revises: 0003
Create Date: 2026-11-02 12:00:00.000000

A registered server has a unique name, a transport (``stdio`` or ``sse``),
a command or a URL, an enabled flag, and a creation timestamp. Revisions
0004 and 0005 belong to sibling cards on other branches and chain onto
0003 ahead of this revision at merge time.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the mcp_servers registry table."""
    op.create_table(
        "mcp_servers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("transport", sa.String(length=16), nullable=False),
        sa.Column("command", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "transport IN ('stdio', 'sse')", name="ck_mcp_servers_transport"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_mcp_servers_name"),
    )


def downgrade() -> None:
    """Drop the mcp_servers registry table."""
    op.drop_table("mcp_servers")
