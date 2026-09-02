from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from app.domain.market_intelligence.constants import (
    BENCHMARK_SYMBOL,
    MARKET_INTELLIGENCE_UNIVERSE,
    METRIC_VERSION,
    PRICE_BASIS,
    SECTOR_SYMBOLS,
)
from app.domain.market_intelligence.models import (
    CandidateSnapshot,
    IngestionStatus,
    ProviderSymbolFailure,
    RankDirection,
    RankRecord,
    SectorMetrics,
)
from app.domain.market_intelligence.ranking import RANKING_METRICS
from app.domain.market_intelligence.snapshot import (
    build_candidate_snapshot,
    classify_ingestion_status,
)

NOW = datetime(2026, 5, 15, 21, 10, tzinfo=timezone.utc)
AS_OF = date(2026, 5, 15)


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


def _complete_metrics() -> dict[str, SectorMetrics]:
    result = {
        symbol: _metrics(index / 100.0)
        for index, symbol in enumerate(SECTOR_SYMBOLS, start=1)
    }
    result[BENCHMARK_SYMBOL] = replace(
        _metrics(0.005),
        relative_return_vs_spy_1d=None,
        relative_return_vs_spy_5d=None,
        relative_return_vs_spy_20d=None,
        relative_return_vs_spy_60d=None,
    )
    return result


def _build(
    *,
    request_succeeded: bool = True,
    metrics_by_symbol: dict[str, SectorMetrics] | None = None,
    history_counts: dict[str, int] | None = None,
    received_symbols: tuple[str, ...] = MARKET_INTELLIGENCE_UNIVERSE,
    rejection_count: int = 0,
    provider_failures: tuple[ProviderSymbolFailure, ...] = (),
    previous_published=None,
) -> CandidateSnapshot:
    metrics = _complete_metrics() if metrics_by_symbol is None else metrics_by_symbol
    counts = (
        {symbol: 90 for symbol in metrics}
        if history_counts is None
        else history_counts
    )
    return build_candidate_snapshot(
        request_succeeded=request_succeeded,
        as_of=AS_OF,
        metrics_by_symbol=metrics,
        history_session_counts=counts,
        received_symbols=received_symbols,
        rejection_count=rejection_count,
        provider_failures=provider_failures,
        provider="yahoo",
        source_freshness={"status": "FRESH", "as_of": AS_OF.isoformat()},
        calculation_timestamp=NOW,
        previous_published=previous_published or {},
    )


@pytest.mark.parametrize(
    ("request_ok", "usable", "complete", "status"),
    [
        (True, 12, True, IngestionStatus.SUCCEEDED),
        (True, 11, False, IngestionStatus.PARTIAL),
        (True, 1, False, IngestionStatus.PARTIAL),
        (True, 0, False, IngestionStatus.FAILED),
        (False, 0, False, IngestionStatus.FAILED),
    ],
)
def test_status_partition_is_mutually_exclusive_and_exhaustive(
    request_ok: bool,
    usable: int,
    complete: bool,
    status: IngestionStatus,
) -> None:
    assert classify_ingestion_status(
        request_succeeded=request_ok,
        usable_symbol_count=usable,
        snapshot_complete=complete,
    ) is status


def test_complete_twelve_symbol_candidate_is_succeeded_and_ranked() -> None:
    result = _build()

    assert result.ingestion_status is IngestionStatus.SUCCEEDED
    assert result.publishable is True
    assert result.missing_symbols == ()
    assert result.usable_symbols == MARKET_INTELLIGENCE_UNIVERSE
    assert len(result.snapshots) == 12
    by_symbol = {snapshot.symbol: snapshot for snapshot in result.snapshots}
    assert by_symbol["SPY"].asset_type == "benchmark_etf"
    assert by_symbol["SPY"].sector_name is None
    assert by_symbol["SPY"].ranks == {}
    assert set(by_symbol["XLK"].ranks) == set(RANKING_METRICS)
    assert by_symbol["XLK"].metric_version == METRIC_VERSION
    assert by_symbol["XLK"].price_basis == PRICE_BASIS


