"""Inclusive rolling ratios over canonical StockBee daily counts."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date

from .types import BreadthDailyCount, BreadthRatios


class IncompatibleBreadthSeedError(ValueError):
    """Raised when seed rows cannot safely join a canonical ratio series."""


def _ratio(window: Sequence[BreadthDailyCount]) -> float | None:
    down = sum(item.stocks_down_4pct for item in window)
    if down == 0:
        return None
    up = sum(item.stocks_up_4pct for item in window)
    return round(up / down, 2)


def _validate_count(
    count: BreadthDailyCount,
    *,
    market: str | None,
    expected_revision: int,
    is_seed: bool,
) -> None:
    if count.calculation_revision != expected_revision:
        label = "Seed" if is_seed else "Count"
        raise IncompatibleBreadthSeedError(
            f"{label} row for {count.date.isoformat()} has calculation revision "
            f"{count.calculation_revision}; expected {expected_revision}"
        )
    if market is not None and count.market != market:
        label = "Seed" if is_seed else "Count"
        raise IncompatibleBreadthSeedError(
            f"{label} row for {count.date.isoformat()} belongs to "
            f"{count.market!r}; expected {market!r}"
        )


def calculate_inclusive_ratios(
    counts: Iterable[BreadthDailyCount],
    seed_counts: Iterable[BreadthDailyCount] = (),
    *,
    market: str | None = None,
    calculation_revision: int = 2,
) -> dict[date, BreadthRatios]:
    current = tuple(sorted(counts, key=lambda item: item.date))
    seeds = tuple(sorted(seed_counts, key=lambda item: item.date))
    for item in seeds:
        _validate_count(
            item,
            market=market,
            expected_revision=calculation_revision,
            is_seed=True,
        )
    for item in current:
        _validate_count(
            item,
            market=market,
            expected_revision=calculation_revision,
            is_seed=False,
        )

    timeline = tuple(sorted((*seeds, *current), key=lambda item: item.date))
    dates = [item.date for item in timeline]
    if len(dates) != len(set(dates)):
        raise IncompatibleBreadthSeedError(
            "Breadth ratio input contains duplicate dates"
        )

    result: dict[date, BreadthRatios] = {}
    position_by_date = {item.date: position for position, item in enumerate(timeline)}
    for item in current:
        position = position_by_date[item.date]
        five = timeline[position - 4 : position + 1] if position >= 4 else ()
        ten = timeline[position - 9 : position + 1] if position >= 9 else ()
        result[item.date] = BreadthRatios(
            ratio_5day=_ratio(five) if len(five) == 5 else None,
            ratio_10day=_ratio(ten) if len(ten) == 10 else None,
        )
    return result
