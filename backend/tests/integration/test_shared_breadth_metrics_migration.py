"""Upgrade/downgrade proof for revision-2 breadth metric columns."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

BACKEND_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    BACKEND_ROOT
    / "alembic"
    / "versions"
    / "20260825_0031_add_shared_breadth_metrics.py"
)

NEW_INTEGER_COLUMNS = {
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
}
NEW_COLUMNS = NEW_INTEGER_COLUMNS | {
    "t2108_pct",
    "stockbee_eligibility_signature",
}


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "shared_breadth_migration", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_revision(engine, fn_name: str) -> None:
    module = _load_migration()
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        original_op = module.op
        module.op = operations
        try:
            getattr(module, fn_name)()
        finally:
            module.op = original_op


def _create_legacy_schema(engine) -> None:
    metadata = sa.MetaData()
    sa.Table(
        "market_breadth",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("market", sa.String(8), nullable=False),
        sa.Column("stocks_up_4pct", sa.Integer, nullable=False),
        sa.Column("stocks_down_4pct", sa.Integer, nullable=False),
        sa.UniqueConstraint("date", "market", name="uix_breadth_date_market"),
    )
    metadata.create_all(engine)


def test_shared_breadth_metrics_upgrade_and_downgrade(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'breadth-migration.sqlite'}")
    _create_legacy_schema(engine)

    _run_revision(engine, "upgrade")
    inspector = sa.inspect(engine)
    columns = {item["name"]: item for item in inspector.get_columns("market_breadth")}
    assert NEW_COLUMNS.issubset(columns)
    assert all(columns[name]["nullable"] for name in NEW_COLUMNS)
    assert all(
        isinstance(columns[name]["type"], sa.Integer) for name in NEW_INTEGER_COLUMNS
    )
    assert isinstance(columns["t2108_pct"]["type"], sa.Float)
    assert isinstance(columns["stockbee_eligibility_signature"]["type"], sa.String)
    assert columns["stockbee_eligibility_signature"]["type"].length == 64
    assert any(
        constraint["name"] == "uix_breadth_date_market"
        for constraint in inspector.get_unique_constraints("market_breadth")
    )

    _run_revision(engine, "downgrade")
    inspector = sa.inspect(engine)
    remaining = {item["name"] for item in inspector.get_columns("market_breadth")}
    assert NEW_COLUMNS.isdisjoint(remaining)
    assert {"id", "date", "market", "stocks_up_4pct", "stocks_down_4pct"}.issubset(
        remaining
    )
    assert any(
        constraint["name"] == "uix_breadth_date_market"
        for constraint in inspector.get_unique_constraints("market_breadth")
    )
    engine.dispose()
