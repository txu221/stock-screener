from __future__ import annotations

from dataclasses import fields, replace
from datetime import date, datetime, timedelta, timezone

import pytest

from app.domain.market_intelligence.metrics import (
    calculate_symbol_metrics,
    with_relative_returns,
)
from app.domain.market_intelligence.models import CanonicalBar, RawBar, SectorMetrics
from app.domain.market_intelligence.validation import validate_provider_rows

NOW = datetime(2026, 5, 15, 21, 10, tzinfo=timezone.utc)


def _sessions(count: int) -> tuple[date, ...]:
    start = date(2026, 1, 5)
    return tuple(start + timedelta(days=index) for index in range(count))


def _bar(
    session: date,
    *,
    close: float,
    volume: float = 100.0,
    high: float | None = None,
    low: float | None = None,
    open_: float | None = None,
) -> CanonicalBar:
    resolved_high = close + 1.0 if high is None else high
    resolved_low = close - 1.0 if low is None else low
    resolved_open = close if open_ is None else open_
    return CanonicalBar(
        provider="fixture_yahoo",
        provider_symbol="XLK",
        symbol="XLK",
        raw_trading_date=session.isoformat(),
        trading_date=session,
        raw_open=resolved_open,
        raw_high=resolved_high,
        raw_low=resolved_low,
        raw_close=close,
        provider_adjusted_close=close,
        adjustment_factor=1.0,
        adjusted_open=resolved_open,
        adjusted_high=resolved_high,
        adjusted_low=resolved_low,
        adjusted_close=close,
        provider_volume=volume,
        source_timestamp=NOW,
        ingestion_timestamp=NOW,
        price_basis="yahoo_adjusted_ohlc_provider_volume",
        normalization_version="market_intelligence_adjusted_ohlcv_v1",
    )


def _series(
    sessions: tuple[date, ...],
    *,
    closes: list[float] | None = None,
    volumes: list[float] | None = None,
) -> tuple[CanonicalBar, ...]:
    resolved_closes = closes or [100.0 + index for index in range(len(sessions))]
    resolved_volumes = volumes or [100.0] * len(sessions)
    return tuple(
        _bar(session, close=close, volume=volume)
        for session, close, volume in zip(
            sessions, resolved_closes, resolved_volumes, strict=True
        )
    )


def _metric_values(metrics: SectorMetrics) -> tuple[float | None, ...]:
    return tuple(getattr(metrics, field.name) for field in fields(metrics))


def test_golden_returns_use_exact_completed_session_offsets(
    golden_raw_bars,
    golden_sessions,
) -> None:
    validation = validate_provider_rows(golden_raw_bars, golden_sessions, NOW)
    xlk_bars = tuple(
        bar for bar in validation.canonical_bars if bar.symbol == "XLK"
    )
    close_by_date = {bar.trading_date: bar.adjusted_close for bar in xlk_bars}

    metrics = calculate_symbol_metrics(xlk_bars, golden_sessions)

    current = close_by_date[golden_sessions[-1]]
    assert metrics.return_1d == pytest.approx(
        current / close_by_date[golden_sessions[-2]] - 1.0
    )
    assert metrics.return_5d == pytest.approx(
        current / close_by_date[golden_sessions[-6]] - 1.0
    )
    assert metrics.return_20d == pytest.approx(
        current / close_by_date[golden_sessions[-21]] - 1.0
    )
    assert metrics.return_60d == pytest.approx(
        current / close_by_date[golden_sessions[-61]] - 1.0
    )


def test_return_uses_session_position_across_calendar_gap() -> None:
    sessions = (date(2026, 5, 8), date(2026, 5, 11))
    bars = (
        _bar(sessions[0], close=100.0),
        _bar(sessions[1], close=110.0),
    )

    metrics = calculate_symbol_metrics(bars, sessions)

    assert metrics.return_1d == pytest.approx(0.10)


def test_missing_anchor_is_unavailable_not_zero() -> None:
    sessions = _sessions(61)
    bars = tuple(
        bar for bar in _series(sessions) if bar.trading_date != sessions[-6]
    )

    metrics = calculate_symbol_metrics(bars, sessions)

    assert metrics.return_1d is not None
    assert metrics.return_5d is None
    assert metrics.return_5d != 0.0


