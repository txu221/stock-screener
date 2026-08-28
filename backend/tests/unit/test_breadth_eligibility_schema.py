from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from app.models.market_breadth import MarketBreadth
from sqlalchemy import Float, Integer, String


REVISION_2_INTEGER_COLUMNS = {
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


def test_market_breadth_has_nullable_eligibility_signature():
    column = MarketBreadth.__table__.c.eligibility_signature

    assert column.type.length == 64
    assert column.nullable is True


def test_breadth_eligibility_migration_follows_current_head():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/20260808_0027_add_breadth_eligibility_signature.py"
    )
    spec = spec_from_file_location("breadth_eligibility_migration", path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert module.revision == "20260808_0027"
    assert module.down_revision == "20260805_0026"


def test_market_breadth_has_nullable_revision_2_columns():
    columns = MarketBreadth.__table__.c

    for name in REVISION_2_INTEGER_COLUMNS:
        assert isinstance(columns[name].type, Integer)
        assert columns[name].nullable is True
    assert isinstance(columns.t2108_pct.type, Float)
    assert columns.t2108_pct.nullable is True
    assert isinstance(columns.stockbee_eligibility_signature.type, String)
    assert columns.stockbee_eligibility_signature.type.length == 64
    assert columns.stockbee_eligibility_signature.nullable is True


def test_shared_breadth_migration_follows_current_head():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/20260825_0031_add_shared_breadth_metrics.py"
    )
    spec = spec_from_file_location("shared_breadth_migration", path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert module.revision == "20260825_0031"
    assert module.down_revision == "20260823_0030"
