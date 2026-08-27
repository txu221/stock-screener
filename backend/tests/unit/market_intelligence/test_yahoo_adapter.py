"""Yahoo bulk adapter and completed-session source contracts."""

from __future__ import annotations

import builtins
from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock

import pandas as pd
import pytest

from app.domain.market_intelligence.constants import MARKET_INTELLIGENCE_UNIVERSE
from app.infra.providers.market_intelligence_yahoo import (
    YahooMarketIntelligenceProvider,
)
from app.services.market_intelligence_session_source import (
    CompletedSessionSource,
    SessionWindowUnavailable,
)

AS_OF = date(2026, 5, 15)
NOW = datetime(2026, 5, 15, 21, 6, tzinfo=timezone.utc)


def _frame(*, include_adjusted: bool = True) -> pd.DataFrame:
    data = {
        "Open": [100.0],
        "High": [103.0],
        "Low": [99.0],
        "Close": [102.0],
        "Volume": [1_250_000.0],
    }
    if include_adjusted:
        data["Adj Close"] = [101.49]
    return pd.DataFrame(data, index=[pd.Timestamp(AS_OF, tz="UTC")])


def _success(symbol: str, *, frame: pd.DataFrame | None = None) -> dict:
    return {
        "symbol": symbol,
        "price_data": _frame() if frame is None else frame,
        "has_error": False,
        "error": None,
        "as_of": NOW,
    }


def _all_success() -> dict[str, dict]:
    return {symbol: _success(symbol) for symbol in MARKET_INTELLIGENCE_UNIVERSE}


def test_adapter_requests_exact_fixed_universe_once() -> None:
    fetcher = Mock()
    fetcher.fetch_batch_prices.return_value = _all_success()
    provider = YahooMarketIntelligenceProvider(fetcher, clock=lambda: NOW)

    result = provider.fetch(MARKET_INTELLIGENCE_UNIVERSE, AS_OF)

    fetcher.fetch_batch_prices.assert_called_once_with(
        list(MARKET_INTELLIGENCE_UNIVERSE), period="6mo"
    )
    assert result.provider == "yahoo"
    assert result.response_timestamp == NOW
    assert result.request_failure is None
    assert len(result.rows) == 12


def test_dataframe_fields_are_mapped_raw_without_adjustment_or_repair() -> None:
    fetcher = Mock()
    fetcher.fetch_batch_prices.return_value = _all_success()
    result = YahooMarketIntelligenceProvider(
        fetcher, clock=lambda: NOW
    ).fetch(MARKET_INTELLIGENCE_UNIVERSE, AS_OF)

    row = next(item for item in result.rows if item.symbol == "XLK")
    assert row.provider == "yahoo"
    assert row.provider_symbol == "XLK"
    assert row.raw_trading_date == pd.Timestamp(AS_OF, tz="UTC")
    assert row.trading_date == AS_OF
    assert (row.open, row.high, row.low, row.close) == (100.0, 103.0, 99.0, 102.0)
    assert row.adjusted_close == 101.49
    assert row.volume == 1_250_000.0
    assert row.source_timestamp == NOW


def test_rows_after_requested_as_of_are_not_emitted() -> None:
    frame = pd.concat(
        [
            _frame(),
            pd.DataFrame(
                {
                    "Open": [200.0],
                    "High": [203.0],
                    "Low": [199.0],
                    "Close": [202.0],
                    "Volume": [2_000_000.0],
                    "Adj Close": [201.0],
                },
                index=[pd.Timestamp(AS_OF + timedelta(days=3), tz="UTC")],
            ),
        ]
    )
    fetcher = Mock()
    fetcher.fetch_batch_prices.return_value = {
        symbol: _success(symbol, frame=frame)
        for symbol in MARKET_INTELLIGENCE_UNIVERSE
    }

    result = YahooMarketIntelligenceProvider(
        fetcher, clock=lambda: NOW
    ).fetch(MARKET_INTELLIGENCE_UNIVERSE, AS_OF)

    assert {row.trading_date for row in result.rows} == {AS_OF}


