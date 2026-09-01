"""Pure validation for provider corporate-action adjustment evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
from typing import Any, Sequence


_MIN_EXPLAINED_FACTOR_RATIO = 0.5
_MAX_EXPLAINED_FACTOR_RATIO = 2.0


class CorporateActionReconciliationError(RuntimeError):
    """Provider action evidence is unsafe to promote into analytical prices."""

    market_intelligence_stage = "corporate_action_reconciliation"
    market_intelligence_failure_category = (
        "CORPORATE_ACTION_RECONCILIATION_FAILURE"
    )


@dataclass(frozen=True)
class CorporateActionEvidence:
    symbol: str
    trading_date: date | str
    close: Any
    adjusted_close: Any
    dividend_cash: Any = None
    split_ratio: Any = None


def _optional_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise CorporateActionReconciliationError(
            "corporate-action evidence is not numeric"
        ) from None
    if math.isnan(number):
        return None
    if not math.isfinite(number):
        raise CorporateActionReconciliationError(
            "corporate-action evidence is not finite"
        )
    return number


def _date_label(value: date | str) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)


def _has_action(row: CorporateActionEvidence) -> bool:
    return any(
        (_optional_number(value) or 0.0) > 0
        for value in (row.dividend_cash, row.split_ratio)
    )


def _positive_price(value: Any) -> float | None:
    try:
        number = _optional_number(value)
    except CorporateActionReconciliationError:
        return None
    if number is None or number <= 0:
        return None
    return number


def validate_corporate_action_sequence(
    evidence: Sequence[CorporateActionEvidence],
) -> None:
    """Reject invalid actions and unexplained, extreme factor discontinuities."""
    previous_factor: float | None = None
    previous_row: CorporateActionEvidence | None = None
    for row in evidence:
        date_label = _date_label(row.trading_date)
        for field_name, raw_value in (
            ("Dividends", row.dividend_cash),
            ("Stock Splits", row.split_ratio),
        ):
            value = _optional_number(raw_value)
            if value is not None and value < 0:
                raise CorporateActionReconciliationError(
                    f"{row.symbol} {date_label} has negative {field_name}"
                )

        close = _positive_price(row.close)
        adjusted_close = _positive_price(row.adjusted_close)
        if close is None or adjusted_close is None:
            previous_factor = None
            previous_row = None
            continue
        factor = adjusted_close / close
        if previous_factor is not None:
            ratio = factor / previous_factor
            has_action = _has_action(row) or (
                previous_row is not None and _has_action(previous_row)
            )
            if (
                ratio < _MIN_EXPLAINED_FACTOR_RATIO
                or ratio > _MAX_EXPLAINED_FACTOR_RATIO
            ) and not has_action:
                raise CorporateActionReconciliationError(
                    f"{row.symbol} {date_label} has unexplained "
                    "adjustment-factor discontinuity"
                )
        previous_factor = factor
        previous_row = row
