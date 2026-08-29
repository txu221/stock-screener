"""Schema contract for corporate-action price provenance."""

from __future__ import annotations

import importlib.util
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest

from app.models.stock import StockPriceRevision


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
    "price_basis",
    "reconciled_at",
    "adjustment_factor",
    "dividend_cash",
    "split_ratio",
}
CANONICAL_ACTION_COLUMNS = {"dividend_cash", "split_ratio"}
REVISION_TABLE = "stock_price_revisions"
REVISION_INDEXES = {
    "ix_stock_price_revisions_stock_price_id",
    "ix_stock_price_revisions_symbol_date",
    "ix_stock_price_revisions_symbol_date_hash",
}


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
    canonical_bars = sa.Table(
        "market_intelligence_canonical_bars",
        metadata,
        sa.Column("run_id", sa.Integer, primary_key=True),
        sa.Column("symbol", sa.String(length=8), primary_key=True),
        sa.Column("trading_date", sa.Date, primary_key=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_symbol", sa.String(length=32), nullable=False),
        sa.Column("raw_trading_date", sa.Text, nullable=False),
        sa.Column("raw_open", sa.Numeric(24, 10), nullable=False),
        sa.Column("raw_high", sa.Numeric(24, 10), nullable=False),
        sa.Column("raw_low", sa.Numeric(24, 10), nullable=False),
        sa.Column("raw_close", sa.Numeric(24, 10), nullable=False),
        sa.Column("provider_adjusted_close", sa.Numeric(24, 10), nullable=False),
        sa.Column("adjustment_factor", sa.Numeric(24, 10), nullable=False),
        sa.Column("adjusted_open", sa.Numeric(24, 10), nullable=False),
        sa.Column("adjusted_high", sa.Numeric(24, 10), nullable=False),
        sa.Column("adjusted_low", sa.Numeric(24, 10), nullable=False),
        sa.Column("adjusted_close", sa.Numeric(24, 10), nullable=False),
        sa.Column("provider_volume", sa.Numeric(24, 10), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingestion_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price_basis", sa.String(length=64), nullable=False),
        sa.Column("normalization_version", sa.String(length=64), nullable=False),
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
    connection.execute(
        canonical_bars.insert().values(
            run_id=17,
            symbol="XLK",
            trading_date=date(2026, 8, 27),
            provider="yahoo",
            provider_symbol="XLK",
            raw_trading_date="2026-08-27T00:00:00Z",
            raw_open=Decimal("100.0000000000"),
            raw_high=Decimal("102.0000000000"),
            raw_low=Decimal("99.0000000000"),
            raw_close=Decimal("101.0000000000"),
            provider_adjusted_close=Decimal("100.5000000000"),
            adjustment_factor=Decimal("0.9950495050"),
            adjusted_open=Decimal("99.5049505000"),
            adjusted_high=Decimal("101.4950495100"),
            adjusted_low=Decimal("98.5099009950"),
            adjusted_close=Decimal("100.5000000000"),
            provider_volume=Decimal("1234567.0000000000"),
            source_timestamp=datetime(2026, 8, 28, 1, tzinfo=timezone.utc),
            ingestion_timestamp=datetime(2026, 8, 28, 2, tzinfo=timezone.utc),
            price_basis="yahoo_adjusted_ohlc_provider_volume",
            normalization_version="market_intelligence_adjusted_ohlcv_v1",
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
                "SELECT symbol, close, adj_close, provider, revision_number, "
                "price_basis, reconciled_at "
                "FROM stock_prices WHERE symbol = 'AAPL'"
            )
        ).one() == ("AAPL", 201.0, 200.5, None, None, None, None)

        canonical_columns = {
            column["name"]
            for column in inspector.get_columns("market_intelligence_canonical_bars")
        }
        assert CANONICAL_ACTION_COLUMNS <= canonical_columns
        canonical_bar = sa.Table(
            "market_intelligence_canonical_bars",
            sa.MetaData(),
            autoload_with=connection,
        )
        legacy_bar = connection.execute(
            sa.select(canonical_bar).where(canonical_bar.c.run_id == 17)
        ).mappings().one()
        assert {
            name: legacy_bar[name]
            for name in (
                "symbol", "provider", "provider_symbol", "raw_trading_date",
                "raw_open", "raw_high", "raw_low", "raw_close",
                "provider_adjusted_close", "adjustment_factor", "adjusted_open",
                "adjusted_high", "adjusted_low", "adjusted_close", "provider_volume",
                "price_basis", "normalization_version",
            )
        } == {
            "symbol": "XLK",
            "provider": "yahoo",
            "provider_symbol": "XLK",
            "raw_trading_date": "2026-08-27T00:00:00Z",
            "raw_open": Decimal("100.0000000000"),
            "raw_high": Decimal("102.0000000000"),
            "raw_low": Decimal("99.0000000000"),
            "raw_close": Decimal("101.0000000000"),
            "provider_adjusted_close": Decimal("100.5000000000"),
            "adjustment_factor": Decimal("0.9950495050"),
            "adjusted_open": Decimal("99.5049505000"),
            "adjusted_high": Decimal("101.4950495100"),
            "adjusted_low": Decimal("98.5099009950"),
            "adjusted_close": Decimal("100.5000000000"),
            "provider_volume": Decimal("1234567.0000000000"),
            "price_basis": "yahoo_adjusted_ohlc_provider_volume",
            "normalization_version": "market_intelligence_adjusted_ohlcv_v1",
        }
        assert legacy_bar["dividend_cash"] is None
        assert legacy_bar["split_ratio"] is None

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
            "price_basis",
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
        assert REVISION_INDEXES <= indexes
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
        downgraded_canonical_bar = sa.Table(
            "market_intelligence_canonical_bars",
            sa.MetaData(),
            autoload_with=connection,
        )
        legacy_bar_after_downgrade = connection.execute(
            sa.select(downgraded_canonical_bar).where(downgraded_canonical_bar.c.run_id == 17)
        ).mappings().one()
        assert legacy_bar_after_downgrade["raw_close"] == Decimal("101.0000000000")
        assert legacy_bar_after_downgrade["price_basis"] == "yahoo_adjusted_ohlc_provider_volume"
        assert legacy_bar_after_downgrade["normalization_version"] == "market_intelligence_adjusted_ohlcv_v1"


def test_stock_price_revisions_reject_orm_updates_and_deletes(db_session) -> None:
    revision = StockPriceRevision(
        symbol="AAPL",
        date=date(2026, 8, 27),
        revision_number=1,
        content_hash="original",
    )
    db_session.add(revision)
    db_session.commit()

    revision.content_hash = "changed"
    with pytest.raises(sa.exc.InvalidRequestError, match="append-only"):
        db_session.flush()
    db_session.rollback()

    db_session.delete(db_session.get(StockPriceRevision, revision.id))
    with pytest.raises(sa.exc.InvalidRequestError, match="append-only"):
        db_session.flush()


def test_stock_price_revision_primary_key_does_not_create_a_redundant_index() -> None:
    indexes = {index.name for index in StockPriceRevision.__table__.indexes}

    assert "ix_stock_price_revisions_id" not in indexes
