"""Add shared revision-2 market-breadth metrics.

Revision ID: 20260825_0031
Revises: 20260823_0030
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260825_0031"
down_revision = "20260823_0030"
branch_labels = None
depends_on = None


_INTEGER_COLUMNS = (
    "advancing_count",
    "declining_count",
    "unchanged_count",
    "new_high_52week_count",
    "new_low_52week_count",
    "t2108_count",
    "atr_10x_extension_count",
    "broad_universe_count",
    "advance_decline_eligible_count",
    "stockbee_daily_eligible_count",
    "stockbee_month_eligible_count",
    "stockbee_34day_eligible_count",
    "stockbee_quarter_eligible_count",
    "t2108_eligible_count",
    "high_low_52week_eligible_count",
    "atr_extension_eligible_count",
    "calculation_revision",
)


def upgrade() -> None:
    for column_name in _INTEGER_COLUMNS:
        op.add_column(
            "market_breadth",
            sa.Column(column_name, sa.Integer(), nullable=True),
        )
    op.add_column(
        "market_breadth",
        sa.Column("t2108_pct", sa.Float(), nullable=True),
    )
    op.add_column(
        "market_breadth",
        sa.Column(
            "stockbee_eligibility_signature",
            sa.String(length=64),
            nullable=True,
        ),
    )


def downgrade() -> None:
    with op.batch_alter_table("market_breadth") as batch_op:
        batch_op.drop_column("stockbee_eligibility_signature")
        batch_op.drop_column("t2108_pct")
        for column_name in reversed(_INTEGER_COLUMNS):
            batch_op.drop_column(column_name)
