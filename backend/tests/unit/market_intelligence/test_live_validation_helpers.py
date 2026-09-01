from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone

from app.domain.market_intelligence.metrics import (
    calculate_symbol_metrics,
    with_relative_returns,
)
from app.domain.market_intelligence.models import CanonicalBar
from scripts.validate_market_intelligence_live import _manual_metrics


NOW = datetime(2026, 9, 1, 21, tzinfo=timezone.utc)


def _sessions(count: int = 90) -> tuple[date, ...]:
    start = date(2026, 4, 1)
    return tuple(start + timedelta(days=index) for index in range(count))


def _bars(symbol: str, sessions: tuple[date, ...]) -> tuple[CanonicalBar, ...]:
    return tuple(
        CanonicalBar(
            provider="yahoo",
            provider_symbol=symbol,
            symbol=symbol,
            raw_trading_date=session,
            trading_date=session,
            raw_open=100.0 + index,
            raw_high=102.0 + index,
            raw_low=99.0 + index,
            raw_close=101.0 + index,
            provider_adjusted_close=101.0 + index,
            adjustment_factor=1.0,
            adjusted_open=100.0 + index,
            adjusted_high=102.0 + index,
            adjusted_low=99.0 + index,
            adjusted_close=101.0 + index,
            provider_volume=1_000.0 + index,
            source_timestamp=NOW,
            ingestion_timestamp=NOW,
            price_basis="yahoo_adjusted_close_provider_volume",
            normalization_version="market_intelligence_adjusted_ohlcv_v2",
        )
        for index, session in enumerate(sessions)
    )


def test_manual_metrics_match_production_when_exact_session_is_missing() -> None:
    sessions = _sessions()
    spy_bars = _bars("SPY", sessions)
    missing_session = sessions[-12]
    sector_bars = tuple(
        bar for bar in _bars("XLK", sessions) if bar.trading_date != missing_session
    )

    expected = with_relative_returns(
        calculate_symbol_metrics(sector_bars, sessions),
        calculate_symbol_metrics(spy_bars, sessions),
    )

    assert _manual_metrics(sector_bars, spy_bars, sessions) == asdict(expected)


def test_manual_metrics_return_unavailable_values_when_current_session_is_missing() -> None:
    sessions = _sessions()
    spy_bars = _bars("SPY", sessions)
    sector_bars = _bars("XLU", sessions[:-1])

    expected = with_relative_returns(
        calculate_symbol_metrics(sector_bars, sessions),
        calculate_symbol_metrics(spy_bars, sessions),
    )

    assert _manual_metrics(sector_bars, spy_bars, sessions) == asdict(expected)
