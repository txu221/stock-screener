"""Add explicit common-stock classification to the market universe.

Revision ID: 20260825_0032
Revises: 20260825_0031
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260825_0032"
down_revision = "20260825_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "stock_universe",
        sa.Column(
            "is_common_stock",
            sa.Boolean(),
            nullable=True,
            server_default=sa.true(),
        ),
    )
    op.execute(
        sa.text(
            "UPDATE stock_universe SET is_common_stock = "
            "CASE WHEN source = 'manual' THEN false ELSE true END"
        )
    )
    with op.batch_alter_table("stock_universe") as batch_op:
        batch_op.alter_column(
            "is_common_stock",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        )


def downgrade() -> None:
    with op.batch_alter_table("stock_universe") as batch_op:
        batch_op.drop_column("is_common_stock")
