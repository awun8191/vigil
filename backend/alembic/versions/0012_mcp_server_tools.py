"""Persist discovered MCP server tools and custom headers.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-16 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add tools_json and headers_json columns to mcp_servers."""
    op.add_column(
        "mcp_servers",
        sa.Column(
            "tools_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "mcp_servers",
        sa.Column(
            "headers_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    """Remove tools_json and headers_json columns from mcp_servers."""
    op.drop_column("mcp_servers", "headers_json")
    op.drop_column("mcp_servers", "tools_json")