def test_eleven_of_twelve_is_partial_and_not_publishable() -> None:
    metrics = _complete_metrics()
    metrics.pop("XLU")
    result = _build(
        metrics_by_symbol=metrics,
        received_symbols=tuple(symbol for symbol in MARKET_INTELLIGENCE_UNIVERSE if symbol != "XLU"),
    )

    assert result.ingestion_status is IngestionStatus.PARTIAL
    assert result.publishable is False
    assert result.missing_symbols == ("XLU",)


def test_one_usable_symbol_is_partial() -> None:
    result = _build(
        metrics_by_symbol={"SPY": _complete_metrics()["SPY"]},
        received_symbols=("SPY",),
    )

    assert result.ingestion_status is IngestionStatus.PARTIAL
    assert result.usable_symbols == ("SPY",)
    assert len(result.snapshots) == 1


def test_zero_usable_symbols_is_failed() -> None:
    result = _build(metrics_by_symbol={}, history_counts={}, received_symbols=())

    assert result.ingestion_status is IngestionStatus.FAILED
    assert result.snapshots == ()
    assert result.publishable is False


def test_request_failure_is_failed_without_candidate_rows() -> None:
    result = _build(
        request_succeeded=False,
        metrics_by_symbol={},
        history_counts={},
        received_symbols=(),
    )

    assert result.ingestion_status is IngestionStatus.FAILED
    assert result.snapshots == ()


@pytest.mark.parametrize("cause", ["history", "rejection", "provider_failure"])
def test_any_completeness_failure_makes_candidate_partial(cause: str) -> None:
    kwargs = {}
    if cause == "history":
        kwargs["history_counts"] = {
            symbol: (89 if symbol == "XLK" else 90)
            for symbol in MARKET_INTELLIGENCE_UNIVERSE
        }
    elif cause == "rejection":
        kwargs["rejection_count"] = 1
    else:
        kwargs["provider_failures"] = (
            ProviderSymbolFailure("XLK", "NO_DATA", "missing"),
        )

    result = _build(**kwargs)

    assert result.ingestion_status is IngestionStatus.PARTIAL
    assert result.publishable is False


def test_missing_required_metric_makes_symbol_unusable_and_run_partial() -> None:
    metrics = _complete_metrics()
    metrics["XLK"] = replace(metrics["XLK"], rvol20=None)

    result = _build(metrics_by_symbol=metrics)

    assert result.ingestion_status is IngestionStatus.PARTIAL
    assert "XLK" not in result.usable_symbols
    assert {snapshot.symbol for snapshot in result.snapshots} == set(
        MARKET_INTELLIGENCE_UNIVERSE
    ) - {"XLK"}


def test_non_finite_required_metric_cannot_enter_publishable_snapshot() -> None:
    metrics = _complete_metrics()
    metrics["XLK"] = replace(metrics["XLK"], cmf_60d_proxy=float("inf"))

    result = _build(metrics_by_symbol=metrics)

    assert result.ingestion_status is IngestionStatus.PARTIAL
    assert result.publishable is False
    assert "XLK" not in result.usable_symbols


def test_rank_change_uses_supplied_published_predecessor() -> None:
    monday = _build()
    monday_by_symbol = {snapshot.symbol: snapshot for snapshot in monday.snapshots}
    monday_xlk = monday_by_symbol["XLK"]
    monday_by_symbol["XLK"] = replace(
        monday_xlk,
        ranks={
            name: RankRecord(7, None, None, RankDirection.NOT_AVAILABLE)
            for name in RANKING_METRICS
        },
    )

    wednesday = _build(previous_published=monday_by_symbol)
    xlk = next(row for row in wednesday.snapshots if row.symbol == "XLK")

    assert xlk.ranks["return_1d"].previous_rank == 7
    assert xlk.ranks["return_1d"].rank_change == (
        7 - xlk.ranks["return_1d"].current_rank
    )
