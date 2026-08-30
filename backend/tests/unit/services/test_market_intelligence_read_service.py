from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.infra.db.models.feature_store import (
    FeatureRun,
    FeatureRunPointer,
    StockFeatureDaily,
)
from app.models.stock import StockPrice
from app.models.stock_universe import StockUniverse
from app.services.market_intelligence_read_service import (
    MarketIntelligenceReadService,
)


AS_OF = date(2026, 8, 26)
PUBLISHED_AT = datetime(2026, 8, 26, 22, 5, tzinfo=timezone.utc)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            FeatureRun.__table__,
            StockFeatureDaily.__table__,
            FeatureRunPointer.__table__,
            StockUniverse.__table__,
            StockPrice.__table__,
        ],
    )
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _published_run(session) -> FeatureRun:
    old = FeatureRun(
        as_of_date=AS_OF - timedelta(days=1),
        run_type="daily_snapshot",
        status="published",
        published_at=PUBLISHED_AT - timedelta(days=1),
    )
    current = FeatureRun(
        as_of_date=AS_OF,
        run_type="daily_snapshot",
        status="published",
        published_at=PUBLISHED_AT,
    )
    session.add_all([old, current])
    session.flush()
    session.add(
        FeatureRunPointer(key="latest_published_market:US", run_id=current.id)
    )
    session.flush()
    return current


def _universe(
    symbol: str,
    *,
    sp500: bool = True,
    active: bool = True,
    price_market_cap: float = 1_000_000_000,
) -> StockUniverse:
    return StockUniverse(
        symbol=symbol,
        name=f"{symbol} Corp",
        market="US",
        sector="Technology",
        industry="Software",
        market_cap=price_market_cap,
        is_sp500=sp500,
        is_active=active,
        status="active" if active else "inactive_manual",
    )


def _feature(
    run_id: int,
    symbol: str,
    *,
    average_dollar_volume: float = 150_000_000,
) -> StockFeatureDaily:
    return StockFeatureDaily(
        run_id=run_id,
        symbol=symbol,
        as_of_date=AS_OF,
        details_json={"avg_dollar_volume": average_dollar_volume},
    )


def _prices(
    symbol: str,
    closes: list[float],
    *,
    volumes: list[int] | None = None,
) -> list[StockPrice]:
    volumes = volumes or [1_000_000] * len(closes)
    start = AS_OF - timedelta(days=len(closes) - 1)
    return [
        StockPrice(
            symbol=symbol,
            date=start + timedelta(days=index),
            open=close,
            high=close,
            low=close,
            close=close,
            adj_close=close,
            volume=volumes[index],
        )
        for index, close in enumerate(closes)
    ]


