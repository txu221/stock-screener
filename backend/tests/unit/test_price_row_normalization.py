from __future__ import annotations

from datetime import date
from datetime import datetime, timezone
import json
from pathlib import Path

import pandas as pd
import pytest

from app.services.price_row_normalization import (
    drop_non_finite_close_rows,
    normalize_price_batch,
    normalize_price_frame,
    stock_price_row_from_ohlcv,
)


FIXTURE_PATH = (
    Path(__file__).parents[1] / "fixtures" / "market_intelligence" / "corporate_actions.json"
)
CORPORATE_ACTION_FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
RECONCILED_AT = datetime(2026, 8, 28, 16, 5, tzinfo=timezone.utc)


def _ohlcv_frame(closes: list[float], days: list[date]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes,
            "Low": closes,
            "Close": closes,
            "Volume": [1_000_000] * len(closes),
        },
        index=pd.to_datetime(days),
    )


def test_drop_non_finite_close_rows_removes_unusable_market_prices():
    payload = _ohlcv_frame(
        [101.0, float("nan"), float("inf")],
        [date(2026, 6, 24), date(2026, 6, 25), date(2026, 6, 26)],
    )

    cleaned = drop_non_finite_close_rows(payload)

    assert cleaned is not None
    assert cleaned["Close"].tolist() == [101.0]
    assert cleaned.index.tolist() == [pd.Timestamp(date(2026, 6, 24))]


def test_drop_non_finite_close_rows_treats_missing_close_as_empty_price_frame():
    payload = pd.DataFrame(
        {"Open": [100.0], "Volume": [1_000_000]},
        index=pd.to_datetime([date(2026, 6, 24)]),
    )

    cleaned = drop_non_finite_close_rows(payload)

    assert cleaned is not None
    assert cleaned.empty
    assert list(cleaned.columns) == ["Open", "Volume"]


def test_stock_price_row_from_ohlcv_skips_rows_without_finite_close():
    row = pd.Series({"Open": 100.0, "Close": float("nan"), "Volume": 1_000_000})

    assert stock_price_row_from_ohlcv(symbol="SPY", row_date=date(2026, 6, 24), row=row) is None


def test_stock_price_row_from_ohlcv_skips_rows_without_complete_finite_ohlc():
    row = pd.Series(
        {
            "Open": 100.0,
            "High": float("nan"),
            "Low": 99.0,
            "Close": 101.0,
            "Volume": 1_000_000,
        }
    )

    assert stock_price_row_from_ohlcv(symbol="SPY", row_date=date(2026, 6, 24), row=row) is None


def test_normalize_price_frame_enforces_min_rows_after_filtering():
    payload = _ohlcv_frame([101.0, float("nan")], [date(2026, 6, 24), date(2026, 6, 25)])

    assert normalize_price_frame(payload, min_rows=2) is None

    cleaned = normalize_price_frame(payload, min_rows=1)

    assert cleaned is not None
    assert cleaned["Close"].tolist() == [101.0]


def test_normalize_price_frame_removes_rows_with_incomplete_ohlc_values():
    payload = pd.DataFrame(
        {
            "Open": [100.0, float("nan")],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [101.0, 102.0],
            "Volume": [1_000_000, 1_000_000],
        },
        index=pd.to_datetime([date(2026, 6, 24), date(2026, 6, 25)]),
    )

    cleaned = normalize_price_frame(payload)

    assert cleaned is not None
    assert cleaned["Close"].tolist() == [101.0]
    assert cleaned.index.tolist() == [pd.Timestamp(date(2026, 6, 24))]


def test_normalize_price_batch_filters_symbols_with_insufficient_clean_rows():
    enough_rows = _ohlcv_frame([101.0, 102.0], [date(2026, 6, 24), date(2026, 6, 25)])
    insufficient_after_filter = _ohlcv_frame([103.0, float("nan")], [date(2026, 6, 24), date(2026, 6, 25)])
    no_close = pd.DataFrame(
        {"Open": [100.0, 101.0], "Volume": [1_000_000, 1_000_000]},
        index=pd.to_datetime([date(2026, 6, 24), date(2026, 6, 25)]),
    )

    cleaned = normalize_price_batch(
        {"AAPL": enough_rows, "MSFT": insufficient_after_filter, "BAD": no_close},
        min_rows=2,
    )

    assert list(cleaned) == ["AAPL"]
    assert cleaned["AAPL"]["Close"].tolist() == [101.0, 102.0]


