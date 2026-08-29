"""Real PostgreSQL validation for migration 20260826_0031.

This module is opt-in because it creates and drops one generated schema in the
explicitly supplied Phase 2 PostgreSQL database.
"""

from __future__ import annotations

import importlib.util
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


pytestmark = [
    pytest.mark.integration,
    pytest.mark.postgresql_integration,
]

MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "alembic"
    / "versions"
    / "20260826_0031_add_market_intelligence_phase1.py"
)
NEW_TABLES = {
    "market_intelligence_run_audits",
    "market_intelligence_canonical_bars",
    "market_intelligence_rejections",
    "market_intelligence_sector_snapshots",
}


def _load_migration():
    spec = importlib.util.spec_from_file_location(MIGRATION_PATH.stem, MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(module, connection, operation: str) -> None:
    module.op = Operations(MigrationContext.configure(connection))
    getattr(module, operation)()


def _create_predecessor_schema(connection) -> None:
    connection.execute(
        sa.text(
            """
            CREATE TABLE feature_runs (
                id SERIAL PRIMARY KEY,
                as_of_date DATE NOT NULL,
                run_type TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                published_at TIMESTAMPTZ NULL
            )
            """
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO feature_runs (as_of_date, run_type, status) "
            "VALUES ('2026-08-24', 'daily_snapshot', 'published')"
        )
    )


def _assert_phase1_schema(connection) -> None:
    inspector = sa.inspect(connection)
    assert NEW_TABLES <= set(inspector.get_table_names())

    expected_columns = {
        "market_intelligence_run_audits": {
            "run_id", "idempotency_key", "input_hash", "ingestion_status",
            "provider", "provider_status", "request_failure_json",
            "metric_version", "normalization_version", "price_basis",
            "target_session", "counters_json", "missing_symbols_json",
            "provider_failures_json", "provider_response_at",
            "source_freshness_json", "calculation_timestamp",
            "ingestion_timestamp", "created_at",
        },
        "market_intelligence_canonical_bars": {
            "run_id", "symbol", "trading_date", "provider", "provider_symbol",
            "raw_trading_date", "raw_open", "raw_high", "raw_low", "raw_close",
            "provider_adjusted_close", "adjustment_factor", "adjusted_open",
            "adjusted_high", "adjusted_low", "adjusted_close", "provider_volume",
            "source_timestamp", "ingestion_timestamp", "price_basis",
            "normalization_version",
        },
        "market_intelligence_rejections": {
            "id", "run_id", "provider", "provider_symbol", "symbol",
            "trading_date", "rejection_code", "reason", "raw_evidence_json",
            "ingestion_timestamp",
        },
        "market_intelligence_sector_snapshots": {
            "run_id", "symbol", "trading_date", "asset_type", "sector_name",
            "return_1d", "return_5d", "return_20d", "return_60d",
            "relative_return_vs_spy_1d", "relative_return_vs_spy_5d",
            "relative_return_vs_spy_20d", "relative_return_vs_spy_60d",
            "rvol20", "flow_pressure_1d_proxy", "cmf_5d_proxy",
            "cmf_20d_proxy", "cmf_60d_proxy", "current_ranks_json",
            "previous_ranks_json", "rank_changes_json", "rank_directions_json",
            "provider", "source_freshness_json", "price_basis", "metric_version",
            "calculation_timestamp", "data_quality_status",
        },
    }
    for table, names in expected_columns.items():
        columns = {column["name"]: column for column in inspector.get_columns(table)}
        assert set(columns) == names
        assert columns["run_id"]["nullable"] is False
        assert columns["provider"]["nullable"] is False

        foreign_keys = inspector.get_foreign_keys(table)
        assert len(foreign_keys) == 1
        assert foreign_keys[0]["referred_table"] == "feature_runs"
        assert foreign_keys[0]["options"].get("ondelete") == "CASCADE"

    audit_columns = {
        column["name"]: column
        for column in inspector.get_columns("market_intelligence_run_audits")
    }
    assert isinstance(audit_columns["target_session"]["type"], sa.Date)
    assert isinstance(audit_columns["counters_json"]["type"], sa.JSON)
    assert audit_columns["request_failure_json"]["nullable"] is True
    assert audit_columns["created_at"]["default"] is not None

    canonical_columns = {
        column["name"]: column
        for column in inspector.get_columns("market_intelligence_canonical_bars")
    }
    assert isinstance(canonical_columns["raw_open"]["type"], sa.Numeric)
    assert canonical_columns["source_timestamp"]["nullable"] is True

    snapshot_columns = {
        column["name"]: column
        for column in inspector.get_columns("market_intelligence_sector_snapshots")
    }
    assert isinstance(snapshot_columns["return_60d"]["type"], sa.Float)
    assert isinstance(snapshot_columns["current_ranks_json"]["type"], sa.JSON)
    assert snapshot_columns["sector_name"]["nullable"] is True

    assert inspector.get_pk_constraint("market_intelligence_run_audits")[
        "constrained_columns"
    ] == ["run_id"]
    assert set(
        inspector.get_pk_constraint("market_intelligence_canonical_bars")[
            "constrained_columns"
        ]
    ) == {"run_id", "symbol", "trading_date"}
    assert set(
        inspector.get_pk_constraint("market_intelligence_sector_snapshots")[
            "constrained_columns"
        ]
    ) == {"run_id", "symbol"}

    audit_uniques = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(
            "market_intelligence_run_audits"
        )
    }
    assert "uq_mi_run_audit_idempotency_key" in audit_uniques
    # PostgreSQL can coalesce a UNIQUE constraint with an identical composite
    # primary key, so SQLAlchemy's reflected unique list may omit it and
    # pg_constraint may expose the named contract as either ``u`` or ``p``.
    # The constrained PK columns are asserted above; query pg_constraint here
    # to prove the named uniqueness contract exists without requiring a
    # redundant second unique index.
    snapshot_constraints = {
        row.conname: row.contype
        for row in connection.execute(
            sa.text(
                "SELECT conname, contype "
                "FROM pg_constraint "
                "WHERE conrelid = "
                "'market_intelligence_sector_snapshots'::regclass"
            )
        )
    }
    assert snapshot_constraints.get("uq_mi_snapshot_run_symbol") in {"p", "u"}

    checks = {
        constraint["name"]
        for table in (
            "market_intelligence_run_audits",
            "market_intelligence_sector_snapshots",
        )
        for constraint in inspector.get_check_constraints(table)
    }
    assert {
        "ck_mi_run_audit_ingestion_status",
        "ck_mi_snapshot_asset_type",
        "ck_mi_snapshot_data_quality_status",
    } <= checks

    indexes = {
        index["name"]
        for table in NEW_TABLES
        for index in inspector.get_indexes(table)
    }
    assert {
        "ix_mi_run_audit_latest_attempt",
        "ix_mi_run_audit_session_metric",
        "ix_mi_canonical_bar_symbol_date",
        "ix_mi_rejection_run_code",
        "ix_mi_snapshot_date_metric",
        "ix_mi_snapshot_symbol_date",
    } <= indexes


