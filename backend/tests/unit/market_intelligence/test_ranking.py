from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from app.domain.market_intelligence.constants import (
    METRIC_VERSION,
    PRICE_BASIS,
    SECTOR_NAMES,
    SECTOR_SYMBOLS,
)
from app.domain.market_intelligence.models import (
    RankDirection,
    RankRecord,
    SectorMetrics,
    SectorSnapshot,
)
from app.domain.market_intelligence.ranking import (
    RANKING_METRICS,
    build_sector_rank_records,
    dense_rank_sectors,
    rank_record,
)

NOW = datetime(2026, 5, 15, 21, 10, tzinfo=timezone.utc)


def _metrics(value: float) -> SectorMetrics:
    return SectorMetrics(
        return_1d=value,
        return_5d=value,
        return_20d=value,
        return_60d=value,
        relative_return_vs_spy_1d=value,
        relative_return_vs_spy_5d=value,
        relative_return_vs_spy_20d=value,
        relative_return_vs_spy_60d=value,
        rvol20=value + 1.0,
        flow_pressure_1d_proxy=value,
        cmf_5d_proxy=value,
        cmf_20d_proxy=value,
        cmf_60d_proxy=value,
    )


def _snapshot(symbol: str, metrics: SectorMetrics, ranks=None) -> SectorSnapshot:
    return SectorSnapshot(
        trading_date=date(2026, 5, 14),
        symbol=symbol,
        asset_type="sector_etf",
        sector_name=SECTOR_NAMES[symbol],
        metrics=metrics,
        ranks=ranks or {},
        provider="yahoo",
        source_freshness={"status": "FRESH"},
        price_basis=PRICE_BASIS,
        metric_version=METRIC_VERSION,
        calculation_timestamp=NOW,
        data_quality_status="COMPLETE",
    )


def test_dense_rank_preserves_ties_without_symbol_tiebreak() -> None:
    ranks = dense_rank_sectors({"XLK": 0.2, "XLF": 0.2, "XLU": 0.1})

    assert ranks == {"XLF": 1, "XLK": 1, "XLU": 2}
    assert list(ranks) == ["XLF", "XLK", "XLU"]


def test_dense_rank_rejects_benchmark_or_unknown_symbols() -> None:
    with pytest.raises(ValueError, match="only Phase 1 sector symbols"):
        dense_rank_sectors({"SPY": 1.0, "XLK": 0.5})


@pytest.mark.parametrize(
    ("previous", "current", "change", "direction"),
    [
        (7, 2, 5, RankDirection.IMPROVED),
        (2, 7, -5, RankDirection.DECLINED),
        (3, 3, 0, RankDirection.UNCHANGED),
        (None, 3, None, RankDirection.NOT_AVAILABLE),
    ],
)
def test_rank_change_direction(
    previous: int | None,
    current: int,
    change: int | None,
    direction: RankDirection,
) -> None:
    result = rank_record(current_rank=current, previous_rank=previous)

    assert result == RankRecord(
        current_rank=current,
        previous_rank=previous,
        rank_change=change,
        rank_direction=direction,
    )


def test_phase1_ranking_metric_set_is_exact() -> None:
    assert RANKING_METRICS == (
        "return_1d",
        "relative_return_vs_spy_5d",
        "relative_return_vs_spy_20d",
        "relative_return_vs_spy_60d",
        "rvol20",
        "cmf_20d_proxy",
    )


def test_build_sector_ranks_uses_all_eleven_sectors_and_previous_ranks() -> None:
    metrics_by_symbol = {
        symbol: _metrics(index / 100.0)
        for index, symbol in enumerate(SECTOR_SYMBOLS, start=1)
    }
    previous = {
        symbol: _snapshot(
            symbol,
            metrics,
            ranks={
                name: RankRecord(
                    current_rank=7,
                    previous_rank=None,
                    rank_change=None,
                    rank_direction=RankDirection.NOT_AVAILABLE,
                )
                for name in RANKING_METRICS
            },
        )
        for symbol, metrics in metrics_by_symbol.items()
    }

    result = build_sector_rank_records(metrics_by_symbol, previous)

    assert set(result) == set(SECTOR_SYMBOLS)
    assert set(result["XLK"]) == set(RANKING_METRICS)
    assert result["XLK"]["return_1d"].previous_rank == 7
    assert result["XLK"]["return_1d"].rank_change == (
        7 - result["XLK"]["return_1d"].current_rank
    )


def test_incomplete_sector_universe_produces_no_candidate_ranks() -> None:
    metrics_by_symbol = {
        symbol: _metrics(index / 100.0)
        for index, symbol in enumerate(SECTOR_SYMBOLS[:-1], start=1)
    }

    assert build_sector_rank_records(metrics_by_symbol, {}) == {}


def test_unavailable_source_metric_produces_no_candidate_ranks() -> None:
    metrics_by_symbol = {
        symbol: _metrics(index / 100.0)
        for index, symbol in enumerate(SECTOR_SYMBOLS, start=1)
    }
    metrics_by_symbol["XLK"] = replace(
        metrics_by_symbol["XLK"], cmf_20d_proxy=None
    )

    assert build_sector_rank_records(metrics_by_symbol, {}) == {}


def test_equal_values_receive_equal_rank_in_full_universe() -> None:
    metrics_by_symbol = {symbol: _metrics(0.1) for symbol in SECTOR_SYMBOLS}

    result = build_sector_rank_records(metrics_by_symbol, {})

    assert {
        records["return_1d"].current_rank for records in result.values()
    } == {1}
