"""Static-site breadth readiness against date-specific eligible universes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class StaticBreadthBackfillAssessment:
    eligible_stocks_by_date: Mapping[date, int]
    scanned_stocks_by_date: Mapping[date, int]
    hard_error_dates: tuple[date, ...] = ()
    tolerated_error_dates: tuple[date, ...] = ()
    undercovered_dates: tuple[date, ...] = ()
    zero_eligible_dates: tuple[date, ...] = ()
    unclassified_error_count: int = 0
    error: str | None = None

    @property
    def status(self) -> str:
        return "errored" if self.error else "completed"

    @property
    def ready_for_exposure(self) -> bool:
        return self.error is None

    def diagnostics(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "eligible_stocks_by_date": {
                calculation_date.isoformat(): count
                for calculation_date, count in self.eligible_stocks_by_date.items()
            },
            "scanned_stocks_by_date": {
                calculation_date.isoformat(): count
                for calculation_date, count in self.scanned_stocks_by_date.items()
            },
            "hard_error_dates": [day.isoformat() for day in self.hard_error_dates],
            "unclassified_error_count": self.unclassified_error_count,
        }
        for key, values in (
            ("tolerated_error_dates", self.tolerated_error_dates),
            ("undercovered_dates", self.undercovered_dates),
            ("zero_eligible_dates", self.zero_eligible_dates),
        ):
            if values:
                payload[key] = [day.isoformat() for day in values]
        return payload


def classify_static_breadth_backfill(
    *,
    stats: Mapping[str, Any],
    dates: Sequence[date],
    as_of_date: date,
    eligible_stocks_by_date: Mapping[date, int],
    scanned_by_date: Mapping[date, int],
    required_scanned_by_date: Mapping[date, int] | None = None,
) -> StaticBreadthBackfillAssessment:
    ordered_dates = tuple(sorted(set(dates)))
    eligible = {
        calculation_date: int(eligible_stocks_by_date.get(calculation_date, 0))
        for calculation_date in ordered_dates
    }
    scanned = {
        calculation_date: int(scanned_by_date.get(calculation_date, 0) or 0)
        for calculation_date in ordered_dates
    }
    required_scanned = {
        calculation_date: int(
            (required_scanned_by_date or eligible_stocks_by_date).get(
                calculation_date,
                0,
            )
            or 0
        )
        for calculation_date in ordered_dates
    }
    error_dates = _static_breadth_error_dates(stats)
    hard_error_dates: list[date] = []
    tolerated_error_dates: list[date] = []
    undercovered_dates: list[date] = []
    zero_eligible_dates: list[date] = []
    has_seen_valid_history = False

    for calculation_date in ordered_dates:
        if eligible[calculation_date] <= 0:
            zero_eligible_dates.append(calculation_date)
            continue
        if calculation_date in error_dates:
            if calculation_date == as_of_date or has_seen_valid_history:
                hard_error_dates.append(calculation_date)
            else:
                tolerated_error_dates.append(calculation_date)
            continue
        if static_breadth_row_has_accepted_coverage(
            scanned[calculation_date],
            eligible_stocks=required_scanned[calculation_date],
        ):
            has_seen_valid_history = True
        elif calculation_date == as_of_date or has_seen_valid_history:
            undercovered_dates.append(calculation_date)

    unclassified_error_count = _static_breadth_unclassified_error_count(
        stats,
        classified_error_dates=error_dates,
    )
    values = {
        "eligible_stocks_by_date": MappingProxyType(eligible),
        "scanned_stocks_by_date": MappingProxyType(scanned),
        "hard_error_dates": tuple(hard_error_dates),
        "tolerated_error_dates": tuple(tolerated_error_dates),
        "undercovered_dates": tuple(undercovered_dates),
        "zero_eligible_dates": tuple(zero_eligible_dates),
        "unclassified_error_count": unclassified_error_count,
    }
    return StaticBreadthBackfillAssessment(
        **values,
        error=_static_breadth_backfill_error(stats, **values),
    )


def static_breadth_row_has_accepted_coverage(
    total_stocks_scanned: int | None,
    *,
    eligible_stocks: int,
) -> bool:
    return eligible_stocks > 0 and int(total_stocks_scanned or 0) >= eligible_stocks


def static_breadth_backfill_needs_scan_counts(
    stats: Mapping[str, Any],
    *,
    eligible_stocks_by_date: Mapping[date, int],
) -> bool:
    return bool(eligible_stocks_by_date) or bool(_static_breadth_error_dates(stats))


def _static_breadth_backfill_error(
    stats: Mapping[str, Any],
    *,
    eligible_stocks_by_date: Mapping[date, int],
    scanned_stocks_by_date: Mapping[date, int],
    hard_error_dates: tuple[date, ...],
    tolerated_error_dates: tuple[date, ...],
    undercovered_dates: tuple[date, ...],
    zero_eligible_dates: tuple[date, ...],
    unclassified_error_count: int,
) -> str | None:
    del tolerated_error_dates
    calculation_errors = int(stats.get("error_stocks") or 0)
    if calculation_errors > 0:
        return (
            "Cache-only breadth backfill has calculation errors "
            f"(error_stocks={calculation_errors})"
        )
    if zero_eligible_dates:
        sample = ",".join(day.isoformat() for day in zero_eligible_dates)
        return f"Static breadth has zero eligible stocks (dates={sample})"
    if hard_error_dates:
        sample = ",".join(day.isoformat() for day in hard_error_dates)
        return f"Cache-only breadth backfill has hard date errors (dates={sample})"
    if unclassified_error_count > 0:
        return f"Cache-only breadth backfill has errors (errors={unclassified_error_count})"
    total_dates = int(stats.get("total_dates") or 0)
    processed = int(stats.get("processed") or 0)
    if total_dates > 0 and processed == 0:
        return "Cache-only breadth backfill processed no dates"
    if undercovered_dates:
        sample = ",".join(
            f"{day.isoformat()}:{scanned_stocks_by_date[day]}/"
            f"{eligible_stocks_by_date[day]}"
            for day in undercovered_dates
        )
        return (
            "Cache-only breadth backfill has insufficient usable coverage "
            f"(scanned/eligible={sample})"
        )
    return None


def _static_breadth_error_dates(stats: Mapping[str, Any]) -> set[date]:
    raw_error_dates = stats.get("error_dates")
    if not isinstance(raw_error_dates, list):
        return set()
    result: set[date] = set()
    for raw_date in raw_error_dates:
        if not isinstance(raw_date, str):
            continue
        try:
            result.add(date.fromisoformat(raw_date))
        except ValueError:
            continue
    return result


def _static_breadth_unclassified_error_count(
    stats: Mapping[str, Any],
    *,
    classified_error_dates: set[date],
) -> int:
    return max(0, int(stats.get("errors") or 0) - len(classified_error_dates))