def test_movers_use_latest_published_sp500_snapshot_and_backend_metrics(session):
    run = _published_run(session)
    symbols = ["AAPL", "MSFT", "LOW", "ILLIQ", "OUT", "INACTIVE", "NOSNAPSHOT"]
    session.add_all(
        [
            _universe("AAPL"),
            _universe("MSFT"),
            _universe("LOW"),
            _universe("ILLIQ"),
            _universe("OUT", sp500=False),
            _universe("INACTIVE", active=False),
            _universe("NOSNAPSHOT"),
        ]
    )
    session.add_all(
        [
            _feature(run.id, "AAPL"),
            _feature(run.id, "MSFT"),
            _feature(run.id, "LOW"),
            _feature(run.id, "ILLIQ", average_dollar_volume=50_000_000),
            _feature(run.id, "OUT"),
            _feature(run.id, "INACTIVE"),
        ]
    )

    base = [100.0] * 20
    session.add_all(_prices("AAPL", [*base, 110.0], volumes=[1_000_000] * 20 + [3_000_000]))
    session.add_all(_prices("MSFT", [*base, 90.0], volumes=[1_000_000] * 20 + [2_000_000]))
    session.add_all(_prices("LOW", [4.0] * 21))
    session.add_all(_prices("ILLIQ", [*base, 120.0]))
    session.add_all(_prices("OUT", [*base, 130.0]))
    session.add_all(_prices("INACTIVE", [*base, 140.0]))
    session.add_all(_prices("NOSNAPSHOT", [*base, 150.0]))
    session.commit()

    result = MarketIntelligenceReadService(
        session, completed_session=AS_OF
    ).get_movers(limit=20)

    assert result.as_of == AS_OF
    assert result.published_at == PUBLISHED_AT
    assert result.provider == "existing_stock_prices"
    assert result.price_basis == "cached_adjusted_close"
    assert result.price_history_quality == "not_corporate_action_reconciled"
    assert result.expected_session == AS_OF
    assert result.freshness_status == "FRESH"
    assert result.eligible_count == 2
    assert [item.symbol for item in result.gainers] == ["AAPL"]
    assert [item.symbol for item in result.losers] == ["MSFT"]
    assert [item.symbol for item in result.unusual_volume] == ["AAPL", "MSFT"]
    assert result.gainers[0].change_1d == pytest.approx(0.10)
    assert result.gainers[0].rvol20 == pytest.approx(3.0)
    assert result.gainers[0].company_name == "AAPL Corp"
    assert result.gainers[0].market_cap == 1_000_000_000
    assert result.sectors[0].advancers == 1
    assert result.sectors[0].decliners == 1


def test_movers_return_transparent_empty_result_without_published_pointer(session):
    result = MarketIntelligenceReadService(session).get_movers()

    assert result.as_of is None
    assert result.published_at is None
    assert result.eligible_count == 0
    assert result.gainers == ()
    assert result.unavailable_reason == "no_published_us_feature_run"


def _etf_closes(*, start: float, d20: float, d5: float, d1: float, today: float) -> list[float]:
    closes = [start] * 61
    closes[-21] = d20
    closes[-6] = d5
    closes[-2] = d1
    closes[-1] = today
    return closes


def test_overview_and_etf_radar_use_completed_adjusted_sessions(session):
    _published_run(session)
    session.add_all(
        _prices(
            "SPY",
            _etf_closes(start=60, d20=80, d5=90, d1=100, today=105),
            volumes=[1_000_000] * 60 + [1_500_000],
        )
    )
    session.add_all(
        _prices(
            "QQQ",
            _etf_closes(start=50, d20=70, d5=85, d1=100, today=110),
            volumes=[1_000_000] * 60 + [2_000_000],
        )
    )
    session.add_all(_prices("IWM", [100.0] * 10))
    session.commit()

    service = MarketIntelligenceReadService(session, completed_session=AS_OF)
    overview = service.get_overview()
    radar = service.get_etf_radar(category="broad_market")

    assert overview.as_of == AS_OF
    assert overview.last_updated == PUBLISHED_AT
    assert overview.price_basis == "cached_adjusted_close"
    assert overview.price_history_quality == "not_corporate_action_reconciled"
    assert overview.expected_session == AS_OF
    assert overview.freshness_status == "FRESH"
    assert [item.symbol for item in overview.pulse] == ["SPY", "QQQ", "DIA", "IWM"]
    assert overview.pulse[0].return_1d == pytest.approx(0.05)
    assert overview.pulse[2].available is False
    assert overview.market_status is None

    assert [item.symbol for item in radar.items] == ["SPY", "QQQ", "IWM", "DIA"]
    qqq = next(item for item in radar.items if item.symbol == "QQQ")
    assert qqq.return_20d == pytest.approx(110 / 70 - 1)
    assert qqq.relative_strength_20d == pytest.approx((110 / 70) - (105 / 80))
    assert qqq.rvol20 == pytest.approx(2.0)
    assert qqq.drawdown_60d == pytest.approx(0.0)
    assert qqq.available is True

    iwm = next(item for item in radar.items if item.symbol == "IWM")
    assert iwm.return_60d is None
    assert iwm.strength_score is None
    assert "DIA" in radar.missing_symbols
    assert radar.metric_version == "market_intelligence_mvp_v1"
    assert radar.score_version == "etf_strength_v1"
    assert radar.last_updated == PUBLISHED_AT
    assert radar.price_basis == "cached_adjusted_close"
    assert radar.price_history_quality == "not_corporate_action_reconciled"
    assert radar.expected_session == AS_OF
    assert radar.freshness_status == "FRESH"


