"""Transparent descending dense ranks for the fixed sector universe."""

from __future__ import annotations

import math
from collections.abc import Mapping

from .constants import METRIC_VERSION, SECTOR_SYMBOLS
from .models import RankDirection, RankRecord, SectorMetrics, SectorSnapshot

RANKING_METRICS = (
    "return_1d",
    "relative_return_vs_spy_5d",
    "relative_return_vs_spy_20d",
    "relative_return_vs_spy_60d",
    "rvol20",
    "cmf_20d_proxy",
)


def dense_rank_sectors(values: Mapping[str, float]) -> dict[str, int]:
    """Rank descending values densely; symbols only stabilize output order."""
    if not set(values).issubset(SECTOR_SYMBOLS):
        raise ValueError("dense ranks accept only Phase 1 sector symbols")
    if not all(math.isfinite(float(value)) for value in values.values()):
        raise ValueError("dense rank values must be finite")
    ordered_values = sorted(set(values.values()), reverse=True)
    rank_by_value = {
        value: index + 1 for index, value in enumerate(ordered_values)
    }
    return {
        symbol: rank_by_value[values[symbol]]
        for symbol in sorted(values)
    }


def rank_record(
    *,
    current_rank: int,
    previous_rank: int | None,
) -> RankRecord:
    if previous_rank is None:
        return RankRecord(
            current_rank=current_rank,
            previous_rank=None,
            rank_change=None,
            rank_direction=RankDirection.NOT_AVAILABLE,
        )
    change = previous_rank - current_rank
    if change > 0:
        direction = RankDirection.IMPROVED
    elif change < 0:
        direction = RankDirection.DECLINED
    else:
        direction = RankDirection.UNCHANGED
    return RankRecord(
        current_rank=current_rank,
        previous_rank=previous_rank,
        rank_change=change,
        rank_direction=direction,
    )


def _previous_rank(
    previous_published: Mapping[str, SectorSnapshot],
    symbol: str,
    metric_name: str,
) -> int | None:
    snapshot = previous_published.get(symbol)
    if snapshot is None or snapshot.metric_version != METRIC_VERSION:
        return None
    record = snapshot.ranks.get(metric_name)
    return record.current_rank if record is not None else None


def build_sector_rank_records(
    metrics_by_symbol: Mapping[str, SectorMetrics],
    previous_published: Mapping[str, SectorSnapshot],
) -> dict[str, dict[str, RankRecord]]:
    """Build all six ranks only for a complete 11-sector metric universe."""
    if set(metrics_by_symbol) != set(SECTOR_SYMBOLS):
        return {}

    records: dict[str, dict[str, RankRecord]] = {
        symbol: {} for symbol in sorted(SECTOR_SYMBOLS)
    }
    for metric_name in RANKING_METRICS:
        values: dict[str, float] = {}
        for symbol in SECTOR_SYMBOLS:
            value = getattr(metrics_by_symbol[symbol], metric_name)
            if value is None or not math.isfinite(float(value)):
                return {}
            values[symbol] = float(value)
        current_ranks = dense_rank_sectors(values)
        for symbol, current_rank in current_ranks.items():
            records[symbol][metric_name] = rank_record(
                current_rank=current_rank,
                previous_rank=_previous_rank(
                    previous_published, symbol, metric_name
                ),
            )
    return records
