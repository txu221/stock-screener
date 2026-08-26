"""Candidate snapshot assembly and exhaustive ingestion-state semantics."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime

from .constants import (
    BENCHMARK_SYMBOL,
    MARKET_INTELLIGENCE_UNIVERSE,
    METRIC_VERSION,
    PRICE_BASIS,
    SECTOR_NAMES,
    SECTOR_SYMBOLS,
)
from .models import (
    CandidateSnapshot,
    IngestionStatus,
    ProviderSymbolFailure,
    SectorMetrics,
    SectorSnapshot,
)
from .ranking import build_sector_rank_records

MINIMUM_HISTORY_SESSIONS = 90
_LOCAL_METRIC_FIELDS = (
    "return_1d",
    "return_5d",
    "return_20d",
    "return_60d",
    "rvol20",
    "flow_pressure_1d_proxy",
    "cmf_5d_proxy",
    "cmf_20d_proxy",
    "cmf_60d_proxy",
)
_RELATIVE_METRIC_FIELDS = (
    "relative_return_vs_spy_1d",
    "relative_return_vs_spy_5d",
    "relative_return_vs_spy_20d",
    "relative_return_vs_spy_60d",
)


def classify_ingestion_status(
    *,
    request_succeeded: bool,
    usable_symbol_count: int,
    snapshot_complete: bool,
) -> IngestionStatus:
    if not request_succeeded or usable_symbol_count == 0:
        return IngestionStatus.FAILED
    if snapshot_complete:
        return IngestionStatus.SUCCEEDED
    return IngestionStatus.PARTIAL


def _has_fields(metrics: SectorMetrics, names: Sequence[str]) -> bool:
    values = (getattr(metrics, name) for name in names)
    return all(
        value is not None and math.isfinite(float(value)) for value in values
    )


def _is_usable(
    symbol: str,
    metrics: SectorMetrics,
    history_session_counts: Mapping[str, int],
) -> bool:
    return (
        history_session_counts.get(symbol, 0) >= MINIMUM_HISTORY_SESSIONS
        and _has_fields(metrics, _LOCAL_METRIC_FIELDS)
    )


def build_candidate_snapshot(
    *,
    request_succeeded: bool,
    as_of: date,
    metrics_by_symbol: Mapping[str, SectorMetrics],
    history_session_counts: Mapping[str, int],
    received_symbols: Sequence[str],
    rejection_count: int,
    provider_failures: Sequence[ProviderSymbolFailure],
    provider: str,
    source_freshness: Mapping[str, object],
    calculation_timestamp: datetime,
    previous_published: Mapping[str, SectorSnapshot],
) -> CandidateSnapshot:
    received = frozenset(received_symbols)
    missing_symbols = tuple(
        symbol
        for symbol in MARKET_INTELLIGENCE_UNIVERSE
        if symbol not in received
    )
    usable_symbols = tuple(
        symbol
        for symbol in MARKET_INTELLIGENCE_UNIVERSE
        if symbol in metrics_by_symbol
        and _is_usable(
            symbol,
            metrics_by_symbol[symbol],
            history_session_counts,
        )
    )

    rank_metrics = {
        symbol: metrics_by_symbol[symbol]
        for symbol in SECTOR_SYMBOLS
        if symbol in usable_symbols
        and _has_fields(metrics_by_symbol[symbol], _RELATIVE_METRIC_FIELDS)
    }
    ranks_by_symbol = build_sector_rank_records(
        rank_metrics, previous_published
    )
    snapshots: list[SectorSnapshot] = []
    for symbol in usable_symbols:
        metrics = metrics_by_symbol[symbol]
        relative_complete = (
            symbol == BENCHMARK_SYMBOL
            or _has_fields(metrics, _RELATIVE_METRIC_FIELDS)
        )
        snapshots.append(
            SectorSnapshot(
                trading_date=as_of,
                symbol=symbol,
                asset_type=(
                    "benchmark_etf"
                    if symbol == BENCHMARK_SYMBOL
                    else "sector_etf"
                ),
                sector_name=SECTOR_NAMES.get(symbol),
                metrics=metrics,
                ranks=ranks_by_symbol.get(symbol, {}),
                provider=provider,
                source_freshness=dict(source_freshness),
                price_basis=PRICE_BASIS,
                metric_version=METRIC_VERSION,
                calculation_timestamp=calculation_timestamp,
                data_quality_status=(
                    "COMPLETE" if relative_complete else "INCOMPLETE"
                ),
            )
        )

    snapshot_complete = (
        request_succeeded
        and usable_symbols == MARKET_INTELLIGENCE_UNIVERSE
        and not missing_symbols
        and rejection_count == 0
        and not provider_failures
        and len(snapshots) == len(MARKET_INTELLIGENCE_UNIVERSE)
        and all(
            snapshot.data_quality_status == "COMPLETE"
            for snapshot in snapshots
        )
        and set(ranks_by_symbol) == set(SECTOR_SYMBOLS)
    )
    ingestion_status = classify_ingestion_status(
        request_succeeded=request_succeeded,
        usable_symbol_count=len(usable_symbols),
        snapshot_complete=snapshot_complete,
    )
    return CandidateSnapshot(
        ingestion_status=ingestion_status,
        snapshots=tuple(snapshots),
        missing_symbols=missing_symbols,
        usable_symbols=usable_symbols,
        publishable=ingestion_status is IngestionStatus.SUCCEEDED,
    )
