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


def _frame(*, include_adjusted: bool = True, include_actions: bool = True) -> pd.DataFrame:
    data = {
        "Open": [100.0],
        "High": [103.0],
        "Low": [99.0],
        "Close": [102.0],
        "Volume": [1_250_000.0],
    }
    if include_adjusted:
        data["Adj Close"] = [101.49]
    if include_actions:
        data["Dividends"] = [0.0]
        data["Stock Splits"] = [0.0]
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


def test_adapter_reports_separate_fetch_and_normalization_timing_evidence() -> None:
    fetcher = Mock()
    fetcher.fetch_batch_prices.return_value = _all_success()
    ticks = iter((10.0, 10.125, 10.125, 10.375))
    provider = YahooMarketIntelligenceProvider(
        fetcher,
        clock=lambda: NOW,
        monotonic=lambda: next(ticks),
    )

    result = provider.fetch(MARKET_INTELLIGENCE_UNIVERSE, AS_OF)

    assert result.stage_timings == {
        "provider_fetch_ms": 125.0,
        "normalization_ms": 250.0,
    }


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
    assert row.dividend_cash == 0.0
    assert row.split_ratio == 0.0
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


def test_missing_symbol_response_is_request_level_schema_drift() -> None:
    payload = _all_success()
    payload.pop("XLU")
    fetcher = Mock()
    fetcher.fetch_batch_prices.return_value = payload

    result = YahooMarketIntelligenceProvider(
        fetcher, clock=lambda: NOW
    ).fetch(MARKET_INTELLIGENCE_UNIVERSE, AS_OF)

    assert result.request_failure is not None
    assert result.request_failure.code == "PROVIDER_SCHEMA_DRIFT"
    assert result.rows == ()
    assert result.symbol_failures == ()


def test_missing_required_columns_are_request_level_schema_drift() -> None:
    payload = _all_success()
    payload["XLE"] = _success("XLE", frame=_frame(include_adjusted=False))
    fetcher = Mock()
    fetcher.fetch_batch_prices.return_value = payload

    result = YahooMarketIntelligenceProvider(
        fetcher, clock=lambda: NOW
    ).fetch(MARKET_INTELLIGENCE_UNIVERSE, AS_OF)

    assert result.request_failure is not None
    assert result.request_failure.code == "PROVIDER_SCHEMA_DRIFT"
    assert result.rows == ()
    assert result.symbol_failures == ()


@pytest.mark.parametrize(
    "invalid_frame",
    (
        _frame(include_actions=False),
        pd.DataFrame(columns=_frame().columns),
        _frame().assign(Close="not numeric"),
        pd.concat((_frame(), _frame())),
        pd.concat(
            (
                _frame().set_axis(pd.DatetimeIndex([AS_OF]), axis="index"),
                _frame().set_axis(
                    pd.DatetimeIndex([AS_OF - timedelta(days=1)]), axis="index"
                ),
            )
        ),
        _frame().set_axis(pd.Index(["not-a-timestamp"]), axis="index"),
    ),
)
def test_schema_contract_failures_are_batch_level_schema_drift(
    invalid_frame: pd.DataFrame,
) -> None:
    payload = _all_success()
    payload["XLK"] = _success("XLK", frame=invalid_frame)
    fetcher = Mock()
    fetcher.fetch_batch_prices.return_value = payload

    result = YahooMarketIntelligenceProvider(
        fetcher, clock=lambda: NOW
    ).fetch(MARKET_INTELLIGENCE_UNIVERSE, AS_OF)

    assert result.request_failure is not None
    assert result.request_failure.code == "PROVIDER_SCHEMA_DRIFT"
    assert result.rows == ()
    assert result.symbol_failures == ()


