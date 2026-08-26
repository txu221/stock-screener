"""Strict, non-coercing validation for Market Intelligence provider bars."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal
from numbers import Real
from typing import Any

from .constants import (
    MARKET_INTELLIGENCE_UNIVERSE,
    NORMALIZATION_VERSION,
    PRICE_BASIS,
)
from .models import (
    BarRejection,
    CanonicalBar,
    RawBar,
    RejectionCode,
    ValidationResult,
)

_REQUIRED_VALUE_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "adjusted_close",
    "volume",
)


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return "NaN"
        return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _raw_evidence(row: RawBar) -> dict[str, Any]:
    return {
        "provider": row.provider,
        "provider_symbol": row.provider_symbol,
        "symbol": row.symbol,
        "raw_trading_date": _json_safe_value(row.raw_trading_date),
        "trading_date": _json_safe_value(row.trading_date),
        "open": _json_safe_value(row.open),
        "high": _json_safe_value(row.high),
        "low": _json_safe_value(row.low),
        "close": _json_safe_value(row.close),
        "adjusted_close": _json_safe_value(row.adjusted_close),
        "volume": _json_safe_value(row.volume),
        "source_timestamp": _json_safe_value(row.source_timestamp),
    }


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _validation_error(
    row: RawBar,
    *,
    expected_sessions: frozenset[date],
    duplicate_keys: frozenset[tuple[str, date]],
) -> tuple[RejectionCode, str] | None:
    if row.symbol not in MARKET_INTELLIGENCE_UNIVERSE:
        return (
            RejectionCode.UNEXPECTED_SYMBOL,
            f"symbol {row.symbol!r} is outside the fixed Phase 1 universe",
        )
    if not isinstance(row.trading_date, date) or row.trading_date not in expected_sessions:
        return (
            RejectionCode.INVALID_TRADING_DATE,
            "trading date is missing or outside the completed-session window",
        )
    if any(getattr(row, field) is None for field in _REQUIRED_VALUE_FIELDS):
        return (
            RejectionCode.MISSING_REQUIRED_FIELD,
            "one or more required OHLCV/adjusted-close fields are missing",
        )

    values = {
        field: _finite_number(getattr(row, field))
        for field in _REQUIRED_VALUE_FIELDS
    }
    if values["adjusted_close"] is None:
        return (
            RejectionCode.INVALID_ADJUSTED_CLOSE,
            "provider adjusted close must be a finite numeric value",
        )
    if any(
        values[field] is None
        for field in ("open", "high", "low", "close", "volume")
    ):
        return (
            RejectionCode.NON_FINITE_VALUE,
            "OHLCV and adjusted close must be finite numeric values",
        )

    open_ = values["open"]
    high = values["high"]
    low = values["low"]
    close = values["close"]
    adjusted_close = values["adjusted_close"]
    volume = values["volume"]
    assert None not in (open_, high, low, close, adjusted_close, volume)

    if open_ <= 0 or high <= 0 or low <= 0 or close <= 0:
        return (
            RejectionCode.NON_POSITIVE_PRICE,
            "raw Open, High, Low, and Close must be greater than zero",
        )
    if adjusted_close <= 0:
        return (
            RejectionCode.INVALID_ADJUSTED_CLOSE,
            "provider adjusted close must be greater than zero",
        )
    if volume < 0:
        return (
            RejectionCode.NEGATIVE_VOLUME,
            "provider volume must be non-negative",
        )

    adjustment_factor = adjusted_close / close
    if not math.isfinite(adjustment_factor) or adjustment_factor <= 0:
        return (
            RejectionCode.INVALID_ADJUSTMENT_FACTOR,
            "adjusted-close ratio must be finite and greater than zero",
        )
    if (
        high < low
        or high < open_
        or high < close
        or low > open_
        or low > close
    ):
        return (
            RejectionCode.INVALID_OHLC_RELATION,
            "raw OHLC values violate high/low range relationships",
        )
    if (row.symbol, row.trading_date) in duplicate_keys:
        return (
            RejectionCode.DUPLICATE_BAR,
            "duplicate symbol and trading-date observation",
        )
    return None


def _canonical_bar(row: RawBar, *, ingestion_timestamp: datetime) -> CanonicalBar:
    open_ = float(row.open)
    high = float(row.high)
    low = float(row.low)
    close = float(row.close)
    adjusted_close = float(row.adjusted_close)
    volume = float(row.volume)
    adjustment_factor = adjusted_close / close
    assert row.trading_date is not None
    return CanonicalBar(
        provider=row.provider,
        provider_symbol=row.provider_symbol,
        symbol=row.symbol,
        raw_trading_date=row.raw_trading_date,
        trading_date=row.trading_date,
        raw_open=open_,
        raw_high=high,
        raw_low=low,
        raw_close=close,
        provider_adjusted_close=adjusted_close,
        adjustment_factor=adjustment_factor,
        adjusted_open=open_ * adjustment_factor,
        adjusted_high=high * adjustment_factor,
        adjusted_low=low * adjustment_factor,
        adjusted_close=close * adjustment_factor,
        provider_volume=volume,
        source_timestamp=row.source_timestamp,
        ingestion_timestamp=ingestion_timestamp,
        price_basis=PRICE_BASIS,
        normalization_version=NORMALIZATION_VERSION,
    )


def validate_provider_rows(
    rows: Iterable[RawBar],
    expected_sessions: Sequence[date],
    ingestion_timestamp: datetime,
) -> ValidationResult:
    """Validate provider rows without repair, coercion, or silent dropping."""
    raw_rows = tuple(rows)
    ordered_sessions = tuple(expected_sessions)
    if any(
        current >= following
        for current, following in zip(ordered_sessions, ordered_sessions[1:])
    ):
        raise ValueError(
            "expected_sessions must be strictly increasing and unique"
        )
    expected_session_set = frozenset(ordered_sessions)
    key_counts = Counter(
        (row.symbol, row.trading_date)
        for row in raw_rows
        if isinstance(row.trading_date, date)
    )
    duplicate_keys = frozenset(
        key for key, count in key_counts.items() if count > 1
    )
    canonical: list[CanonicalBar] = []
    rejections: list[BarRejection] = []

    for row in raw_rows:
        error = _validation_error(
            row,
            expected_sessions=expected_session_set,
            duplicate_keys=duplicate_keys,
        )
        if error is None:
            canonical.append(
                _canonical_bar(row, ingestion_timestamp=ingestion_timestamp)
            )
            continue
        code, reason = error
        rejections.append(
            BarRejection(
                provider=row.provider,
                provider_symbol=row.provider_symbol,
                symbol=row.symbol,
                trading_date=row.trading_date,
                code=code,
                reason=reason,
                raw_evidence=_raw_evidence(row),
                ingestion_timestamp=ingestion_timestamp,
            )
        )

    canonical.sort(key=lambda bar: (bar.symbol, bar.trading_date))
    received_symbols = tuple(sorted({str(row.symbol) for row in raw_rows}))
    return ValidationResult(
        canonical_bars=tuple(canonical),
        rejections=tuple(rejections),
        received_symbols=received_symbols,
    )
