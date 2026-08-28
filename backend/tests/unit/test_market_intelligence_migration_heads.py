"""Regression coverage for the fork-main/Market Intelligence Alembic merge."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


BACKEND_ROOT = Path(__file__).resolve().parents[2]
MERGE_REVISION = "20260828_0033"
EXPECTED_PARENTS = {"20260825_0032", "20260826_0031"}


def test_market_intelligence_and_breadth_migrations_have_one_merge_head() -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == [MERGE_REVISION]

    revision = scripts.get_revision(MERGE_REVISION)
    assert revision is not None
    assert set(revision._normalized_down_revisions) == EXPECTED_PARENTS
