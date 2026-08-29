"""Add corporate-action provenance to current prices and revision evidence.

Revision ID: 20260828_0034
Revises: 20260828_0033
Create Date: 2026-08-28
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op


revision = "20260828_0034"
down_revision = "20260828_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stock_prices", sa.Column("provider", sa.String(length=32), nullable=True))
    op.add_column(
        "stock_prices",
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "stock_prices",
        sa.Column("normalization_version", sa.String(length=64), nullable=True),
    )
    op.add_column("stock_prices", sa.Column("content_hash", sa.String(length=64), nullable=True))
    op.add_column("stock_prices", sa.Column("revision_number", sa.Integer(), nullable=True))
    op.add_column("stock_prices", sa.Column("adjustment_factor", sa.Float(), nullable=True))
    op.add_column("stock_prices", sa.Column("dividend_cash", sa.Float(), nullable=True))
    op.add_column("stock_prices", sa.Column("split_ratio", sa.Float(), nullable=True))

    op.create_table(
        "stock_price_revisions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "stock_price_id",
            sa.Integer(),
            sa.ForeignKey("stock_prices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("open", sa.Float(), nullable=True),
        sa.Column("high", sa.Float(), nullable=True),
        sa.Column("low", sa.Float(), nullable=True),
        sa.Column("close", sa.Float(), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("adj_close", sa.Float(), nullable=True),
        sa.Column("adjustment_factor", sa.Float(), nullable=True),
        sa.Column("dividend_cash", sa.Float(), nullable=True),
        sa.Column("split_ratio", sa.Float(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("normalization_version", sa.String(length=64), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "symbol",
            "date",
            "revision_number",
            name="uq_stock_price_revision_symbol_date_revision",
        ),
    )
    op.create_index(
        "ix_stock_price_revisions_stock_price_id",
        "stock_price_revisions",
        ["stock_price_id"],
    )
    op.create_index(
        "ix_stock_price_revisions_symbol_date",
        "stock_price_revisions",
        ["symbol", "date"],
    )
    op.create_index(
        "ix_stock_price_revisions_symbol_date_hash",
        "stock_price_revisions",
        ["symbol", "date", "content_hash"],
    )

    op.add_column(
        "market_intelligence_canonical_bars",
        sa.Column("dividend_cash", sa.Numeric(24, 10), nullable=True),
    )
    op.add_column(
        "market_intelligence_canonical_bars",
        sa.Column("split_ratio", sa.Numeric(24, 10), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("market_intelligence_canonical_bars", "split_ratio")
    op.drop_column("market_intelligence_canonical_bars", "dividend_cash")

    op.drop_index(
        "ix_stock_price_revisions_symbol_date_hash",
        table_name="stock_price_revisions",
    )
    op.drop_index(
        "ix_stock_price_revisions_symbol_date",
        table_name="stock_price_revisions",
    )
    op.drop_index(
        "ix_stock_price_revisions_stock_price_id",
        table_name="stock_price_revisions",
    )
    op.drop_table("stock_price_revisions")

    op.drop_column("stock_prices", "split_ratio")
    op.drop_column("stock_prices", "dividend_cash")
    op.drop_column("stock_prices", "adjustment_factor")
    op.drop_column("stock_prices", "revision_number")
    op.drop_column("stock_prices", "content_hash")
    op.drop_column("stock_prices", "normalization_version")
    op.drop_column("stock_prices", "source_timestamp")
    op.drop_column("stock_prices", "provider")
