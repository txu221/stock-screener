"""Canonical orchestration for preparing static Market breadth history."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from types import MappingProxyType
from typing import Any, Protocol

from ..models.market_breadth import MarketBreadth
from .static_breadth_assessment import (
    classify_static_breadth_backfill,
)
from .static_breadth_eligibility import StaticBreadthEligibility

STATIC_BREADTH_RATIO_RECOMPUTE_TRADING_DAYS = 10


class _BreadthCalculator(Protocol):
    def backfill_range(self, start_date: date, end_date: date, **kwargs) -> dict:
        ...


@dataclass(frozen=True, slots=True)
class StaticBreadthHistoryRequest:
    market: str
    as_of_date: date
    min_trading_days: int
    lookback_days: int


@dataclass(frozen=True, slots=True)
class StaticBreadthHistoryResult:
    values: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return dict(self.values)


class StaticBreadthHistoryCoordinator:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Any],
        trading_dates: Callable[[date, date, str], Sequence[date]],
        eligibility_classifier: Callable[..., StaticBreadthEligibility],
        calculator_factory: Callable[[Any, Any, str], _BreadthCalculator],
        price_cache_factory: Callable[[], Any],
        message_sink: Callable[[str], None] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._trading_dates = trading_dates
        self._eligibility_classifier = eligibility_classifier
        self._calculator_factory = calculator_factory
        self._price_cache_factory = price_cache_factory
        self._message_sink = message_sink or (lambda _message: None)

    def ensure(
        self,
        request: StaticBreadthHistoryRequest,
    ) -> StaticBreadthHistoryResult:
        market = str(request.market or "US").upper()
        start_date = request.as_of_date - timedelta(days=request.lookback_days)
        desired_dates = list(
            self._trading_dates(start_date, request.as_of_date, market)
        )
        target_dates = (
            desired_dates[-request.min_trading_days :]
            if request.min_trading_days > 0
            else desired_dates
        )
        base_diagnostics = {
            "market": market,
            "as_of_date": request.as_of_date.isoformat(),
            "lookback_start_date": start_date.isoformat(),
            "target_trading_days": len(target_dates),
        }
        if not target_dates:
            return _result(
                {
                    "status": "skipped",
                    **base_diagnostics,
                    "recomputed_dates": 0,
                }
            )

        with self._session_factory() as db:
            existing_rows = (
                db.query(MarketBreadth)
                .filter(
                    MarketBreadth.date >= target_dates[0],
                    MarketBreadth.date <= request.as_of_date,
                    MarketBreadth.market == market,
                )
                .all()
            )
            existing_by_date = {row.date: row for row in existing_rows}
            existing_dates = set(existing_by_date)
            missing_dates = [
                calculation_date
                for calculation_date in target_dates
                if calculation_date not in existing_dates
            ]
            eligibility = self._eligibility_classifier(
                db,
                market=market,
                calculation_dates=target_dates,
            )
            incomplete_existing_dates = [
                calculation_date
                for calculation_date in target_dates
                if calculation_date in existing_by_date
                and getattr(
                    existing_by_date[calculation_date],
                    "eligibility_signature",
                    None,
                )
                != eligibility.eligibility_signatures_by_date[calculation_date]
            ]
            repair_dates = sorted(
                set(missing_dates + incomplete_existing_dates)
            )
            recompute_dates = static_breadth_recompute_dates(
                target_dates=target_dates,
                repair_dates=repair_dates,
                as_of_date=request.as_of_date,
            )

            if (
                len(recompute_dates) == 1
                and request.as_of_date in existing_dates
                and request.as_of_date not in incomplete_existing_dates
            ):
                self._message_sink(
                    "Existing breadth history already covers the last "
                    f"{len(target_dates)} trading days through "
                    f"{request.as_of_date}."
                )
                return _result(
                    {
                        "status": "skipped",
                        **base_diagnostics,
                        "missing_dates": 0,
                        "recomputed_dates": 0,
                        "validated_existing_dates": len(target_dates),
                        **static_breadth_eligibility_diagnostics(eligibility),
                    }
                )

            self._message_sink(
                f"Recomputing {len(recompute_dates)} dates "
                f"({len(missing_dates)} missing) to ensure "
                f"{len(target_dates)} trading-day history through "
                f"{request.as_of_date}."
            )
            calculator = self._calculator_factory(
                db,
                self._price_cache_factory(),
                market,
            )
            stats = calculator.backfill_range(
                start_date=recompute_dates[0],
                end_date=recompute_dates[-1],
                trading_dates=recompute_dates,
                cache_only=True,
                exclude_unsupported_price_symbols=True,
                required_as_of_date=request.as_of_date,
                eligible_symbols_by_date={
                    calculation_date: eligibility.eligible_symbols_by_date[
                        calculation_date
                    ]
                    for calculation_date in recompute_dates
                },
                eligibility_signatures_by_date={
                    calculation_date: eligibility.eligibility_signatures_by_date[
                        calculation_date
                    ]
                    for calculation_date in recompute_dates
                },
            )
            scanned_by_date = {
                row.date: int(row.total_stocks_scanned or 0)
                for row in existing_rows
            }
            scanned_by_date.update(
                {
                    date.fromisoformat(raw_date): int(count or 0)
                    for raw_date, count in (
                        stats.get("scanned_stocks_by_date") or {}
                    ).items()
                }
            )
            required_scanned_by_date = {
                row.date: int(
                    getattr(row, "advance_decline_eligible_count", 0) or 0
                )
                for row in existing_rows
            }
            computed_required = (
                stats.get("advance_decline_eligible_stocks_by_date")
                or stats.get("scanned_stocks_by_date")
                or {}
            )
            required_scanned_by_date.update(
                {
                    date.fromisoformat(raw_date): int(count or 0)
                    for raw_date, count in computed_required.items()
                }
            )
            assessment = classify_static_breadth_backfill(
                stats=stats,
                dates=target_dates,
                as_of_date=request.as_of_date,
                eligible_stocks_by_date=eligibility.eligible_counts_by_date,
                scanned_by_date=scanned_by_date,
                required_scanned_by_date=required_scanned_by_date,
            )
            stats.update(assessment.diagnostics())
            stats.update(
                {
                    "status": assessment.status,
                    **base_diagnostics,
                    "missing_dates": len(missing_dates),
                    "incomplete_existing_dates": len(
                        incomplete_existing_dates
                    ),
                    "recomputed_dates": len(recompute_dates),
                    **static_breadth_eligibility_diagnostics(eligibility),
                }
            )
            if assessment.error:
                stats["error"] = assessment.error
            return _result(stats)


def static_breadth_eligibility_diagnostics(
    eligibility: StaticBreadthEligibility,
) -> dict[str, Any]:
    return {
        "candidate_stocks_by_date": {
            calculation_date.isoformat(): record.candidate_count
            for calculation_date, record in eligibility.by_date.items()
        },
        "eligible_stocks_by_date": {
            calculation_date.isoformat(): len(record.eligible_symbols)
            for calculation_date, record in eligibility.by_date.items()
        },
        "universe_policy_by_date": {
            calculation_date.isoformat(): record.universe_policy
            for calculation_date, record in eligibility.by_date.items()
        },
        "eligibility_signatures_by_date": {
            calculation_date.isoformat(): record.eligibility_signature
            for calculation_date, record in eligibility.by_date.items()
        },
        "unsupported_symbols": eligibility.unsupported_count,
        "insufficient_history_symbols": eligibility.insufficient_history_count,
        "exact_date_gap_symbols": eligibility.exact_date_gap_count,
        "unsupported_symbols_sample": list(eligibility.unsupported_symbols),
        "insufficient_history_symbols_sample": list(
            eligibility.insufficient_history_symbols
        ),
        "exact_date_gap_symbols_sample": list(
            eligibility.exact_date_gap_symbols
        ),
    }


def static_breadth_recompute_dates(
    *,
    target_dates: Sequence[date],
    repair_dates: Sequence[date],
    as_of_date: date,
) -> list[date]:
    recompute_dates = set(repair_dates)
    index_by_date = {
        calculation_date: index
        for index, calculation_date in enumerate(target_dates)
    }
    for repair_date in repair_dates:
        repair_index = index_by_date.get(repair_date)
        if repair_index is None:
            continue
        affected_end_index = min(
            len(target_dates),
            repair_index + STATIC_BREADTH_RATIO_RECOMPUTE_TRADING_DAYS + 1,
        )
        recompute_dates.update(target_dates[repair_index:affected_end_index])
    recompute_dates.add(as_of_date)
    return sorted(recompute_dates)


def _result(values: Mapping[str, Any]) -> StaticBreadthHistoryResult:
    return StaticBreadthHistoryResult(MappingProxyType(dict(values)))
