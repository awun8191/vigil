"""Migrate widgets to A2UI-only surfaces and remove legacy non-A2UI widget rows.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-19 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0014"
down_revision: Union[str, Sequence[str], None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Remove legacy non-A2UI widgets and constrain widget_type to a2ui_surface."""
    op.execute(sa.text("DELETE FROM widgets WHERE widget_type != 'a2ui_surface'"))
    op.drop_constraint("ck_widgets_widget_type", "widgets", type_="check")
    op.create_check_constraint(
        "ck_widgets_widget_type",
        "widgets",
        "widget_type in ('a2ui_surface')",
    )


def downgrade() -> None:
    """Restore the legacy four-type widget_type check constraint."""
    op.execute(
        sa.text(
            "DELETE FROM widgets WHERE widget_type not in "
            "('metric_card', 'data_table', 'task_list', 'status_feed')"
        )
    )
    op.drop_constraint("ck_widgets_widget_type", "widgets", type_="check")
    op.create_check_constraint(
        "ck_widgets_widget_type",
        "widgets",
        "widget_type in ('metric_card', 'data_table', 'task_list', 'status_feed')",
    )
