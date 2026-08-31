"""Deterministic configuration and value objects for Market Intelligence MVP v1."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
import math
from types import MappingProxyType
from typing import Iterable, Mapping, Protocol, Sequence


MVP_METRIC_VERSION = "market_intelligence_mvp_v1"
ETF_STRENGTH_VERSION = "etf_strength_v1"
MVP_PRICE_BASIS = "cached_adjusted_close"
MVP_PRICE_HISTORY_QUALITY = "partial_corporate_action_adjustment"
CORPORATE_ACTION_ADJUSTED_QUALITY = "corporate_action_adjusted"

_RECONCILED_PRICE_BASIS = "yahoo_adjusted_close_provider_volume"
_RECONCILED_NORMALIZATION_VERSION = "canonical_price_adjustment_v2"

PULSE_SYMBOLS = ("SPY", "QQQ", "DIA", "IWM")

_ETF_CATEGORIES = {
    "broad_market": ("SPY", "QQQ", "IWM", "DIA"),
    "sector": (
        "XLC",
        "XLY",
        "XLP",
        "XLE",
        "XLF",
        "XLV",
        "XLI",
        "XLB",
        "XLRE",
        "XLK",
        "XLU",
    ),
    "semiconductor": ("SMH", "SOXX", "XSD"),
    "software": ("IGV",),
    "biotech": ("XBI", "IBB"),
    "defense": ("ITA", "PPA"),
    "energy": ("XLE", "XOP"),
    "metals": ("GDX", "GDXJ", "COPX"),
    "uranium": ("URA",),
}

ETF_CATEGORIES = MappingProxyType(_ETF_CATEGORIES)
ETF_UNIVERSE = tuple(
    dict.fromkeys(
        symbol
        for symbols in _ETF_CATEGORIES.values()
        for symbol in symbols
    )
)

FLOW_PRESSURE_DISCLOSURE = (
    "OHLCV-derived pressure proxy. "
    "Not measured institutional or exchange net flow."
)


class DailyPriceLike(Protocol):
    date: date
    adj_close: float | None
    volume: int | None


def _has_reconciled_price_provenance(row: object) -> bool:
    content_hash = getattr(row, "content_hash", None)
    revision_number = getattr(row, "revision_number", None)
    return (
        getattr(row, "provider", None) == "yahoo"
        and getattr(row, "source_timestamp", None) is not None
        and getattr(row, "normalization_version", None)
        == _RECONCILED_NORMALIZATION_VERSION
        and getattr(row, "price_basis", None) == _RECONCILED_PRICE_BASIS
        and isinstance(content_hash, str)
        and len(content_hash) == 64
        and all(
            character in "0123456789abcdef"
            for character in content_hash.lower()
        )
        and isinstance(revision_number, int)
        and revision_number >= 0
        and getattr(row, "reconciled_at", None) is not None
        and _finite_positive(getattr(row, "adj_close", None)) is not None
        and _finite_positive(getattr(row, "adjustment_factor", None)) is not None
    )


def classify_price_history_quality(rows: Iterable[object]) -> str:
    observed = tuple(rows)
    if observed and all(_has_reconciled_price_provenance(row) for row in observed):
        return CORPORATE_ACTION_ADJUSTED_QUALITY
    return MVP_PRICE_HISTORY_QUALITY


@dataclass(frozen=True)
class PriceMetrics:
    available: bool
    price: float | None = None
    volume: int | None = None
    return_1d: float | None = None
    return_5d: float | None = None
    return_20d: float | None = None
    return_60d: float | None = None
    rvol20: float | None = None
    drawdown_60d: float | None = None


@dataclass(frozen=True)
class MarketPulseItem:
    symbol: str
    available: bool
    price: float | None = None
    return_1d: float | None = None
    return_5d: float | None = None
    return_20d: float | None = None
    return_60d: float | None = None


@dataclass(frozen=True)
class MarketOverview:
    as_of: date | None
    last_updated: datetime | None
    provider: str
    metric_version: str
    price_basis: str
    price_history_quality: str
    expected_session: date | None
    freshness_status: str
    market_status: str | None
    pulse: tuple[MarketPulseItem, ...]
    missing_symbols: tuple[str, ...]
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class MoverItem:
    symbol: str
    company_name: str | None
    price: float
    change_1d: float
    volume: int | None
    rvol20: float | None
    average_dollar_volume: float
    sector: str | None
    industry: str | None
    market_cap: float | None


@dataclass(frozen=True)
class MoverSectorSummary:
    sector: str
    advancers: int
    decliners: int
    unchanged: int
    total: int


@dataclass(frozen=True)
class MoverSummary:
    as_of: date | None
    published_at: datetime | None
    provider: str
    metric_version: str
    price_basis: str
    price_history_quality: str
    expected_session: date | None
    freshness_status: str
    eligible_count: int
    gainers: tuple[MoverItem, ...]
    losers: tuple[MoverItem, ...]
    unusual_volume: tuple[MoverItem, ...]
    sectors: tuple[MoverSectorSummary, ...]
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class EtfStrengthItem:
    symbol: str
    categories: tuple[str, ...]
    available: bool
    price: float | None = None
    return_1d: float | None = None
    return_5d: float | None = None
    return_20d: float | None = None
    return_60d: float | None = None
    relative_strength_1d: float | None = None
    relative_strength_5d: float | None = None
    relative_strength_20d: float | None = None
    relative_strength_60d: float | None = None
    rvol20: float | None = None
    drawdown_60d: float | None = None
    strength_score: float | None = None
    score_components: Mapping[str, float] | None = None
    overall_rank: int | None = None
    category_ranks: Mapping[str, int] | None = None


@dataclass(frozen=True)
class EtfRadar:
    as_of: date | None
    last_updated: datetime | None
    provider: str
    metric_version: str
    price_basis: str
    price_history_quality: str
    expected_session: date | None
    freshness_status: str
    score_version: str
    category: str
    items: tuple[EtfStrengthItem, ...]
    missing_symbols: tuple[str, ...]
    unavailable_reason: str | None = None


ETF_STRENGTH_WEIGHTS = MappingProxyType(
    {
        "relative_strength_20d": 0.30,
        "relative_strength_60d": 0.25,
        "return_20d": 0.20,
        "volume_confirmation": 0.15,
        "drawdown_60d": 0.10,
    }
)


def _finite_positive(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def calculate_price_metrics(
    rows: Sequence[DailyPriceLike],
    *,
    as_of: date,
) -> PriceMetrics:
    """Calculate completed-session metrics from adjusted closes only."""

    ordered = sorted((row for row in rows if row.date <= as_of), key=lambda row: row.date)
    if not ordered or ordered[-1].date != as_of:
        return PriceMetrics(available=False)

    current = _finite_positive(ordered[-1].adj_close)
    if current is None:
        return PriceMetrics(available=False)

    def session_return(lookback: int) -> float | None:
        if len(ordered) < lookback + 1:
            return None
        prior = _finite_positive(ordered[-(lookback + 1)].adj_close)
        if prior is None:
            return None
        return current / prior - 1.0

    rvol20: float | None = None
    if len(ordered) >= 21:
        prior_volumes = [row.volume for row in ordered[-21:-1]]
        if all(volume is not None and volume >= 0 for volume in prior_volumes):
            average = sum(int(volume) for volume in prior_volumes) / 20.0
            current_volume = ordered[-1].volume
            if current_volume is not None and current_volume >= 0 and average > 0:
                rvol20 = float(current_volume) / average

    drawdown: float | None = None
    if len(ordered) >= 61:
        window = [_finite_positive(row.adj_close) for row in ordered[-61:]]
        if all(value is not None for value in window):
            maximum = max(value for value in window if value is not None)
            drawdown = current / maximum - 1.0

    current_volume = ordered[-1].volume
    return PriceMetrics(
        available=True,
        price=current,
        volume=(
            int(current_volume)
            if current_volume is not None and current_volume >= 0
            else None
        ),
        return_1d=session_return(1),
        return_5d=session_return(5),
        return_20d=session_return(20),
        return_60d=session_return(60),
        rvol20=rvol20,
        drawdown_60d=drawdown,
    )


def categories_for_symbol(symbol: str) -> tuple[str, ...]:
    return tuple(
        category
        for category, symbols in ETF_CATEGORIES.items()
        if symbol in symbols
    )


def _volume_confirmation(item: EtfStrengthItem) -> float | None:
    if item.rvol20 is None or item.return_20d is None:
        return None
    centered = min(max(item.rvol20, 0.0), 3.0) - 1.0
    if item.return_20d > 0:
        return centered
    if item.return_20d < 0:
        return -centered
    return 0.0


def _score_inputs(item: EtfStrengthItem) -> dict[str, float] | None:
    volume_confirmation = _volume_confirmation(item)
    values = {
        "relative_strength_20d": item.relative_strength_20d,
        "relative_strength_60d": item.relative_strength_60d,
        "return_20d": item.return_20d,
        "volume_confirmation": volume_confirmation,
        "drawdown_60d": item.drawdown_60d,
    }
    if any(value is None or not math.isfinite(value) for value in values.values()):
        return None
    return {name: float(value) for name, value in values.items() if value is not None}


def _inclusive_percentile(value: float, population: Iterable[float]) -> float:
    values = tuple(population)
    if not values:
        raise ValueError("percentile population cannot be empty")
    return 100.0 * sum(candidate <= value for candidate in values) / len(values)


def score_and_rank_etfs(
    items: Sequence[EtfStrengthItem],
) -> tuple[EtfStrengthItem, ...]:
    raw_inputs = {item.symbol: _score_inputs(item) for item in items}
    populations = {
        component: tuple(
            values[component]
            for values in raw_inputs.values()
            if values is not None
        )
        for component in ETF_STRENGTH_WEIGHTS
    }

    scored: list[EtfStrengthItem] = []
    for item in items:
        values = raw_inputs[item.symbol]
        if values is None:
            scored.append(item)
            continue
        percentiles = {
            component: _inclusive_percentile(value, populations[component])
            for component, value in values.items()
        }
        score = sum(
            ETF_STRENGTH_WEIGHTS[component] * percentile
            for component, percentile in percentiles.items()
        )
        scored.append(
            replace(
                item,
                strength_score=round(score, 6),
                score_components=MappingProxyType(percentiles),
            )
        )

    ranked_symbols = [
        item.symbol
        for item in sorted(
            (item for item in scored if item.strength_score is not None),
            key=lambda item: (-float(item.strength_score), item.symbol),
        )
    ]
    overall_ranks = {
        symbol: index for index, symbol in enumerate(ranked_symbols, start=1)
    }

    category_ranks: dict[str, dict[str, int]] = {}
    for category, symbols in ETF_CATEGORIES.items():
        members = sorted(
            (
                item
                for item in scored
                if item.symbol in symbols and item.strength_score is not None
            ),
            key=lambda item: (-float(item.strength_score), item.symbol),
        )
        category_ranks[category] = {
            item.symbol: index for index, item in enumerate(members, start=1)
        }

    return tuple(
        replace(
            item,
            overall_rank=overall_ranks.get(item.symbol),
            category_ranks=MappingProxyType(
                {
                    category: ranks[item.symbol]
                    for category, ranks in category_ranks.items()
                    if item.symbol in ranks
                }
            ),
        )
        for item in scored
    )