@pytest.mark.parametrize("case", CORPORATE_ACTION_FIXTURE["cases"], ids=lambda case: case["name"])
def test_stock_price_row_from_ohlcv_preserves_corporate_action_evidence(case):
    normalized = stock_price_row_from_ohlcv(
        symbol=case["symbol"],
        row_date=date.fromisoformat(case["date"]),
        row=case["row"],
        provider=CORPORATE_ACTION_FIXTURE["provider"],
        source_timestamp=CORPORATE_ACTION_FIXTURE["source_timestamp"],
        normalization_version=CORPORATE_ACTION_FIXTURE["normalization_version"],
        reconciled_at=RECONCILED_AT,
    )

    assert normalized is not None
    assert normalized["open"] == case["row"]["Open"]
    assert normalized["close"] == case["row"]["Close"]
    assert normalized["adj_close"] == case["row"]["Adj Close"]
    assert normalized["volume"] == case["row"]["Volume"]
    assert normalized["adjustment_factor"] == pytest.approx(case["expected_factor"])
    assert normalized["split_ratio"] == case["expected_split_ratio"]
    assert normalized["dividend_cash"] == case["expected_dividend_cash"]
    assert normalized["normalization_version"] == "canonical_price_adjustment_v2"
    assert normalized["price_basis"] == "yahoo_adjusted_close_provider_volume"
    assert normalized["reconciled_at"] is not None


def test_stock_price_row_from_ohlcv_marks_missing_adjusted_close_unreconciled():
    normalized = stock_price_row_from_ohlcv(
        symbol="NOADJ",
        row_date=date(2026, 6, 24),
        row={"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1_000_000},
        provider="yahoo",
        source_timestamp="2026-08-28T16:00:00+00:00",
        normalization_version="canonical_price_adjustment_v2",
        reconciled_at=RECONCILED_AT,
    )

    assert normalized is not None
    assert normalized["adj_close"] is None
    assert normalized["adjustment_factor"] is None
    assert normalized["price_basis"] == "raw_ohlcv_unreconciled"
    assert normalized["reconciled_at"] is None


def test_stock_price_row_from_ohlcv_requires_a_non_blank_provider_for_reconciliation():
    normalized = stock_price_row_from_ohlcv(
        symbol="NOPROVIDER",
        row_date=date(2026, 6, 24),
        row={"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Adj Close": 99.5, "Volume": 1_000_000},
        provider=" ",
        source_timestamp="2026-08-28T16:00:00+00:00",
        normalization_version="canonical_price_adjustment_v2",
        reconciled_at=RECONCILED_AT,
    )

    assert normalized is not None
    assert normalized["price_basis"] == "raw_ohlcv_unreconciled"
    assert normalized["reconciled_at"] is None


def test_stock_price_row_from_ohlcv_hash_is_deterministic_for_identical_evidence():
    arguments = {
        "symbol": "HASH",
        "row_date": date(2026, 6, 24),
        "row": {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Adj Close": 99.5, "Volume": 1_000_000, "Dividends": 0.5, "Stock Splits": 0.0},
        "provider": "yahoo",
        "source_timestamp": "2026-08-28T16:00:00+00:00",
        "normalization_version": "canonical_price_adjustment_v2",
        "reconciled_at": RECONCILED_AT,
    }

    first = stock_price_row_from_ohlcv(**arguments)
    second = stock_price_row_from_ohlcv(**arguments)

    assert first is not None
    assert second is not None
    assert first["content_hash"] == second["content_hash"]
    assert len(first["content_hash"]) == 64
    assert first == second


def test_stock_price_row_from_ohlcv_hash_changes_when_only_source_timestamp_changes():
    arguments = {
        "symbol": "HASH",
        "row_date": date(2026, 6, 24),
        "row": {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Adj Close": 99.5, "Volume": 1_000_000},
        "provider": "yahoo",
        "normalization_version": "canonical_price_adjustment_v2",
        "reconciled_at": RECONCILED_AT,
    }

    first = stock_price_row_from_ohlcv(
        **arguments,
        source_timestamp="2026-06-24T00:00:00+00:00",
    )
    second = stock_price_row_from_ohlcv(
        **arguments,
        source_timestamp="2026-06-25T00:00:00+00:00",
    )

    assert first is not None
    assert second is not None
    assert first["content_hash"] != second["content_hash"]
