"""Additive migration proof for persisted Market Intelligence observability."""

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
    / "20260829_0035_market_intelligence_observability.py"
)
OBSERVABILITY_COLUMNS = {
    "pipeline_version",
    "failure_category",
    "stage_timings_json",
    "publication_status",
    "retry_status",
    "reuse_status",
}


def _load_migration():
    assert MIGRATION_PATH.exists(), "Market Intelligence observability migration is missing"
    spec = importlib.util.spec_from_file_location(MIGRATION_PATH.stem, MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(module, connection, operation: str) -> None:
    module.op = Operations(MigrationContext.configure(connection))
    getattr(module, operation)()


def test_observability_migration_is_additive_nullable_and_reversible() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    audits = sa.Table(
        "market_intelligence_run_audits",
        metadata,
        sa.Column("run_id", sa.Integer, primary_key=True),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
    )
    metadata.create_all(engine)
    migration = _load_migration()

    assert migration.revision == "20260829_0035"
    assert migration.down_revision == "20260828_0034"

    with engine.begin() as connection:
        connection.execute(audits.insert().values(run_id=17, idempotency_key="a" * 64))
        _run(migration, connection, "upgrade")

        columns = {
            column["name"]: column
            for column in sa.inspect(connection).get_columns(audits.name)
        }
        assert OBSERVABILITY_COLUMNS <= set(columns)
        assert all(columns[name]["nullable"] for name in OBSERVABILITY_COLUMNS)

        upgraded = sa.Table(audits.name, sa.MetaData(), autoload_with=connection)
        legacy = connection.execute(
            sa.select(upgraded).where(upgraded.c.run_id == 17)
        ).mappings().one()
        assert {name: legacy[name] for name in OBSERVABILITY_COLUMNS} == {
            name: None for name in OBSERVABILITY_COLUMNS
        }

        _run(migration, connection, "downgrade")
        downgraded = {
            column["name"]
            for column in sa.inspect(connection).get_columns(audits.name)
        }
        assert OBSERVABILITY_COLUMNS.isdisjoint(downgraded)
        assert connection.execute(
            sa.text(
                "SELECT run_id, idempotency_key "
                "FROM market_intelligence_run_audits WHERE run_id = 17"
            )
        ).one() == (17, "a" * 64)
