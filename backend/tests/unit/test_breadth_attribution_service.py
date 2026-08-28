"""Unit tests for ``BreadthAttributionService``."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from app.services.breadth_attribution_service import (
    NO_GROUP_LABEL,
    BreadthAttributionService,
)


def _frame(
    closes: list[float],
    start: str = "2026-04-01",
    *,
    adjusted_closes: list[float] | None = None,
    final_volume: float = 200_000,
) -> pd.DataFrame:
    """Return enough complete OHLCV history for StockBee daily eligibility."""
    minimum_rows = 21
    padded_closes = [closes[0]] * (minimum_rows - len(closes)) + closes
    padded_adjusted = (
        [adjusted_closes[0]] * (minimum_rows - len(adjusted_closes)) + adjusted_closes
        if adjusted_closes is not None
        else padded_closes
    )
    idx = pd.bdate_range(start=start, periods=len(padded_closes))
    close = pd.Series(padded_closes, index=idx, dtype=float)
    adjusted = pd.Series(padded_adjusted, index=idx, dtype=float)
    volume = pd.Series(100_000.0, index=idx)
    volume.iloc[-1] = final_volume
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Adj Close": adjusted,
            "Volume": volume,
        },
        index=idx,
    )


def _last_date(frame: pd.DataFrame) -> date:
    return pd.Timestamp(frame.index[-1]).date()


def test_compute_returns_empty_when_no_symbols():
    service = BreadthAttributionService()
    result = service.compute(
        symbols_meta=[],
        price_data={},
        target_dates=[date(2026, 5, 5)],
    )
    assert result == []


def test_compute_attributes_up_mover_to_ibd_group():
    service = BreadthAttributionService()
    # Day 1 = 100, Day 2 = 105 → +5%, exceeds 4% threshold.
    price_data = {"PLTR": _frame([100.0, 105.0])}
    symbols_meta = [
        {
            "symbol": "PLTR",
            "company_name": "Palantir Technologies",
            "ibd_industry_group": "Computer Software-Database",
        }
    ]

    result = service.compute(
        symbols_meta=symbols_meta,
        price_data=price_data,
        target_dates=[_last_date(price_data["PLTR"])],
    )

    assert len(result) == 1
    day = result[0]
    assert day["date"] == _last_date(price_data["PLTR"]).isoformat()
    assert day["stocks_up_4pct"] == 1
    assert day["stocks_down_4pct"] == 0
    assert len(day["groups"]) == 1
    group = day["groups"][0]
    assert group["group"] == "Computer Software-Database"
    assert group["up_count"] == 1
    assert group["down_count"] == 0
    assert group["net"] == 1
    assert group["up_stocks"][0]["symbol"] == "PLTR"
    assert group["up_stocks"][0]["name"] == "Palantir Technologies"
    assert group["up_stocks"][0]["pct_change"] == pytest.approx(5.0)
    assert group["up_stocks"][0]["close"] == pytest.approx(105.0)


def test_compute_attributes_down_mover():
    service = BreadthAttributionService()
    price_data = {"XYZ": _frame([100.0, 94.0])}  # -6%

    result = service.compute(
        symbols_meta=[{"symbol": "XYZ", "ibd_industry_group": "Banks-Money Center"}],
        price_data=price_data,
        target_dates=[_last_date(price_data["XYZ"])],
    )

    day = result[0]
    assert day["stocks_up_4pct"] == 0
    assert day["stocks_down_4pct"] == 1
    group = day["groups"][0]
    assert group["down_count"] == 1
    assert group["net"] == -1
    assert group["down_stocks"][0]["pct_change"] == pytest.approx(-6.0)


def test_compute_buckets_missing_group_into_no_group():
    service = BreadthAttributionService()
    price_data = {
        "AAA": _frame([100.0, 106.0]),  # +6%
        "BBB": _frame([100.0, 108.0]),  # +8%
    }
    symbols_meta = [
        {"symbol": "AAA"},  # No ibd_industry_group key
        {"symbol": "BBB", "ibd_industry_group": "  "},  # Whitespace → No Group
    ]

    result = service.compute(
        symbols_meta=symbols_meta,
        price_data=price_data,
        target_dates=[_last_date(price_data["AAA"])],
    )

    day = result[0]
    assert day["stocks_up_4pct"] == 2
    assert len(day["groups"]) == 1
    no_group = day["groups"][0]
    assert no_group["group"] == NO_GROUP_LABEL
    assert no_group["up_count"] == 2
    assert {entry["symbol"] for entry in no_group["up_stocks"]} == {"AAA", "BBB"}


def test_compute_skips_movers_below_threshold():
    service = BreadthAttributionService()
    # +3.5% — below the 4% threshold, should not appear.
    price_data = {"FLAT": _frame([100.0, 103.5])}
    result = service.compute(
        symbols_meta=[{"symbol": "FLAT", "ibd_industry_group": "Retail"}],
        price_data=price_data,
        target_dates=[_last_date(price_data["FLAT"])],
    )
    assert result[0]["groups"] == []
    assert result[0]["stocks_up_4pct"] == 0


def test_compute_sorts_groups_by_total_activity_then_net():
    service = BreadthAttributionService()
    # Group A: 1 up, 0 down (activity=1, net=1)
    # Group B: 2 up, 1 down (activity=3, net=1)
    # Group C: 0 up, 2 down (activity=2, net=-2)
    price_data = {
        "A1": _frame([100.0, 110.0]),
        "B1": _frame([100.0, 110.0]),
        "B2": _frame([100.0, 110.0]),
        "B3": _frame([100.0, 90.0]),
        "C1": _frame([100.0, 90.0]),
        "C2": _frame([100.0, 90.0]),
    }
    meta = [
        {"symbol": "A1", "ibd_industry_group": "Alpha"},
        {"symbol": "B1", "ibd_industry_group": "Bravo"},
        {"symbol": "B2", "ibd_industry_group": "Bravo"},
        {"symbol": "B3", "ibd_industry_group": "Bravo"},
        {"symbol": "C1", "ibd_industry_group": "Charlie"},
        {"symbol": "C2", "ibd_industry_group": "Charlie"},
    ]
    result = service.compute(
        symbols_meta=meta,
        price_data=price_data,
        target_dates=[_last_date(price_data["A1"])],
    )
    groups = [g["group"] for g in result[0]["groups"]]
    # Bravo first (highest activity=3), then Charlie (activity=2), then Alpha.
    assert groups == ["Bravo", "Charlie", "Alpha"]


def test_compute_returns_history_oldest_to_newest():
    service = BreadthAttributionService()
    # Three-day frame where day 2 has a +5% move and day 3 has -5%.
    history = _frame([100.0, 105.0, 99.0])
    history.loc[history.index[-3:], "Volume"] = [100_000.0, 200_000.0, 300_000.0]
    price_data = {"X": history}
    dates = [pd.Timestamp(value).date() for value in price_data["X"].index[-2:]]
    result = service.compute(
        symbols_meta=[{"symbol": "X", "ibd_industry_group": "G"}],
        price_data=price_data,
        target_dates=dates,
    )
    assert [day["date"] for day in result] == [value.isoformat() for value in dates]
    assert result[0]["stocks_up_4pct"] == 1
    assert result[1]["stocks_down_4pct"] == 1


def test_compute_filters_movers_by_each_dates_universe():
    history = _frame([100.0, 105.0, 110.25])
    dates = [pd.Timestamp(value).date() for value in history.index[-2:]]

    result = BreadthAttributionService().compute(
        symbols_meta=[{"symbol": "NEW", "ibd_industry_group": "Software"}],
        price_data={"NEW": history},
        target_dates=dates,
        symbols_by_date={
            dates[0]: frozenset(),
            dates[1]: frozenset({"NEW"}),
        },
    )

    assert result[0]["stocks_up_4pct"] == 0
    assert result[1]["stocks_up_4pct"] == 1


def test_compute_skips_dates_with_missing_price_data():
    service = BreadthAttributionService()
    price_data = {"X": _frame([100.0, 105.0])}  # Only covers 2026-05-01, 2026-05-02
    present = _last_date(price_data["X"])
    result = service.compute(
        symbols_meta=[{"symbol": "X", "ibd_industry_group": "G"}],
        price_data=price_data,
        target_dates=[present, date(2026, 5, 10)],
    )
    # 2026-05-10 has no price → no group entry for that day.
    by_date = {row["date"]: row for row in result}
    assert by_date[present.isoformat()]["stocks_up_4pct"] == 1
    assert by_date["2026-05-10"]["groups"] == []
    assert by_date["2026-05-10"]["stocks_up_4pct"] == 0


def test_compute_skips_symbols_without_price_data():
    service = BreadthAttributionService()
    result = service.compute(
        symbols_meta=[{"symbol": "MISSING", "ibd_industry_group": "G"}],
        price_data={"MISSING": None},
        target_dates=[date(2026, 5, 2)],
    )
    assert result[0]["groups"] == []


def test_compute_isolates_malformed_symbol_history():
    valid = _frame([100.0, 105.0])
    malformed = _frame([100.0, 106.0]).drop(columns=["Adj Close"])
    target = _last_date(valid)

    result = BreadthAttributionService().compute(
        symbols_meta=[
            {"symbol": "VALID", "ibd_industry_group": "Software"},
            {"symbol": "BAD", "ibd_industry_group": "Banks"},
        ],
        price_data={"VALID": valid, "BAD": malformed},
        target_dates=[target],
    )

    assert result[0]["stocks_up_4pct"] == 1
    assert result[0]["groups"][0]["up_stocks"][0]["symbol"] == "VALID"


def test_compute_uses_adjusted_return_and_stockbee_volume_filters():
    eligible = _frame(
        [50.0, 52.5],
        adjusted_closes=[100.0, 105.0],
        final_volume=200_000,
    )
    flat_volume = _frame([100.0, 105.0], final_volume=100_000)
    target = _last_date(eligible)

    result = BreadthAttributionService().compute(
        symbols_meta=[
            {"symbol": "ELIGIBLE", "ibd_industry_group": "Software"},
            {"symbol": "FLATVOL", "ibd_industry_group": "Software"},
        ],
        price_data={"ELIGIBLE": eligible, "FLATVOL": flat_volume},
        target_dates=[target],
    )

    assert result[0]["stocks_up_4pct"] == 1
    assert result[0]["groups"][0]["up_stocks"][0]["symbol"] == "ELIGIBLE"
    assert result[0]["groups"][0]["up_stocks"][0]["pct_change"] == pytest.approx(5.0)
