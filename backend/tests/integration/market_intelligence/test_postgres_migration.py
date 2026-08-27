"""Real PostgreSQL validation for migration 20260826_0031.

This module is opt-in because it creates and drops one generated schema in the
explicitly supplied Phase 2 PostgreSQL database.
"""

from __future__ import annotations

import importlib.util
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