def test_unexpected_symbol_coverage_is_request_level_schema_drift() -> None:
    payload = _all_success()
    payload["QQQ"] = _success("QQQ")
    fetcher = Mock()
    fetcher.fetch_batch_prices.return_value = payload

    result = YahooMarketIntelligenceProvider(
        fetcher, clock=lambda: NOW
    ).fetch(MARKET_INTELLIGENCE_UNIVERSE, AS_OF)

    assert result.request_failure is not None
    assert result.request_failure.code == "PROVIDER_SCHEMA_DRIFT"
    assert result.rows == ()
    assert result.symbol_failures == ()


@pytest.mark.parametrize(
    "payload",
    (
        ["not-a-symbol-mapping"],
        {symbol: _success(symbol) for symbol in MARKET_INTELLIGENCE_UNIVERSE[:-1]},
        {
            **_all_success(),
            "XLK": "not-a-symbol-entry-mapping",
        },
    ),
)
def test_malformed_response_mapping_shape_is_request_level_schema_drift(
    payload: object,
) -> None:
    fetcher = Mock()
    fetcher.fetch_batch_prices.return_value = payload

    result = YahooMarketIntelligenceProvider(
        fetcher, clock=lambda: NOW
    ).fetch(MARKET_INTELLIGENCE_UNIVERSE, AS_OF)

    assert result.request_failure is not None
    assert result.request_failure.code == "PROVIDER_SCHEMA_DRIFT"
    assert result.rows == ()
    assert result.symbol_failures == ()


@pytest.mark.parametrize("tz", (None, "UTC", "America/New_York"))
def test_valid_naive_or_timezone_aware_timestamp_indexes_are_accepted(tz: str | None) -> None:
    frame = _frame().set_axis(pd.DatetimeIndex([AS_OF], tz=tz), axis="index")
    fetcher = Mock()
    fetcher.fetch_batch_prices.return_value = {
        symbol: _success(symbol, frame=frame)
        for symbol in MARKET_INTELLIGENCE_UNIVERSE
    }

    result = YahooMarketIntelligenceProvider(
        fetcher, clock=lambda: NOW
    ).fetch(MARKET_INTELLIGENCE_UNIVERSE, AS_OF)

    assert result.request_failure is None
    assert len(result.rows) == len(MARKET_INTELLIGENCE_UNIVERSE)


def test_timezone_normalization_uses_utc_date_but_preserves_raw_index_provenance() -> None:
    raw_index = pd.Timestamp("2026-05-14 23:30:00", tz="America/New_York")
    frame = _frame().set_axis(pd.DatetimeIndex([raw_index]), axis="index")
    fetcher = Mock()
    fetcher.fetch_batch_prices.return_value = {
        symbol: _success(symbol, frame=frame)
        for symbol in MARKET_INTELLIGENCE_UNIVERSE
    }

    result = YahooMarketIntelligenceProvider(
        fetcher, clock=lambda: NOW
    ).fetch(MARKET_INTELLIGENCE_UNIVERSE, AS_OF)

    xlk = next(row for row in result.rows if row.symbol == "XLK")
    assert result.request_failure is None
    assert xlk.raw_trading_date == raw_index
    assert xlk.trading_date == AS_OF


@pytest.mark.parametrize(
    "column",
    (
        "Open",
        "High",
        "Low",
        "Close",
        "Adj Close",
        "Volume",
        "Dividends",
        "Stock Splits",
    ),
)
def test_boolean_required_numeric_column_is_request_level_schema_drift(
    column: str,
) -> None:
    frame = _frame()
    frame[column] = True
    payload = _all_success()
    payload["XLK"] = _success("XLK", frame=frame)
    fetcher = Mock()
    fetcher.fetch_batch_prices.return_value = payload

    result = YahooMarketIntelligenceProvider(
        fetcher, clock=lambda: NOW
    ).fetch(MARKET_INTELLIGENCE_UNIVERSE, AS_OF)

    assert result.request_failure is not None
    assert result.request_failure.code == "PROVIDER_SCHEMA_DRIFT"
    assert result.rows == ()
    assert result.symbol_failures == ()


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