def test_insufficient_history_marks_each_unavailable_lookback() -> None:
    sessions = _sessions(5)

    metrics = calculate_symbol_metrics(_series(sessions), sessions)

    assert metrics.return_1d is not None
    assert metrics.return_5d is None
    assert metrics.return_20d is None
    assert metrics.return_60d is None
    assert metrics.rvol20 is None
    assert metrics.cmf_5d_proxy is not None
    assert metrics.cmf_20d_proxy is None
    assert metrics.cmf_60d_proxy is None


def test_duplicate_canonical_date_makes_metrics_unavailable() -> None:
    sessions = _sessions(61)
    bars = (*_series(sessions), _bar(sessions[-1], close=200.0))

    metrics = calculate_symbol_metrics(bars, sessions)

    assert set(_metric_values(metrics)) == {None}


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("adjusted_close", float("nan")),
        ("adjusted_high", float("inf")),
        ("provider_volume", float("nan")),
    ],
)
def test_non_finite_canonical_input_cannot_escape_into_metrics(
    field_name: str,
    value: float,
) -> None:
    sessions = _sessions(61)
    bars = list(_series(sessions))
    bars[-1] = replace(bars[-1], **{field_name: value})

    metrics = calculate_symbol_metrics(tuple(bars), sessions)

    assert set(_metric_values(metrics)) == {None}


def test_rvol20_excludes_current_session_from_denominator() -> None:
    sessions = _sessions(21)
    volumes = [100.0] * 20 + [1_000.0]

    metrics = calculate_symbol_metrics(
        _series(sessions, volumes=volumes), sessions
    )

    assert metrics.rvol20 == pytest.approx(10.0)


def test_rvol20_zero_historical_average_is_unavailable() -> None:
    sessions = _sessions(21)
    volumes = [0.0] * 20 + [1_000.0]

    metrics = calculate_symbol_metrics(
        _series(sessions, volumes=volumes), sessions
    )

    assert metrics.rvol20 is None


def test_flow_pressure_uses_adjusted_ohlc_close_location() -> None:
    sessions = (date(2026, 5, 15),)
    bar = _bar(sessions[0], close=105.0, high=110.0, low=90.0)

    metrics = calculate_symbol_metrics((bar,), sessions)

    assert metrics.flow_pressure_1d_proxy == pytest.approx(0.5)


def test_zero_range_flow_pressure_is_explicit_zero() -> None:
    sessions = (date(2026, 5, 15),)
    bar = _bar(
        sessions[0],
        close=100.0,
        high=100.0,
        low=100.0,
        open_=100.0,
    )

    metrics = calculate_symbol_metrics((bar,), sessions)

    assert metrics.flow_pressure_1d_proxy == 0.0


def test_cmf_windows_include_current_session() -> None:
    sessions = _sessions(60)
    bars = tuple(
        _bar(session, close=105.0, high=110.0, low=90.0, volume=100.0)
        for session in sessions
    )

    metrics = calculate_symbol_metrics(bars, sessions)

    assert metrics.cmf_5d_proxy == pytest.approx(0.5)
    assert metrics.cmf_20d_proxy == pytest.approx(0.5)
    assert metrics.cmf_60d_proxy == pytest.approx(0.5)


def test_cmf_zero_volume_denominator_is_unavailable() -> None:
    sessions = _sessions(60)
    bars = tuple(
        _bar(session, close=105.0, high=110.0, low=90.0, volume=0.0)
        for session in sessions
    )

    metrics = calculate_symbol_metrics(bars, sessions)

    assert metrics.cmf_5d_proxy is None
    assert metrics.cmf_20d_proxy is None
    assert metrics.cmf_60d_proxy is None