def test_overview_and_etf_radar_ignore_unfinished_daily_price_row(session):
    _published_run(session)
    session.add_all(
        _prices(
            "SPY",
            _etf_closes(start=60, d20=80, d5=90, d1=100, today=105),
        )
    )
    session.add(
        StockPrice(
            symbol="SPY",
            date=AS_OF + timedelta(days=1),
            open=999,
            high=999,
            low=999,
            close=999,
            adj_close=999,
            volume=9_999_999,
        )
    )
    session.commit()

    service = MarketIntelligenceReadService(session, completed_session=AS_OF)
    overview = service.get_overview()
    radar = service.get_etf_radar(category="broad_market")

    assert overview.as_of == AS_OF
    assert overview.pulse[0].price == pytest.approx(105)
    assert radar.as_of == AS_OF
    assert radar.items[0].price == pytest.approx(105)


def test_overview_and_etf_radar_use_published_boundary_not_same_day_partial_bar(session):
    published_date = AS_OF - timedelta(days=1)
    run = FeatureRun(
        as_of_date=published_date,
        run_type="daily_snapshot",
        status="published",
        published_at=PUBLISHED_AT - timedelta(days=1),
    )
    session.add(run)
    session.flush()
    session.add(FeatureRunPointer(key="latest_published_market:US", run_id=run.id))
    session.add_all(_prices("SPY", [100.0] * 20 + [105.0]))
    session.commit()

    service = MarketIntelligenceReadService(
        session,
        completed_sessions=(published_date, AS_OF),
    )
    overview = service.get_overview()
    radar = service.get_etf_radar(category="broad_market")

    assert overview.as_of == published_date
    assert overview.pulse[0].price == pytest.approx(100.0)
    assert overview.freshness_status == "AGING"
    assert radar.as_of == published_date
    assert radar.items[0].price == pytest.approx(100.0)
    assert radar.freshness_status == "AGING"


def test_legacy_completed_session_marks_two_session_lag_stale(session):
    published_date = AS_OF - timedelta(days=2)
    run = FeatureRun(
        as_of_date=published_date,
        run_type="daily_snapshot",
        status="published",
        published_at=PUBLISHED_AT - timedelta(days=2),
    )
    session.add(run)
    session.flush()
    session.add(FeatureRunPointer(key="latest_published_market:US", run_id=run.id))
    session.commit()

    overview = MarketIntelligenceReadService(
        session,
        completed_session=AS_OF,
    ).get_overview()

    assert overview.as_of == published_date
    assert overview.freshness_status == "STALE"


def test_movers_normalize_non_finite_market_cap_before_serialization(session):
    run = _published_run(session)
    session.add(_universe("NAN", price_market_cap=float("nan")))
    session.add(_feature(run.id, "NAN"))
    session.add_all(_prices("NAN", [100.0] * 20 + [110.0]))
    session.commit()

    result = MarketIntelligenceReadService(
        session, completed_session=AS_OF
    ).get_movers()

    assert result.gainers[0].symbol == "NAN"
    assert result.gainers[0].market_cap is None


def test_rvol_zero_baseline_is_null_not_infinity(session):
    run = _published_run(session)
    session.add(_universe("ZERO"))
    session.add(_feature(run.id, "ZERO"))
    session.add_all(_prices("ZERO", [99.0] * 20 + [100.0], volumes=[0] * 20 + [1_000_000]))
    session.commit()

    result = MarketIntelligenceReadService(session).get_movers()

    assert result.gainers[0].rvol20 is None
