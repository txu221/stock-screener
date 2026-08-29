"""Shared normalization for persisted OHLCV price rows."""

from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
import json
from typing import Any, Mapping

import pandas as pd

from app.infra.serialization import finite_float_or_none

OHLC_COLUMNS = ("Open", "High", "Low", "Close")
CANONICAL_PRICE_NORMALIZATION_VERSION = "canonical_price_adjustment_v2"
RECONCILED_PRICE_BASIS = "yahoo_adjusted_close_provider_volume"
UNRECONCILED_PRICE_BASIS = "raw_ohlcv_unreconciled"


def finite_ohlc_values(
    open_: Any,
    high: Any,
    low: Any,
    close: Any,
) -> tuple[float, float, float, float] | None:
    open_price = finite_float_or_none(open_)
    high_price = finite_float_or_none(high)
    low_price = finite_float_or_none(low)
    close_price = finite_float_or_none(close)
    if (
        open_price is None
        or high_price is None
        or low_price is None
        or close_price is None
    ):
        return None
    return open_price, high_price, low_price, close_price


def drop_non_finite_close_rows(data: pd.DataFrame | None) -> pd.DataFrame | None:
    """Remove rows whose OHLC values cannot be safely treated as market prices."""
    if data is None or data.empty:
        return data
    if any(column not in data.columns for column in OHLC_COLUMNS):
        return data.iloc[0:0].copy()
    keep_mask = pd.Series(True, index=data.index)
    for column in OHLC_COLUMNS:
        keep_mask &= data[column].map(finite_float_or_none).notna()
    if bool(keep_mask.all()):
        return data
    return data.loc[keep_mask].copy()


def normalize_price_frame(
    data: pd.DataFrame | None,
    *,
    min_rows: int = 1,
) -> pd.DataFrame | None:
    """Return a finite-close OHLCV frame that satisfies the row-count contract."""
    cleaned = drop_non_finite_close_rows(data)
    if cleaned is None or cleaned.empty:
        return None
    if len(cleaned) < min_rows:
        return None
    return cleaned


def normalize_price_batch(
    batch_data: Mapping[str, pd.DataFrame | None],
    *,
    min_rows: int = 1,
) -> dict[str, pd.DataFrame]:
    """Normalize a symbol->price-frame batch and drop unusable symbols."""
    normalized: dict[str, pd.DataFrame] = {}
    for symbol, data in batch_data.items():
        cleaned = normalize_price_frame(data, min_rows=min_rows)
        if cleaned is not None:
            normalized[symbol] = cleaned
    return normalized


def _volume_or_zero(value: Any) -> int:
    number = finite_float_or_none(value)
    if number is None:
        return 0
    return int(number)


def _timestamp_or_none(value: datetime | str | None) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    return None


def _finite_or_none(value: Any) -> float | None:
    return finite_float_or_none(value)


def price_row_content_hash(evidence: Mapping[str, Any]) -> str:
    """Return a stable hash for the provider evidence carried by one price row."""
    content = {
        "symbol": evidence.get("symbol"),
        "date": evidence.get("date").isoformat() if isinstance(evidence.get("date"), date) else evidence.get("date"),
        "open": evidence.get("open"),
        "high": evidence.get("high"),
        "low": evidence.get("low"),
        "close": evidence.get("close"),
        "volume": evidence.get("volume"),
        "adj_close": evidence.get("adj_close"),
        "adjustment_factor": evidence.get("adjustment_factor"),
        "dividend_cash": evidence.get("dividend_cash"),
        "split_ratio": evidence.get("split_ratio"),
        "provider": evidence.get("provider"),
        "normalization_version": evidence.get("normalization_version"),
    }
    return sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def stock_price_row_from_ohlcv(
    *,
    symbol: str,
    row_date: date,
    row: Mapping[str, Any],
    provider: str | None = None,
    source_timestamp: datetime | str | None = None,
    normalization_version: str | None = None,
) -> dict[str, Any] | None:
    """Build a StockPrice mapping, skipping rows without complete finite OHLC."""
    ohlc = finite_ohlc_values(
        row.get("Open"),
        row.get("High"),
        row.get("Low"),
        row.get("Close"),
    )
    if ohlc is None:
        return None
    open_, high, low, close = ohlc
    adj_close = finite_float_or_none(row.get("Adj Close"))
    provider_name = (str(provider).strip() or None) if provider is not None else None
    timestamp = _timestamp_or_none(source_timestamp)
    dividend_cash = _finite_or_none(row.get("Dividends"))
    split_ratio = _finite_or_none(row.get("Stock Splits"))
    adjustment_factor = (
        adj_close / close
        if adj_close is not None and adj_close > 0 and close > 0
        else None
    )
    reconciled = (
        adjustment_factor is not None
        and provider_name is not None
        and timestamp is not None
        and normalization_version == CANONICAL_PRICE_NORMALIZATION_VERSION
    )
    normalized = {
        "symbol": symbol,
        "date": row_date,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": _volume_or_zero(row.get("Volume")),
        "adj_close": adj_close,
        "adjustment_factor": adjustment_factor,
        "dividend_cash": dividend_cash,
        "split_ratio": split_ratio,
        "provider": provider_name,
        "source_timestamp": timestamp,
        "normalization_version": normalization_version,
        "price_basis": RECONCILED_PRICE_BASIS if reconciled else UNRECONCILED_PRICE_BASIS,
        "reconciled_at": datetime.now(timezone.utc) if reconciled else None,
    }
    normalized["content_hash"] = price_row_content_hash(normalized)
    return normalized
