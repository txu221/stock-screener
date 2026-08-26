"""Upgrade/downgrade proof for the additive Phase 1 schema."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
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
    assert MIGRATION_PATH.exists(), "Phase 1 migration is missing"
    spec = importlib.util.spec_from_file_location(MIGRATION_PATH.stem, MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(module, connection, operation: str) -> None:
    module.op = Operations(MigrationContext.configure(connection))
    getattr(module, operation)()


def test_market_intelligence_migration_is_additive_and_reversible() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table(
        "feature_runs",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
    )
    metadata.create_all(engine)
    migration = _load_migration()

    assert migration.revision == "20260826_0031"
    assert migration.down_revision == "20260823_0030"

    with engine.begin() as connection:
        _run(migration, connection, "upgrade")
        inspector = sa.inspect(connection)
        assert set(inspector.get_table_names()) == {"feature_runs", *NEW_TABLES}

        audit_columns = {column["name"] for column in inspector.get_columns(
            "market_intelligence_run_audits"
        )}
        assert {
            "idempotency_key",
            "ingestion_status",
            "metric_version",
            "normalization_version",
            "price_basis",
            "source_freshness_json",
        } <= audit_columns

        snapshot_columns = {column["name"] for column in inspector.get_columns(
            "market_intelligence_sector_snapshots"
        )}
        assert {
            "return_60d",
            "relative_return_vs_spy_60d",
            "rvol20",
            "flow_pressure_1d_proxy",
            "cmf_60d_proxy",
            "current_ranks_json",
            "metric_version",
        } <= snapshot_columns

        for table in NEW_TABLES:
            foreign_keys = inspector.get_foreign_keys(table)
            assert len(foreign_keys) == 1
            assert foreign_keys[0]["referred_table"] == "feature_runs"
            assert foreign_keys[0]["options"].get("ondelete") == "CASCADE"

        _run(migration, connection, "downgrade")
        assert set(sa.inspect(connection).get_table_names()) == {"feature_runs"}
