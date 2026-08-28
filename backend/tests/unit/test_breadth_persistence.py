from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.market_breadth import MarketBreadth
from app.services.breadth.persistence import BreadthPersistence
from app.services.breadth.types import (
    BreadthDailyResult,
    BreadthEligibilityCounts,
    BreadthIndicatorValues,
)


def _result(*, advancing: int = 8) -> BreadthDailyResult:
    return BreadthDailyResult(
        market="US",
        calculation_date=date(2026, 8, 21),
        values=BreadthIndicatorValues(
            stocks_up_4pct=3,
            stocks_down_4pct=1,
            advancing_count=advancing,
            declining_count=2,
            t2108_count=7,
            t2108_pct=70.0,
        ),
        eligibility=BreadthEligibilityCounts(
            advance_decline_eligible_count=10,
            stockbee_daily_eligible_count=9,
            t2108_eligible_count=10,
        ),
        broad_universe_count=12,
        eligibility_signature="a" * 64,
        stockbee_eligibility_signature="b" * 64,
    )


def test_persistence_upserts_every_revision_2_field_in_one_market_partition():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[MarketBreadth.__table__])
    db = sessionmaker(bind=engine)()
    persistence = BreadthPersistence(db)

    persistence.upsert_daily(_result(), duration_seconds=1.25)
    persistence.upsert_daily(_result(advancing=9), duration_seconds=0.75)

    rows = db.query(MarketBreadth).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.advancing_count == 9
    assert row.advance_decline_eligible_count == 10
    assert row.broad_universe_count == 12
    assert row.total_stocks_scanned == 12
    assert row.stockbee_eligibility_signature == "b" * 64
    assert row.calculation_revision == 2
    assert row.calculation_duration_seconds == 0.75