def test_real_postgresql_upgrade_downgrade_reupgrade_preserves_predecessor(
    phase2_postgresql_engine,
) -> None:
    migration = _load_migration()
    assert migration.revision == "20260826_0031"
    assert migration.down_revision == "20260823_0030"

    with phase2_postgresql_engine.begin() as connection:
        version = connection.execute(sa.text("SELECT version()" )).scalar_one()
        assert "PostgreSQL" in version
        _create_predecessor_schema(connection)

        _run(migration, connection, "upgrade")
        _assert_phase1_schema(connection)
        assert connection.execute(
            sa.text("SELECT count(*) FROM feature_runs")
        ).scalar_one() == 1

        _run(migration, connection, "downgrade")
        inspector = sa.inspect(connection)
        assert NEW_TABLES.isdisjoint(inspector.get_table_names())
        assert "feature_runs" in inspector.get_table_names()
        assert connection.execute(
            sa.text("SELECT count(*) FROM feature_runs")
        ).scalar_one() == 1

        _run(migration, connection, "upgrade")
        _assert_phase1_schema(connection)
        assert connection.execute(
            sa.text("SELECT count(*) FROM feature_runs")
        ).scalar_one() == 1


def test_real_postgresql_v2_price_provenance_migration_preserves_legacy_rows(
    phase2_postgresql_engine,
) -> None:
    migration_path = (
        Path(__file__).resolve().parents[3]
        / "alembic"
        / "versions"
        / "20260828_0034_market_intelligence_production_hardening_v2.py"
    )
    assert migration_path.exists(), "production-hardening v2 migration is missing"
    spec = importlib.util.spec_from_file_location(migration_path.stem, migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    with phase2_postgresql_engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                CREATE TABLE stock_prices (
                    id SERIAL PRIMARY KEY,
                    symbol VARCHAR(20) NOT NULL,
                    date DATE NOT NULL,
                    open DOUBLE PRECISION,
                    high DOUBLE PRECISION,
                    low DOUBLE PRECISION,
                    close DOUBLE PRECISION,
                    volume BIGINT,
                    adj_close DOUBLE PRECISION,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    CONSTRAINT uix_symbol_date UNIQUE (symbol, date)
                )
                """
            )
        )
        connection.execute(
            sa.text(
                """
                CREATE TABLE market_intelligence_canonical_bars (
                    run_id INTEGER NOT NULL,
                    symbol VARCHAR(8) NOT NULL,
                    trading_date DATE NOT NULL,
                    provider VARCHAR(32) NOT NULL,
                    provider_symbol VARCHAR(32) NOT NULL,
                    raw_trading_date TEXT NOT NULL,
                    raw_open NUMERIC(24, 10) NOT NULL,
                    raw_high NUMERIC(24, 10) NOT NULL,
                    raw_low NUMERIC(24, 10) NOT NULL,
                    raw_close NUMERIC(24, 10) NOT NULL,
                    provider_adjusted_close NUMERIC(24, 10) NOT NULL,
                    adjustment_factor NUMERIC(24, 10) NOT NULL,
                    adjusted_open NUMERIC(24, 10) NOT NULL,
                    adjusted_high NUMERIC(24, 10) NOT NULL,
                    adjusted_low NUMERIC(24, 10) NOT NULL,
                    adjusted_close NUMERIC(24, 10) NOT NULL,
                    provider_volume NUMERIC(24, 10) NOT NULL,
                    source_timestamp TIMESTAMPTZ NULL,
                    ingestion_timestamp TIMESTAMPTZ NOT NULL,
                    price_basis VARCHAR(64) NOT NULL,
                    normalization_version VARCHAR(64) NOT NULL,
                    PRIMARY KEY (run_id, symbol, trading_date)
                )
                """
            )
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO stock_prices (symbol, date, close, adj_close)
                VALUES ('AAPL', '2026-08-27', 201.0, 200.5)
                """
            )
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO market_intelligence_canonical_bars (
                    run_id, symbol, trading_date, provider, provider_symbol,
                    raw_trading_date, raw_open, raw_high, raw_low, raw_close,
                    provider_adjusted_close, adjustment_factor, adjusted_open,
                    adjusted_high, adjusted_low, adjusted_close, provider_volume,
                    source_timestamp, ingestion_timestamp, price_basis,
                    normalization_version
                ) VALUES (
                    17, 'XLK', '2026-08-27', 'yahoo', 'XLK',
                    '2026-08-27T00:00:00Z', 100.0, 102.0, 99.0, 101.0,
                    100.5, 0.9950495050, 99.5049505000, 101.4950495100,
                    98.5099009950, 100.5, 1234567.0,
                    '2026-08-28T01:00:00Z', '2026-08-28T02:00:00Z',
                    'yahoo_adjusted_ohlc_provider_volume',
                    'market_intelligence_adjusted_ohlcv_v1'
                )
                """
            )
        )

        _run(migration, connection, "upgrade")
        inspector = sa.inspect(connection)
        columns = {
            column["name"]: column
            for column in inspector.get_columns("stock_prices")
        }
        expected_v2_columns = {
            "provider", "source_timestamp", "normalization_version", "content_hash",
            "revision_number", "price_basis", "reconciled_at", "adjustment_factor",
            "dividend_cash", "split_ratio",
        }
        assert expected_v2_columns <= set(columns)
        assert all(columns[name]["nullable"] for name in expected_v2_columns)
        assert connection.execute(
            sa.text(
                "SELECT symbol, close, adj_close, provider, revision_number, "
                "price_basis, reconciled_at "
                "FROM stock_prices WHERE symbol = 'AAPL'"
            )
        ).one() == ("AAPL", 201.0, 200.5, None, None, None, None)

        revision_indexes = {
            index["name"]
            for index in inspector.get_indexes("stock_price_revisions")
        }
        assert {
            "ix_stock_price_revisions_stock_price_id",
            "ix_stock_price_revisions_symbol_date",
            "ix_stock_price_revisions_symbol_date_hash",
        } <= revision_indexes
        revision_constraints = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("stock_price_revisions")
        }
        assert "uq_stock_price_revision_symbol_date_revision" in revision_constraints
        canonical_columns = {
            column["name"]
            for column in inspector.get_columns("market_intelligence_canonical_bars")
        }
        assert {"dividend_cash", "split_ratio"} <= canonical_columns
        legacy_bar = connection.execute(
            sa.text(
                """
                SELECT run_id, symbol, trading_date, provider, provider_symbol,
                    raw_trading_date, raw_open, raw_high, raw_low, raw_close,
                    provider_adjusted_close, adjustment_factor, adjusted_open,
                    adjusted_high, adjusted_low, adjusted_close, provider_volume,
                    source_timestamp, ingestion_timestamp, price_basis,
                    normalization_version, dividend_cash, split_ratio
                FROM market_intelligence_canonical_bars
                WHERE run_id = 17 AND symbol = 'XLK'
                """
            )
        ).mappings().one()
        assert {
            name: legacy_bar[name]
            for name in (
                "run_id", "symbol", "trading_date", "provider", "provider_symbol",
                "raw_trading_date", "source_timestamp", "ingestion_timestamp",
                "price_basis", "normalization_version",
            )
        } == {
            "run_id": 17,
            "symbol": "XLK",
            "trading_date": date(2026, 8, 27),
            "provider": "yahoo",
            "provider_symbol": "XLK",
            "raw_trading_date": "2026-08-27T00:00:00Z",
            "source_timestamp": datetime(2026, 8, 28, 1, tzinfo=timezone.utc),
            "ingestion_timestamp": datetime(2026, 8, 28, 2, tzinfo=timezone.utc),
            "price_basis": "yahoo_adjusted_ohlc_provider_volume",
            "normalization_version": "market_intelligence_adjusted_ohlcv_v1",
        }
        assert tuple(
            float(legacy_bar[name])
            for name in (
                "raw_open", "raw_high", "raw_low", "raw_close",
                "provider_adjusted_close", "adjustment_factor", "adjusted_open",
                "adjusted_high", "adjusted_low", "adjusted_close", "provider_volume",
            )
        ) == (
            100.0, 102.0, 99.0, 101.0, 100.5, 0.9950495050, 99.5049505000,
            101.4950495100, 98.5099009950, 100.5, 1234567.0,
        )
        assert legacy_bar["dividend_cash"] is None
        assert legacy_bar["split_ratio"] is None

        trigger_names = set(
            connection.execute(
                sa.text(
                    """
                    SELECT tgname FROM pg_trigger
                    WHERE tgrelid = 'stock_price_revisions'::regclass
                    AND NOT tgisinternal
                    """
                )
            ).scalars()
        )
        assert trigger_names == {"trg_stock_price_revisions_append_only"}
        trigger_definition = connection.execute(
            sa.text(
                """
                SELECT pg_get_triggerdef(oid) FROM pg_trigger
                WHERE tgname = 'trg_stock_price_revisions_append_only'
                """
            )
        ).scalar_one()
        assert "BEFORE DELETE OR UPDATE" in trigger_definition
        assert "stock_price_revisions_reject_mutation" in trigger_definition
        connection.execute(
            sa.text(
                """
                INSERT INTO stock_price_revisions (symbol, date, revision_number)
                VALUES ('AAPL', '2026-08-27', 1)
                """
            )
        )
        with pytest.raises(sa.exc.DatabaseError, match="append-only"):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        "UPDATE stock_price_revisions SET content_hash = 'changed' "
                        "WHERE symbol = 'AAPL'"
                    )
                )
        with pytest.raises(sa.exc.DatabaseError, match="append-only"):
            with connection.begin_nested():
                connection.execute(
                    sa.text("DELETE FROM stock_price_revisions WHERE symbol = 'AAPL'")
                )

        _run(migration, connection, "downgrade")
        assert "stock_price_revisions" not in sa.inspect(connection).get_table_names()
        assert connection.execute(
            sa.text("SELECT to_regprocedure('stock_price_revisions_reject_mutation()')")
        ).scalar_one() is None
        assert connection.execute(
            sa.text("SELECT symbol, close, adj_close FROM stock_prices WHERE symbol = 'AAPL'")
        ).one() == ("AAPL", 201.0, 200.5)
        legacy_bar_after_downgrade = connection.execute(
            sa.text(
                """
                SELECT raw_close, price_basis, normalization_version
                FROM market_intelligence_canonical_bars
                WHERE run_id = 17 AND symbol = 'XLK'
                """
            )
        ).mappings().one()
        assert float(legacy_bar_after_downgrade["raw_close"]) == 101.0
        assert legacy_bar_after_downgrade["price_basis"] == "yahoo_adjusted_ohlc_provider_volume"
        assert legacy_bar_after_downgrade["normalization_version"] == "market_intelligence_adjusted_ohlcv_v1"
