from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.domain.market_intelligence.constants import MARKET_INTELLIGENCE_UNIVERSE
from app.domain.market_intelligence.metrics import (
    calculate_symbol_metrics,
    with_relative_returns,
)
from app.domain.market_intelligence.models import (
    CanonicalBar,
    ProviderBatchResult,
    ProviderSymbolFailure,
    RawBar,
)
from scripts import validate_market_intelligence_live as live_validation
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


def _raw_rows(sessions: tuple[date, ...]) -> tuple[RawBar, ...]:
    return tuple(
        RawBar(
            provider=bar.provider,
            provider_symbol=bar.symbol,
            symbol=bar.symbol,
            raw_trading_date=bar.trading_date,
            trading_date=bar.trading_date,
            open=bar.raw_open,
            high=bar.raw_high,
            low=bar.raw_low,
            close=bar.raw_close,
            adjusted_close=bar.provider_adjusted_close,
            volume=bar.provider_volume,
            source_timestamp=NOW,
            dividend_cash=0.0,
            split_ratio=0.0,
        )
        for symbol in MARKET_INTELLIGENCE_UNIVERSE
        for bar in _bars(symbol, sessions)
    )


def _install_provider_fixture(monkeypatch, sessions, rows, *, failures=()):
    result = ProviderBatchResult(
        provider="yahoo",
        response_timestamp=NOW,
        rows=rows,
        symbol_failures=failures,
        request_failure=None,
    )
    monkeypatch.setenv("RUN_MARKET_INTELLIGENCE_LIVE", "1")
    monkeypatch.setattr(
        live_validation,
        "MarketCalendarService",
        lambda: SimpleNamespace(
            last_completed_trading_day=lambda market: sessions[-1],
            trading_days=lambda market, start, end: list(sessions),
        ),
    )
    monkeypatch.setattr(live_validation, "BulkDataFetcher", lambda: object())
    monkeypatch.setattr(
        live_validation,
        "YahooMarketIntelligenceProvider",
        lambda *args, **kwargs: SimpleNamespace(fetch=lambda symbols, target: result),
    )


@pytest.mark.parametrize(
    ("scenario", "expected_status", "expected_rejections"),
    [
        ("complete", "SUCCEEDED", 0),
        ("missing_target", "PARTIAL", 0),
        ("missing_spy", "PARTIAL", 0),
        ("empty", "FAILED", 0),
        ("rejected_target", "PARTIAL", 1),
        ("old_rejection", "PARTIAL", 1),
        ("symbol_failure", "PARTIAL", 0),
    ],
)
def test_live_validation_reports_incomplete_provider_evidence(
    monkeypatch, scenario, expected_status, expected_rejections
) -> None:
    sessions = _sessions(100)
    rows = _raw_rows(sessions)
    failures = ()
    if scenario == "missing_target":
        rows = tuple(row for row in rows if not (
            row.symbol == "XLK" and row.trading_date == sessions[-1]
        ))
    elif scenario == "missing_spy":
        rows = tuple(row for row in rows if row.symbol != "SPY")
    elif scenario == "empty":
        rows = ()
    elif scenario in {"rejected_target", "old_rejection"}:
        rejected_date = sessions[-1] if scenario == "rejected_target" else sessions[0]
        rows = tuple(
            replace(row, volume=-1) if row.symbol == "XLK"
            and row.trading_date == rejected_date else row
            for row in rows
        )
    elif scenario == "symbol_failure":
        failures = (ProviderSymbolFailure("XLK", "PROVIDER_FAILURE", "not logged"),)
    _install_provider_fixture(monkeypatch, sessions, rows, failures=failures)

    summary = live_validation.run_live_validation()

    assert summary["candidate_status"] == expected_status
    assert summary["rejected_rows"] == expected_rejections
    assert summary["request_failure"] is None
    assert len(summary["historical_replay_using_real_provider_data"]) == 5
    if scenario in {"missing_target", "rejected_target"}:
        assert summary["manual_checks"]["XLK"]["status"] == "UNAVAILABLE"
        assert summary["manual_checks"]["XLK"]["all_metrics_match"] is False
    if scenario == "empty":
        assert summary["snapshot_count"] == 0
        assert all(item["max_input_date"] is None for item in
                   summary["historical_replay_using_real_provider_data"])
    if scenario in {"old_rejection", "symbol_failure"}:
        assert all(item["status"] == "PARTIAL" for item in
                   summary["historical_replay_using_real_provider_data"])


@pytest.mark.parametrize("complete", [True, False])
def test_live_validation_cli_exit_code_reflects_candidate_health(monkeypatch, capsys, complete):
    sessions = _sessions(100)
    _install_provider_fixture(
        monkeypatch, sessions, _raw_rows(sessions) if complete else ()
    )
    monkeypatch.delenv("PHASE2_COMPLETED_SESSION", raising=False)

    assert live_validation.main() == (0 if complete else 2)
    assert '"candidate_status"' in capsys.readouterr().out
