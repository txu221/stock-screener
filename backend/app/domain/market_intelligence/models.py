"""Immutable value objects for the Phase 1 sector-intelligence pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Mapping


class IngestionStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class RankDirection(str, Enum):
    IMPROVED = "IMPROVED"
    DECLINED = "DECLINED"
    UNCHANGED = "UNCHANGED"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class RejectionCode(str, Enum):
    UNEXPECTED_SYMBOL = "UNEXPECTED_SYMBOL"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    INVALID_TRADING_DATE = "INVALID_TRADING_DATE"
    NON_FINITE_VALUE = "NON_FINITE_VALUE"
    NON_POSITIVE_PRICE = "NON_POSITIVE_PRICE"
    INVALID_ADJUSTED_CLOSE = "INVALID_ADJUSTED_CLOSE"
    INVALID_ADJUSTMENT_FACTOR = "INVALID_ADJUSTMENT_FACTOR"
    INVALID_OHLC_RELATION = "INVALID_OHLC_RELATION"
    NEGATIVE_VOLUME = "NEGATIVE_VOLUME"
    DUPLICATE_BAR = "DUPLICATE_BAR"


@dataclass(frozen=True)
class RawBar:
    provider: str
    provider_symbol: str
    symbol: str
    raw_trading_date: Any
    trading_date: date | None
    open: Any
    high: Any
    low: Any
    close: Any
    adjusted_close: Any
    volume: Any
    source_timestamp: datetime | None
    dividend_cash: Any = None
    split_ratio: Any = None


@dataclass(frozen=True)
class CanonicalBar:
    provider: str
    provider_symbol: str
    symbol: str
    raw_trading_date: Any
    trading_date: date
    raw_open: float
    raw_high: float
    raw_low: float
    raw_close: float
    provider_adjusted_close: float
    adjustment_factor: float
    adjusted_open: float
    adjusted_high: float
    adjusted_low: float
    adjusted_close: float
    provider_volume: float
    source_timestamp: datetime | None
    ingestion_timestamp: datetime
    price_basis: str
    normalization_version: str
    dividend_cash: float | None = None
    split_ratio: float | None = None


@dataclass(frozen=True)
class BarRejection:
    provider: str
    provider_symbol: str
    symbol: str | None
    trading_date: date | None
    code: RejectionCode
    reason: str
    raw_evidence: Mapping[str, Any]
    ingestion_timestamp: datetime


@dataclass(frozen=True)
class RankRecord:
    current_rank: int
    previous_rank: int | None
    rank_change: int | None
    rank_direction: RankDirection


@dataclass(frozen=True)
class RequestFailure:
    code: str
    message: str


@dataclass(frozen=True)
class ProviderSymbolFailure:
    symbol: str
    code: str
    message: str


@dataclass(frozen=True)
class ProviderBatchResult:
    provider: str
    response_timestamp: datetime
    rows: tuple[RawBar, ...]
    symbol_failures: tuple[ProviderSymbolFailure, ...]
    request_failure: RequestFailure | None


@dataclass(frozen=True)
class ValidationResult:
    canonical_bars: tuple[CanonicalBar, ...]
    rejections: tuple[BarRejection, ...]
    received_symbols: tuple[str, ...]


@dataclass(frozen=True)
class SectorMetrics:
    return_1d: float | None
    return_5d: float | None
    return_20d: float | None
    return_60d: float | None
    relative_return_vs_spy_1d: float | None
    relative_return_vs_spy_5d: float | None
    relative_return_vs_spy_20d: float | None
    relative_return_vs_spy_60d: float | None
    rvol20: float | None
    flow_pressure_1d_proxy: float | None
    cmf_5d_proxy: float | None
    cmf_20d_proxy: float | None
    cmf_60d_proxy: float | None


@dataclass(frozen=True)
class SectorSnapshot:
    trading_date: date
    symbol: str
    asset_type: str
    sector_name: str | None
    metrics: SectorMetrics
    ranks: Mapping[str, RankRecord]
    provider: str
    source_freshness: Mapping[str, Any]
    price_basis: str
    metric_version: str
    calculation_timestamp: datetime
    data_quality_status: str


@dataclass(frozen=True)
class RunAudit:
    idempotency_key: str
    input_hash: str
    ingestion_status: IngestionStatus
    provider: str
    provider_status: str
    request_failure: RequestFailure | None
    metric_version: str
    normalization_version: str
    price_basis: str
    counters: Mapping[str, int]
    missing_symbols: tuple[str, ...]
    provider_failures: tuple[ProviderSymbolFailure, ...]
    target_session: date
    provider_response_at: datetime | None
    source_freshness: Mapping[str, Any]
    calculation_timestamp: datetime
    ingestion_timestamp: datetime


@dataclass(frozen=True)
class CandidateSnapshot:
    ingestion_status: IngestionStatus
    snapshots: tuple[SectorSnapshot, ...]
    missing_symbols: tuple[str, ...]
    usable_symbols: tuple[str, ...]
    publishable: bool


@dataclass(frozen=True)
class MarketIntelligenceRunBundle:
    """One persisted attempt plus its immutable Phase 1 evidence."""

    run_id: int
    as_of_date: date
    lifecycle_status: str
    created_at: datetime
    published_at: datetime | None
    audit: RunAudit
    canonical_bars: tuple[CanonicalBar, ...]
    rejections: tuple[BarRejection, ...]
    snapshots: tuple[SectorSnapshot, ...]