def test_total_batch_timeout_is_one_request_failure_not_twelve_symbol_failures() -> None:
    fetcher = Mock()
    fetcher.fetch_batch_prices.side_effect = TimeoutError("timeout")

    result = YahooMarketIntelligenceProvider(
        fetcher, clock=lambda: NOW
    ).fetch(MARKET_INTELLIGENCE_UNIVERSE, AS_OF)

    assert result.request_failure is not None
    assert result.request_failure.code == "PROVIDER_TIMEOUT"
    assert result.rows == ()
    assert result.symbol_failures == ()


def test_bulk_fetcher_wrapped_batch_failure_is_restored_to_request_failure() -> None:
    fetcher = Mock()
    fetcher.fetch_batch_prices.return_value = {
        symbol: {
            "symbol": symbol,
            "price_data": None,
            "has_error": True,
            "error": "Batch download error: connection timed out",
            "error_kind": "transient",
        }
        for symbol in MARKET_INTELLIGENCE_UNIVERSE
    }

    result = YahooMarketIntelligenceProvider(
        fetcher, clock=lambda: NOW
    ).fetch(MARKET_INTELLIGENCE_UNIVERSE, AS_OF)

    assert result.request_failure is not None
    assert result.request_failure.code == "PROVIDER_TIMEOUT"
    assert result.rows == ()
    assert result.symbol_failures == ()


def test_missing_symbol_response_and_provider_error_are_symbol_failures() -> None:
    payload = _all_success()
    payload.pop("XLU")
    payload["XLK"] = {
        "symbol": "XLK",
        "price_data": None,
        "has_error": True,
        "error": "rate limited",
        "error_kind": "rate_limited",
    }
    fetcher = Mock()
    fetcher.fetch_batch_prices.return_value = payload

    result = YahooMarketIntelligenceProvider(
        fetcher, clock=lambda: NOW
    ).fetch(MARKET_INTELLIGENCE_UNIVERSE, AS_OF)

    failures = {failure.symbol: failure for failure in result.symbol_failures}
    assert result.request_failure is None
    assert failures["XLU"].code == "MISSING_SYMBOL_RESPONSE"
    assert failures["XLK"].code == "RATE_LIMITED"
    assert {row.symbol for row in result.rows}.isdisjoint({"XLU", "XLK"})


def test_malformed_frame_is_symbol_failure_and_does_not_emit_rows() -> None:
    payload = _all_success()
    payload["XLE"] = _success("XLE", frame=_frame(include_adjusted=False))
    fetcher = Mock()
    fetcher.fetch_batch_prices.return_value = payload

    result = YahooMarketIntelligenceProvider(
        fetcher, clock=lambda: NOW
    ).fetch(MARKET_INTELLIGENCE_UNIVERSE, AS_OF)

    failure = next(item for item in result.symbol_failures if item.symbol == "XLE")
    assert failure.code == "MALFORMED_FRAME"
    assert not any(row.symbol == "XLE" for row in result.rows)


def test_adapter_never_imports_shared_price_row_normalization(monkeypatch) -> None:
    fetcher = Mock()
    fetcher.fetch_batch_prices.return_value = _all_success()
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if "price_row_normalization" in name:
            raise AssertionError("shared permissive normalizer must not be imported")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    result = YahooMarketIntelligenceProvider(
        fetcher, clock=lambda: NOW
    ).fetch(MARKET_INTELLIGENCE_UNIVERSE, AS_OF)

    assert len(result.rows) == 12


def test_completed_session_source_retains_full_provider_validation_window() -> None:
    calendar = Mock()
    available = tuple(AS_OF - timedelta(days=value) for value in range(119, -1, -1))
    calendar.trading_days.return_value = list(available)
    source = CompletedSessionSource(calendar)

    result = source.completed_sessions("US", AS_OF, minimum=90)

    assert result == available
    start = AS_OF - timedelta(days=210)
    calendar.trading_days.assert_called_once_with("US", start, AS_OF)


@pytest.mark.parametrize("failure", ["too_short", "as_of_missing"])
def test_completed_session_source_rejects_unusable_window(failure: str) -> None:
    calendar = Mock()
    if failure == "too_short":
        calendar.trading_days.return_value = [AS_OF]
    else:
        calendar.trading_days.return_value = [AS_OF - timedelta(days=1)] * 90
    source = CompletedSessionSource(calendar)

    with pytest.raises(SessionWindowUnavailable) as raised:
        source.completed_sessions("US", AS_OF, minimum=90)

    assert raised.value.market == "US"
    assert raised.value.as_of == AS_OF
    assert raised.value.required_count == 90
