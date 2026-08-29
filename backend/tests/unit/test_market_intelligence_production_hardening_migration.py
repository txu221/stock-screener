"""Schema contract for corporate-action price provenance."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260828_0034_market_intelligence_production_hardening_v2.py"
)

PRICE_PROVENANCE_COLUMNS = {
    "provider",
    "source_timestamp",
    "normalization_version",
    "content_hash",
    "revision_number",
    "adjustment_factor",
    "dividend_cash",
    "split_ratio",
}
CANONICAL_ACTION_COLUMNS = {"dividend_cash", "split_ratio"}
REVISION_TABLE = "stock_price_revisions"


def _load_migration():
    assert MIGRATION_PATH.exists(), "production-hardening v2 migration is missing"
    spec = importlib.util.spec_from_file_location(MIGRATION_PATH.stem, MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(module, connection, operation: str) -> None:
    module.op = Operations(MigrationContext.configure(connection))
    getattr(module, operation)()


def _create_predecessor_schema(connection) -> None:
    metadata = sa.MetaData()
    stock_prices = sa.Table(
        "stock_prices",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("open", sa.Float),
        sa.Column("high", sa.Float),
        sa.Column("low", sa.Float),
        sa.Column("close", sa.Float),
        sa.Column("volume", sa.BigInteger),
        sa.Column("adj_close", sa.Float),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("symbol", "date", name="uix_symbol_date"),
    )
    sa.Table(
        "market_intelligence_canonical_bars",
        metadata,
        sa.Column("run_id", sa.Integer, primary_key=True),
        sa.Column("symbol", sa.String(length=8), primary_key=True),
        sa.Column("trading_date", sa.Date, primary_key=True),
    )
    metadata.create_all(connection)
    connection.execute(
        stock_prices.insert().values(
            symbol="AAPL",
            date=date(2026, 8, 27),
            open=200.0,
            high=202.0,
            low=198.0,
            close=201.0,
            volume=1_000,
            adj_close=200.5,
        )
    )


def test_v2_price_provenance_migration_is_additive_and_reversible() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    migration = _load_migration()

    assert migration.revision == "20260828_0034"
    assert migration.down_revision == "20260828_0033"

    with engine.begin() as connection:
        _create_predecessor_schema(connection)
        _run(migration, connection, "upgrade")

        inspector = sa.inspect(connection)
        stock_price_columns = {
            column["name"]: column
            for column in inspector.get_columns("stock_prices")
        }
        assert PRICE_PROVENANCE_COLUMNS <= set(stock_price_columns)
        assert all(stock_price_columns[name]["nullable"] for name in PRICE_PROVENANCE_COLUMNS)
        assert connection.execute(
            sa.text(
                "SELECT symbol, close, adj_close, provider, revision_number "
                "FROM stock_prices WHERE symbol = 'AAPL'"
            )
        ).one() == ("AAPL", 201.0, 200.5, None, None)

        canonical_columns = {
            column["name"]
            for column in inspector.get_columns("market_intelligence_canonical_bars")
        }
        assert CANONICAL_ACTION_COLUMNS <= canonical_columns

        revision_columns = {
            column["name"]
            for column in inspector.get_columns(REVISION_TABLE)
        }
        assert {
            "id",
            "stock_price_id",
            "symbol",
            "date",
            "revision_number",
            "content_hash",
            "adjustment_factor",
            "dividend_cash",
            "split_ratio",
            "provider",
            "source_timestamp",
            "normalization_version",
            "created_at",
        } <= revision_columns

        indexes = {
            index["name"]
            for index in inspector.get_indexes(REVISION_TABLE)
        }
        assert "ix_stock_price_revisions_symbol_date" in indexes
        uniqueness = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints(REVISION_TABLE)
        }
        assert "uq_stock_price_revision_symbol_date_revision" in uniqueness

        _run(migration, connection, "downgrade")
        downgraded_columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("stock_prices")
        }
        assert PRICE_PROVENANCE_COLUMNS.isdisjoint(downgraded_columns)
        assert REVISION_TABLE not in sa.inspect(connection).get_table_names()
        assert connection.execute(
            sa.text("SELECT symbol, close, adj_close FROM stock_prices WHERE symbol = 'AAPL'")
        ).one() == ("AAPL", 201.0, 200.5)
