"""Card proactivity tags and active verbs.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-10

Supports Hark Pro proactivity tags and active verb pills on Action Cards.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add proactivity_tag and verb columns to cards and card_actions."""
    op.add_column("cards", sa.Column("proactivity_tag", sa.String(length=64), nullable=True))
    op.create_index("ix_cards_proactivity_tag", "cards", ["proactivity_tag"], unique=False)
    op.add_column("cards", sa.Column("verb", sa.String(length=64), nullable=True))
    op.add_column("card_actions", sa.Column("verb", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Revert proactivity_tag and verb columns."""
    op.drop_column("card_actions", "verb")
    op.drop_column("cards", "verb")
    op.drop_index("ix_cards_proactivity_tag", table_name="cards")
    op.drop_column("cards", "proactivity_tag")