def test_relative_returns_are_sector_minus_spy() -> None:
    sector = SectorMetrics(
        return_1d=0.02,
        return_5d=0.05,
        return_20d=0.10,
        return_60d=0.20,
        relative_return_vs_spy_1d=None,
        relative_return_vs_spy_5d=None,
        relative_return_vs_spy_20d=None,
        relative_return_vs_spy_60d=None,
        rvol20=1.0,
        flow_pressure_1d_proxy=0.0,
        cmf_5d_proxy=0.0,
        cmf_20d_proxy=0.0,
        cmf_60d_proxy=0.0,
    )
    spy = replace(
        sector,
        return_1d=0.01,
        return_5d=0.02,
        return_20d=0.03,
        return_60d=0.04,
    )

    result = with_relative_returns(sector, spy)

    assert result.relative_return_vs_spy_1d == pytest.approx(0.01)
    assert result.relative_return_vs_spy_5d == pytest.approx(0.03)
    assert result.relative_return_vs_spy_20d == pytest.approx(0.07)
    assert result.relative_return_vs_spy_60d == pytest.approx(0.16)
    assert sector.relative_return_vs_spy_20d is None


def test_missing_spy_return_keeps_relative_metric_unavailable() -> None:
    sessions = _sessions(61)
    sector = calculate_symbol_metrics(_series(sessions), sessions)
    spy = replace(sector, return_20d=None)

    result = with_relative_returns(sector, spy)

    assert result.relative_return_vs_spy_20d is None


@pytest.mark.parametrize(
    ("pre_split_close", "post_split_close", "pre_adjusted_close", "split_ratio"),
    (
        (100.0, 50.0, 50.0, 2.0),
        (90.0, 30.0, 30.0, 3.0),
        (10.0, 100.0, 100.0, 0.1),
    ),
)
def test_adjusted_return_has_no_artificial_split_jump(
    pre_split_close: float,
    post_split_close: float,
    pre_adjusted_close: float,
    split_ratio: float,
) -> None:
    sessions = (date(2026, 5, 14), date(2026, 5, 15))
    raw_bars = (
        RawBar(
            provider="yahoo",
            provider_symbol="XLK",
            symbol="XLK",
            raw_trading_date=sessions[0].isoformat(),
            trading_date=sessions[0],
            open=pre_split_close,
            high=pre_split_close,
            low=pre_split_close,
            close=pre_split_close,
            adjusted_close=pre_adjusted_close,
            volume=100.0,
            source_timestamp=NOW,
            dividend_cash=0.0,
            split_ratio=0.0,
        ),
        RawBar(
            provider="yahoo",
            provider_symbol="XLK",
            symbol="XLK",
            raw_trading_date=sessions[1].isoformat(),
            trading_date=sessions[1],
            open=post_split_close,
            high=post_split_close,
            low=post_split_close,
            close=post_split_close,
            adjusted_close=post_split_close,
            volume=100.0,
            source_timestamp=NOW,
            dividend_cash=0.0,
            split_ratio=split_ratio,
        ),
    )

    validation = validate_provider_rows(raw_bars, sessions, NOW)
    metrics = calculate_symbol_metrics(validation.canonical_bars, sessions)

    assert validation.rejections == ()
    assert metrics.return_1d == pytest.approx(0.0)


def test_adjusted_return_includes_cash_dividend_without_price_drop_artifact() -> None:
    sessions = (date(2026, 5, 14), date(2026, 5, 15))
    raw_bars = (
        RawBar(
            provider="yahoo",
            provider_symbol="XLK",
            symbol="XLK",
            raw_trading_date=sessions[0].isoformat(),
            trading_date=sessions[0],
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            adjusted_close=99.0,
            volume=100.0,
            source_timestamp=NOW,
            dividend_cash=0.0,
            split_ratio=0.0,
        ),
        RawBar(
            provider="yahoo",
            provider_symbol="XLK",
            symbol="XLK",
            raw_trading_date=sessions[1].isoformat(),
            trading_date=sessions[1],
            open=99.0,
            high=99.0,
            low=99.0,
            close=99.0,
            adjusted_close=99.0,
            volume=100.0,
            source_timestamp=NOW,
            dividend_cash=1.0,
            split_ratio=0.0,
        ),
    )

    validation = validate_provider_rows(raw_bars, sessions, NOW)
    metrics = calculate_symbol_metrics(validation.canonical_bars, sessions)

    assert validation.rejections == ()
    assert metrics.return_1d == pytest.approx(0.0)
