"""Date-specific, cache-only eligibility for static breadth calculations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType

from sqlalchemy.orm import Session

from ..domain.providers.price_symbol_support import split_supported_price_symbols
from .bounded_history_universe import CurrentActiveFallbackUniverseResolver
from .breadth.universe import (
    BREADTH_ELIGIBILITY_SIGNATURE_VERSION,
    breadth_eligibility_signature,
)

STATIC_BREADTH_ELIGIBILITY_VERSION = BREADTH_ELIGIBILITY_SIGNATURE_VERSION
DEFAULT_EXCLUSION_SAMPLE_LIMIT = 20


@dataclass(frozen=True, slots=True)
class StaticBreadthDateEligibility:
    calculation_date: date
    candidate_count: int
    eligible_symbols: tuple[str, ...]
    universe_policy: str
    eligibility_signature: str


@dataclass(frozen=True, slots=True)
class StaticBreadthEligibility:
    eligible_symbols_by_date: Mapping[date, tuple[str, ...]]
    candidate_counts_by_date: Mapping[date, int]
    eligible_counts_by_date: Mapping[date, int]
    universe_policy_by_date: Mapping[date, str]
    eligibility_signatures_by_date: Mapping[date, str]
    unsupported_count: int
    insufficient_history_count: int
    exact_date_gap_count: int
    unsupported_symbols: tuple[str, ...]
    insufficient_history_symbols: tuple[str, ...]
    exact_date_gap_symbols: tuple[str, ...]
    by_date: Mapping[date, StaticBreadthDateEligibility] = field(init=False)

    def __post_init__(self) -> None:
        expected_dates = set(self.eligible_symbols_by_date)
        parallel_dates = {
            "candidate counts": set(self.candidate_counts_by_date),
            "eligible counts": set(self.eligible_counts_by_date),
            "universe policies": set(self.universe_policy_by_date),
            "eligibility signatures": set(self.eligibility_signatures_by_date),
        }
        for label, actual_dates in parallel_dates.items():
            if actual_dates != expected_dates:
                raise ValueError(f"static breadth {label} dates do not match")

        records: dict[date, StaticBreadthDateEligibility] = {}
        for calculation_date in sorted(expected_dates):
            symbols = tuple(self.eligible_symbols_by_date[calculation_date])
            if self.eligible_counts_by_date[calculation_date] != len(symbols):
                raise ValueError(
                    "static breadth eligible count does not match symbols for "
                    f"{calculation_date.isoformat()}"
                )
            signature = self.eligibility_signatures_by_date[calculation_date]
            records[calculation_date] = StaticBreadthDateEligibility(
                calculation_date=calculation_date,
                candidate_count=self.candidate_counts_by_date[calculation_date],
                eligible_symbols=symbols,
                universe_policy=self.universe_policy_by_date[calculation_date],
                eligibility_signature=signature,
            )
        object.__setattr__(self, "by_date", MappingProxyType(records))


def _bounded_sample(symbols: set[str], limit: int) -> tuple[str, ...]:
    return tuple(sorted(symbols)[:limit])


def static_breadth_eligibility_signature(symbols: Sequence[str]) -> str:
    """Compatibility name for the canonical shared breadth signature."""
    return breadth_eligibility_signature(symbols)


def classify_static_breadth_eligibility(
    db: Session,
    *,
    market: str,
    calculation_dates: Sequence[date],
    universe_resolver: CurrentActiveFallbackUniverseResolver | None = None,
    exclusion_sample_limit: int = DEFAULT_EXCLUSION_SAMPLE_LIMIT,
) -> StaticBreadthEligibility:
    """Resolve the point-in-time broad universe for each date.

    Price/history sufficiency deliberately does not filter this boundary. The
    shared engine applies the distinct data requirements for every metric.
    """
    if exclusion_sample_limit < 0:
        raise ValueError("exclusion_sample_limit must be non-negative")
    normalized_market = str(market or "").strip().upper()
    ordered_dates = tuple(sorted(set(calculation_dates)))
    resolver = universe_resolver or CurrentActiveFallbackUniverseResolver()

    candidates_by_date: dict[date, tuple[str, ...]] = {}
    policy_by_date: dict[date, str] = {}
    unsupported: set[str] = set()
    for calculation_date in ordered_dates:
        universe = resolver.resolve(
            db,
            market=normalized_market,
            as_of_date=calculation_date,
        )
        candidates = tuple(sorted(set(universe.symbols)))
        _supported, unsupported_for_date = split_supported_price_symbols(candidates)
        candidates_by_date[calculation_date] = candidates
        unsupported.update(unsupported_for_date)
        policy_by_date[calculation_date] = (
            resolver.policy_for(normalized_market, calculation_date) or "unrecorded"
        )

    eligible_by_date = dict(candidates_by_date)

    eligible_counts = {
        calculation_date: len(symbols)
        for calculation_date, symbols in eligible_by_date.items()
    }
    return StaticBreadthEligibility(
        eligible_symbols_by_date=MappingProxyType(eligible_by_date),
        candidate_counts_by_date=MappingProxyType(
            {
                calculation_date: len(symbols)
                for calculation_date, symbols in candidates_by_date.items()
            }
        ),
        eligible_counts_by_date=MappingProxyType(eligible_counts),
        universe_policy_by_date=MappingProxyType(policy_by_date),
        eligibility_signatures_by_date=MappingProxyType(
            {
                calculation_date: static_breadth_eligibility_signature(symbols)
                for calculation_date, symbols in eligible_by_date.items()
            }
        ),
        unsupported_count=len(unsupported),
        insufficient_history_count=0,
        exact_date_gap_count=0,
        unsupported_symbols=_bounded_sample(unsupported, exclusion_sample_limit),
        insufficient_history_symbols=(),
        exact_date_gap_symbols=(),
    )
