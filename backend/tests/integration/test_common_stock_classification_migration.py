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
    / "20260825_0032_add_common_stock_classification.py"
)


def _run_revision(engine, fn_name: str) -> None:
    spec = importlib.util.spec_from_file_location(
        "common_stock_classification_migration",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        original_op = module.op
        module.op = operations
        try:
            getattr(module, fn_name)()
        finally:
            module.op = original_op


def test_common_stock_classification_migrates_manual_rows_fail_closed(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'classification.sqlite'}")
    metadata = sa.MetaData()
    universe = sa.Table(
        "stock_universe",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("source", sa.String(20)),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            universe.insert(),
            [
                {"symbol": "AAPL", "source": "finviz"},
                {"symbol": "SPY", "source": "manual"},
            ],
        )

    _run_revision(engine, "upgrade")

    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT symbol, is_common_stock FROM stock_universe "
                "ORDER BY symbol"
            )
        ).all()
    assert rows == [("AAPL", 1), ("SPY", 0)]
    column = {
        item["name"]: item for item in sa.inspect(engine).get_columns("stock_universe")
    }["is_common_stock"]
    assert column["nullable"] is False

    _run_revision(engine, "downgrade")

    remaining = {
        item["name"] for item in sa.inspect(engine).get_columns("stock_universe")
    }
    assert "is_common_stock" not in remaining
    engine.dispose()
