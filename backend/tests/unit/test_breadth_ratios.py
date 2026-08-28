from datetime import date

import pandas as pd
import pytest
from app.services.breadth.ratios import (
    IncompatibleBreadthSeedError,
    calculate_inclusive_ratios,
)
from app.services.breadth.types import BreadthDailyCount


def test_five_and_ten_day_ratios_include_current_row():
    pairs = [
        (204, 225),
        (158, 124),
        (192, 102),
        (246, 116),
        (148, 87),
        (114, 159),
        (74, 343),
        (395, 177),
        (78, 274),
        (228, 25),
    ]
    dates = pd.bdate_range("2026-08-10", periods=10).date
    counts = [
        BreadthDailyCount(
            date=day,
            stocks_up_4pct=up,
            stocks_down_4pct=down,
        )
        for day, (up, down) in zip(dates, pairs, strict=True)
    ]

    result = calculate_inclusive_ratios(counts)

    assert result[dates[-1]].ratio_5day == pytest.approx(0.91)
    assert result[dates[-1]].ratio_10day == pytest.approx(1.13)


def test_ratios_require_exactly_five_and_ten_rows():
    dates = pd.bdate_range("2026-08-03", periods=9).date
    counts = [BreadthDailyCount(day, 2, 1) for day in dates]

    result = calculate_inclusive_ratios(counts)

    assert result[dates[3]].ratio_5day is None
    assert result[dates[3]].ratio_10day is None
    assert result[dates[4]].ratio_5day == pytest.approx(2.0)
    assert result[dates[4]].ratio_10day is None


def test_ratio_is_null_when_down_count_sum_is_zero():
    dates = pd.bdate_range("2026-08-03", periods=10).date
    counts = [BreadthDailyCount(day, 2, 0) for day in dates]

    result = calculate_inclusive_ratios(counts)

    assert result[dates[-1]].ratio_5day is None
    assert result[dates[-1]].ratio_10day is None


@pytest.mark.parametrize(
    "seed",
    [
        BreadthDailyCount(date(2026, 8, 1), 1, 1, market="HK"),
        BreadthDailyCount(
            date(2026, 8, 1),
            1,
            1,
            market="US",
            calculation_revision=1,
        ),
    ],
)
def test_ratio_seeds_must_match_market_and_revision(seed):
    current = BreadthDailyCount(date(2026, 8, 3), 1, 1, market="US")

    with pytest.raises(IncompatibleBreadthSeedError):
        calculate_inclusive_ratios([current], seed_counts=[seed], market="US")
