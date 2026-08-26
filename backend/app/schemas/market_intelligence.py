"""Stable API schemas for Phase 1 sector intelligence and Data Health."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Self

from pydantic import BaseModel

from app.domain.market_intelligence.constants import (
    BENCHMARK_SYMBOL,
    MARKET_INTELLIGENCE_UNIVERSE,
    METRIC_SEMANTICS,
    SECTOR_SYMBOLS,
)
from app.domain.market_intelligence.models import (
    MarketIntelligenceRunBundle,
    SectorSnapshot,
)

_UNIVERSE_ORDER = {
    symbol: index for index, symbol in enumerate(MARKET_INTELLIGENCE_UNIVERSE)
}


class FlowPressureProxyResponse(BaseModel):
    metric_type: str = "derived_proxy"
    flow_pressure_1d: float | None
    cmf_5d: float | None
    cmf_20d: float | None
    cmf_60d: float | None


class SectorIntelligenceItemResponse(BaseModel):
    symbol: str
    name: str | None
    asset_type: str
    returns: dict[str, float | None]
    relative_strength: dict[str, float | None]
    rvol20: float | None
    flow_pressure_proxy: FlowPressureProxyResponse
    ranks: dict[str, int]
    previous_ranks: dict[str, int | None]
    rank_changes: dict[str, int | None]
    rank_directions: dict[str, str]
    freshness: dict[str, Any]
    data_quality_status: str

    @classmethod
    def from_snapshot(cls, snapshot: SectorSnapshot) -> Self:
        metrics = snapshot.metrics
        return cls(
            symbol=snapshot.symbol,
            name=snapshot.sector_name,
            asset_type=snapshot.asset_type,
            returns={
                "1d": metrics.return_1d,
                "5d": metrics.return_5d,
                "20d": metrics.return_20d,
                "60d": metrics.return_60d,
            },
            relative_strength={
                "1d_vs_spy": metrics.relative_return_vs_spy_1d,
                "5d_vs_spy": metrics.relative_return_vs_spy_5d,
                "20d_vs_spy": metrics.relative_return_vs_spy_20d,
                "60d_vs_spy": metrics.relative_return_vs_spy_60d,
            },
            rvol20=metrics.rvol20,
            flow_pressure_proxy=FlowPressureProxyResponse(
                flow_pressure_1d=metrics.flow_pressure_1d_proxy,
                cmf_5d=metrics.cmf_5d_proxy,
                cmf_20d=metrics.cmf_20d_proxy,
                cmf_60d=metrics.cmf_60d_proxy,
            ),
            ranks={name: rank.current_rank for name, rank in snapshot.ranks.items()},
            previous_ranks={
                name: rank.previous_rank for name, rank in snapshot.ranks.items()
            },
            rank_changes={
                name: rank.rank_change for name, rank in snapshot.ranks.items()
            },
            rank_directions={
                name: rank.rank_direction.value
                for name, rank in snapshot.ranks.items()
            },
            freshness=dict(snapshot.source_freshness),
            data_quality_status=snapshot.data_quality_status,
        )


class SectorIntelligenceLatestResponse(BaseModel):
    run_id: int
    as_of: date
    published_at: datetime
    provider: str
    provider_status: str
    metric_version: str
    normalization_version: str
    price_basis: str
    metric_semantics: str
    status: str
    run_status: str
    calculation_timestamp: datetime
    benchmark: SectorIntelligenceItemResponse
    sectors: list[SectorIntelligenceItemResponse]

    @classmethod
    def from_bundle(cls, bundle: MarketIntelligenceRunBundle) -> Self:
        by_symbol = {snapshot.symbol: snapshot for snapshot in bundle.snapshots}
        if set(by_symbol) != set(MARKET_INTELLIGENCE_UNIVERSE):
            raise ValueError("published sector bundle does not contain the fixed universe")
        benchmark = by_symbol.get(BENCHMARK_SYMBOL)
        if benchmark is None or bundle.published_at is None:
            raise ValueError("published sector bundle is missing benchmark metadata")
        return cls(
            run_id=bundle.run_id,
            as_of=bundle.as_of_date,
            published_at=bundle.published_at,
            provider=bundle.audit.provider,
            provider_status=bundle.audit.provider_status,
            metric_version=bundle.audit.metric_version,
            normalization_version=bundle.audit.normalization_version,
            price_basis=bundle.audit.price_basis,
            metric_semantics=METRIC_SEMANTICS,
            status=bundle.audit.ingestion_status.value,
            run_status=bundle.audit.ingestion_status.value,
            calculation_timestamp=bundle.audit.calculation_timestamp,
            benchmark=SectorIntelligenceItemResponse.from_snapshot(benchmark),
            sectors=[
                SectorIntelligenceItemResponse.from_snapshot(by_symbol[symbol])
                for symbol in SECTOR_SYMBOLS
                if symbol in by_symbol
            ],
        )


class SectorIntelligenceHistoryItemResponse(BaseModel):
    run_id: int
    as_of: date
    published_at: datetime
    provider: str
    status: str
    metric_version: str
    snapshots: list[SectorIntelligenceItemResponse]

    @classmethod
    def from_bundle(cls, bundle: MarketIntelligenceRunBundle) -> Self:
        if bundle.published_at is None:
            raise ValueError("history accepts only published bundles")
        ordered = sorted(
            bundle.snapshots,
            key=lambda snapshot: _UNIVERSE_ORDER[snapshot.symbol],
        )
        return cls(
            run_id=bundle.run_id,
            as_of=bundle.as_of_date,
            published_at=bundle.published_at,
            provider=bundle.audit.provider,
            status=bundle.audit.ingestion_status.value,
            metric_version=bundle.audit.metric_version,
            snapshots=[
                SectorIntelligenceItemResponse.from_snapshot(snapshot)
                for snapshot in ordered
            ],
        )


class SectorIntelligenceHistoryResponse(BaseModel):
    metric_version: str
    symbol: str | None
    items: list[SectorIntelligenceHistoryItemResponse]


class RequestFailureResponse(BaseModel):
    code: str
    message: str


class ProviderSymbolFailureResponse(BaseModel):
    symbol: str
    code: str
    message: str


class MarketIntelligenceHealthRunResponse(BaseModel):
    run_id: int
    as_of: date
    status: str
    lifecycle_status: str
    provider: str
    provider_status: str
    metric_version: str
    normalization_version: str
    price_basis: str
    counters: dict[str, int]
    missing_symbols: list[str]
    request_failure: RequestFailureResponse | None
    provider_failures: list[ProviderSymbolFailureResponse]
    source_freshness: dict[str, Any]
    calculation_timestamp: datetime
    ingestion_timestamp: datetime
    published_at: datetime | None

    @classmethod
    def from_bundle(cls, bundle: MarketIntelligenceRunBundle) -> Self:
        audit = bundle.audit
        return cls(
            run_id=bundle.run_id,
            as_of=bundle.as_of_date,
            status=audit.ingestion_status.value,
            lifecycle_status=bundle.lifecycle_status,
            provider=audit.provider,
            provider_status=audit.provider_status,
            metric_version=audit.metric_version,
            normalization_version=audit.normalization_version,
            price_basis=audit.price_basis,
            counters=dict(audit.counters),
            missing_symbols=list(audit.missing_symbols),
            request_failure=(
                RequestFailureResponse(
                    code=audit.request_failure.code,
                    message=audit.request_failure.message,
                )
                if audit.request_failure is not None
                else None
            ),
            provider_failures=[
                ProviderSymbolFailureResponse(
                    symbol=failure.symbol,
                    code=failure.code,
                    message=failure.message,
                )
                for failure in audit.provider_failures
            ],
            source_freshness=dict(audit.source_freshness),
            calculation_timestamp=audit.calculation_timestamp,
            ingestion_timestamp=audit.ingestion_timestamp,
            published_at=bundle.published_at,
        )


class MarketIntelligenceHealthResponse(BaseModel):
    universe_expected: int
    current_run_timestamp: datetime | None
    latest_attempt: MarketIntelligenceHealthRunResponse | None
    latest_published: MarketIntelligenceHealthRunResponse | None
    last_successful_run: MarketIntelligenceHealthRunResponse | None
    last_complete_published_snapshot: date | None
    publication_occurred: bool
