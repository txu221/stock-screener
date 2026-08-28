from contextlib import contextmanager
from dataclasses import replace
from datetime import date
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.market_breadth import MarketBreadth
from app.services.breadth.rebuild import BreadthRebuildService
from app.services.breadth.types import (
    BreadthDailyResult,
    BreadthEligibilityCounts,
    BreadthIndicatorValues,
)


def _result(day: date) -> BreadthDailyResult:
    return BreadthDailyResult(
        market="US",
        calculation_date=day,
        values=BreadthIndicatorValues(
            stocks_up_4pct=8,
            stocks_down_4pct=2,
            advancing_count=60,
            declining_count=35,
            unchanged_count=5,
            t2108_count=50,
            t2108_pct=50.0,
        ),
        eligibility=BreadthEligibilityCounts(
            advance_decline_eligible_count=100,
            stockbee_daily_eligible_count=90,
            stockbee_month_eligible_count=80,
            stockbee_34day_eligible_count=75,
            stockbee_quarter_eligible_count=70,
            t2108_eligible_count=100,
            high_low_52week_eligible_count=65,
            atr_extension_eligible_count=85,
        ),
        broad_universe_count=110,
        eligibility_signature="a" * 64,
        stockbee_eligibility_signature="b" * 64,
    )


@contextmanager
def _cutover_engine(tmp_path):
    database_url = os.environ.get("DATABASE_URL")
    use_postgres = (
        os.environ.get("STOCKSCANNER_TEST_ALLOW_POSTGRES") == "1"
        and database_url
        and database_url.startswith("postgresql")
    )
    if not use_postgres:
        engine = create_engine(f"sqlite:///{tmp_path / 'cutover.sqlite'}")
        try:
            yield engine
        finally:
            engine.dispose()
        return

    schema = f"breadth_cutover_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        database_url,
        connect_args={"options": f"-csearch_path={schema}"},
    )
    try:
        yield engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def test_revision_cutover_replaces_legacy_rows_and_preserves_unrelated_tables(tmp_path):
    with _cutover_engine(tmp_path) as engine:
        Base.metadata.create_all(engine, tables=[MarketBreadth.__table__])
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE unrelated (id INTEGER PRIMARY KEY, value TEXT)")
            )
            connection.execute(
                text("INSERT INTO unrelated (id, value) VALUES (1, 'keep')")
            )
        Session = sessionmaker(bind=engine)
        with Session() as db:
            db.add(
                MarketBreadth(
                    market="US",
                    date=date(2026, 8, 20),
                    stocks_up_4pct=99,
                    stocks_down_4pct=1,
                    stocks_up_25pct_quarter=0,
                    stocks_down_25pct_quarter=0,
                    stocks_up_25pct_month=0,
                    stocks_down_25pct_month=0,
                    stocks_up_50pct_month=0,
                    stocks_down_50pct_month=0,
                    stocks_up_13pct_34days=0,
                    stocks_down_13pct_34days=0,
                    total_stocks_scanned=100,
                )
            )
            db.commit()
            service = BreadthRebuildService(db, required_markets=("US",))
            service.recreate_staging()
            service.record_build_manifest(
                {"US": (date(2026, 8, 21),)},
                full_market_set=True,
            )
            service.stage_results((_result(date(2026, 8, 21)),))

            report = service.validate()
            assert report["valid"] is True
            service.activate()

            rows = db.query(MarketBreadth).all()
            assert [(row.date, row.calculation_revision) for row in rows] == [
                (date(2026, 8, 21), 2)
            ]
            unrelated = db.execute(
                text("SELECT value FROM unrelated WHERE id = 1")
            ).scalar()
            assert unrelated == "keep"


def test_validation_allows_overlapping_trailing_range_signals(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'overlap.sqlite'}")
    Base.metadata.create_all(engine, tables=[MarketBreadth.__table__])
    Session = sessionmaker(bind=engine)
    with Session() as db:
        result = _result(date(2026, 8, 21))
        result = replace(
            result,
            values=replace(
                result.values,
                stocks_up_13pct_34days=50,
                stocks_down_13pct_34days=40,
                stocks_up_25pct_quarter=50,
                stocks_down_25pct_quarter=40,
            ),
        )
        service = BreadthRebuildService(db, required_markets=("US",))
        service.recreate_staging()
        service.record_build_manifest(
            {"US": (date(2026, 8, 21),)},
            full_market_set=True,
        )
        service.stage_results((result,))

        report = service.validate()

        assert report["valid"] is True
        assert not any(
            error.startswith("pair_exceeds_eligibility")
            for error in report["errors"]
        )

    engine.dispose()


def test_validation_rejects_dates_missing_from_persisted_build_manifest(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'missing-date.sqlite'}")
    Base.metadata.create_all(engine, tables=[MarketBreadth.__table__])
    Session = sessionmaker(bind=engine)
    with Session() as db:
        service = BreadthRebuildService(db, required_markets=("US",))
        service.recreate_staging()
        service.record_build_manifest(
            {"US": (date(2026, 8, 20), date(2026, 8, 21))},
            full_market_set=True,
        )
        service.stage_results((_result(date(2026, 8, 21)),))

        report = service.validate()

        assert report["valid"] is False
        assert "missing_date:US:2026-08-20" in report["errors"]

    engine.dispose()


def test_selective_rebuild_cannot_replace_other_live_markets(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'partial.sqlite'}")
    Base.metadata.create_all(engine, tables=[MarketBreadth.__table__])
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(
            MarketBreadth(
                market="HK",
                date=date(2026, 8, 20),
                stocks_up_4pct=1,
                stocks_down_4pct=1,
                total_stocks_scanned=2,
            )
        )
        db.commit()
        service = BreadthRebuildService(db, required_markets=("US", "HK"))
        service.recreate_staging()
        service.record_build_manifest(
            {"US": (date(2026, 8, 21),)},
            full_market_set=False,
        )
        service.stage_results((_result(date(2026, 8, 21)),))

        report = service.validate()
        with pytest.raises(RuntimeError, match="invalid breadth rebuild"):
            service.activate()

        assert report["valid"] is False
        assert "partial_market_set" in report["errors"]
        assert db.query(MarketBreadth).filter(MarketBreadth.market == "HK").count() == 1

    engine.dispose()


def test_validation_rejects_forged_full_market_manifest(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'forged-full.sqlite'}")
    Base.metadata.create_all(engine, tables=[MarketBreadth.__table__])
    Session = sessionmaker(bind=engine)
    with Session() as db:
        service = BreadthRebuildService(db, required_markets=("US", "HK"))
        service.recreate_staging()
        service.record_build_manifest(
            {"US": (date(2026, 8, 21),)},
            full_market_set=True,
        )
        service.stage_results((_result(date(2026, 8, 21)),))

        report = service.validate()

        assert report["valid"] is False
        assert "missing_manifest_market:HK" in report["errors"]

    engine.dispose()


def test_validation_rejects_empty_required_market_partition(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'empty-market.sqlite'}")
    Base.metadata.create_all(engine, tables=[MarketBreadth.__table__])
    Session = sessionmaker(bind=engine)
    with Session() as db:
        service = BreadthRebuildService(db, required_markets=("US", "HK"))
        service.recreate_staging()
        service.record_build_manifest(
            {"US": (date(2026, 8, 21),), "HK": ()},
            full_market_set=True,
        )
        service.stage_results((_result(date(2026, 8, 21)),))

        report = service.validate()

        assert report["valid"] is False
        assert "empty_manifest_market:HK" in report["errors"]
        with pytest.raises(RuntimeError, match="invalid breadth rebuild"):
            service.activate()

    engine.dispose()
