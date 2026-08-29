"""Strict-boundary adapter over the existing Yahoo bulk price path."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from app.domain.market_intelligence.models import (
    ProviderBatchResult,
    ProviderSymbolFailure,
    RawBar,
    RequestFailure,
)

_REQUIRED_COLUMNS = frozenset(
    {
        "Open",
        "High",
        "Low",
        "Close",
        "Adj Close",
        "Volume",
        "Dividends",
        "Stock Splits",
    }
)


def _schema_drift(message: str) -> RequestFailure:
    return RequestFailure(code="PROVIDER_SCHEMA_DRIFT", message=message)


def _frame_schema_error(symbol: str, frame: Any) -> str | None:
    if not isinstance(frame, pd.DataFrame):
        return f"{symbol}: price_data is not a pandas DataFrame"
    if frame.empty:
        return f"{symbol}: price frame contains no rows"
    missing = sorted(_REQUIRED_COLUMNS.difference(frame.columns))
    if missing:
        return f"{symbol}: price frame is missing required columns: {', '.join(missing)}"
    if not isinstance(frame.index, pd.DatetimeIndex):
        return f"{symbol}: price frame index is not a DatetimeIndex"
    try:
        normalized_index = (
            frame.index.tz_localize("UTC")
            if frame.index.tz is None
            else frame.index.tz_convert("UTC")
        )
    except (TypeError, ValueError) as exc:
        return f"{symbol}: price frame timezone cannot be normalized: {exc}"
    if normalized_index.hasnans:
        return f"{symbol}: price frame index contains invalid timestamps"
    if not normalized_index.is_monotonic_increasing:
        return f"{symbol}: price frame index is not monotonic increasing"
    if not normalized_index.is_unique:
        return f"{symbol}: price frame index contains duplicate timestamps"
    non_numeric = sorted(
        column
        for column in _REQUIRED_COLUMNS
        if not pd.api.types.is_numeric_dtype(frame[column])
    )
    if non_numeric:
        return f"{symbol}: price frame has non-numeric columns: {', '.join(non_numeric)}"
    return None


def _batch_schema_error(
    results: Mapping[str, Any],
    requested: Sequence[str],
) -> str | None:
    result_symbols = set(results)
    requested_symbols = set(requested)
    if result_symbols != requested_symbols:
        missing = sorted(requested_symbols.difference(result_symbols))
        unexpected = sorted(result_symbols.difference(requested_symbols))
        details = []
        if missing:
            details.append(f"missing symbols: {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected symbols: {', '.join(unexpected)}")
        return f"Yahoo bulk response symbol coverage differs from request ({'; '.join(details)})"
    for symbol in requested:
        entry = results[symbol]
        if not isinstance(entry, Mapping):
            return f"{symbol}: symbol response is not a mapping"
        if entry.get("symbol") != symbol:
            return f"{symbol}: symbol response identity does not match requested symbol"
        if not isinstance(entry.get("has_error"), bool):
            return f"{symbol}: symbol response has invalid has_error flag"
        if bool(entry["has_error"]):
            continue
        error = _frame_schema_error(symbol, entry.get("price_data"))
        if error is not None:
            return error
    return None


def _failure_code(value: Any) -> str:
    raw = getattr(value, "value", value)
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", str(raw or "PROVIDER_SYMBOL_ERROR"))
    return normalized.strip("_").upper() or "PROVIDER_SYMBOL_ERROR"


def _request_failure(exc: Exception) -> RequestFailure:
    if isinstance(exc, TimeoutError):
        code = "PROVIDER_TIMEOUT"
    elif isinstance(exc, PermissionError):
        code = "PROVIDER_AUTHENTICATION"
    elif isinstance(exc, (ConnectionError, OSError)):
        code = "PROVIDER_NETWORK"
    else:
        code = "PROVIDER_REQUEST_FAILED"
    return RequestFailure(code=code, message=str(exc) or type(exc).__name__)


def _normalized_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    to_python = getattr(value, "to_pydatetime", None)
    if callable(to_python):
        converted = to_python()
        if isinstance(converted, datetime):
            return converted.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _source_timestamp(entry: Mapping[str, Any]) -> datetime | None:
    value = entry.get("source_timestamp", entry.get("as_of"))
    if value is None:
        return None
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    return None


def _wrapped_batch_failure(
    results: Mapping[str, Any],
    requested: Sequence[str],
) -> RequestFailure | None:
    entries = [results.get(symbol) for symbol in requested]
    if not entries or not all(
        isinstance(entry, Mapping) and bool(entry.get("has_error"))
        for entry in entries
    ):
        return None
    messages = {str(entry.get("error") or "") for entry in entries}
    if len(messages) != 1:
        return None
    message = next(iter(messages))
    lowered = message.lower()
    if not (
        lowered.startswith("batch download error:")
        or "yf.download returned empty" in lowered
    ):
        return None
    if "timed out" in lowered or "timeout" in lowered:
        code = "PROVIDER_TIMEOUT"
    elif "auth" in lowered or "unauthorized" in lowered:
        code = "PROVIDER_AUTHENTICATION"
    elif "connection" in lowered or "network" in lowered:
        code = "PROVIDER_NETWORK"
    else:
        code = "PROVIDER_BAD_RESPONSE"
    return RequestFailure(code=code, message=message)


class YahooMarketIntelligenceProvider:
    """Map bulk Yahoo frames to raw rows without normalizing or repairing them."""

    def __init__(self, fetcher: Any, *, clock) -> None:
        self._fetcher = fetcher
        self._clock = clock

    def fetch(
        self,
        symbols: Sequence[str],
        as_of: date,
    ) -> ProviderBatchResult:
        requested = tuple(symbols)
        try:
            results = self._fetcher.fetch_batch_prices(
                list(requested), period="6mo"
            )
        except Exception as exc:
            return ProviderBatchResult(
                provider="yahoo",
                response_timestamp=self._clock(),
                rows=(),
                symbol_failures=(),
                request_failure=_request_failure(exc),
            )

        response_timestamp = self._clock()
        if not isinstance(results, Mapping):
            return ProviderBatchResult(
                provider="yahoo",
                response_timestamp=response_timestamp,
                rows=(),
                symbol_failures=(),
                request_failure=_schema_drift(
                    "bulk price response is not a symbol mapping"
                ),
            )
        wrapped_failure = _wrapped_batch_failure(results, requested)
        if wrapped_failure is not None:
            return ProviderBatchResult(
                provider="yahoo",
                response_timestamp=response_timestamp,
                rows=(),
                symbol_failures=(),
                request_failure=wrapped_failure,
            )

        schema_error = _batch_schema_error(results, requested)
        if schema_error is not None:
            return ProviderBatchResult(
                provider="yahoo",
                response_timestamp=response_timestamp,
                rows=(),
                symbol_failures=(),
                request_failure=_schema_drift(schema_error),
            )

        rows: list[RawBar] = []
        failures: list[ProviderSymbolFailure] = []
        for symbol in requested:
            entry = results.get(symbol)
            assert isinstance(entry, Mapping)
            if bool(entry.get("has_error")):
                failures.append(
                    ProviderSymbolFailure(
                        symbol=symbol,
                        code=_failure_code(entry.get("error_kind")),
                        message=str(entry.get("error") or "Yahoo symbol fetch failed"),
                    )
                )
                continue

            frame = entry["price_data"]
            assert isinstance(frame, pd.DataFrame)
            frame_rows = tuple(frame.iterrows())

            source_timestamp = _source_timestamp(entry)
            emitted = 0
            for raw_index, values in frame_rows:
                trading_date = _normalized_date(raw_index)
                if trading_date is not None and trading_date > as_of:
                    continue
                rows.append(
                    RawBar(
                        provider="yahoo",
                        provider_symbol=str(entry.get("symbol") or symbol),
                        symbol=symbol,
                        raw_trading_date=raw_index,
                        trading_date=trading_date,
                        open=values["Open"],
                        high=values["High"],
                        low=values["Low"],
                        close=values["Close"],
                        adjusted_close=values["Adj Close"],
                        volume=values["Volume"],
                        source_timestamp=source_timestamp,
                        dividend_cash=values["Dividends"],
                        split_ratio=values["Stock Splits"],
                    )
                )
                emitted += 1
            if emitted == 0:
                failures.append(
                    ProviderSymbolFailure(
                        symbol=symbol,
                        code="NO_ROWS_AS_OF",
                        message=f"price frame has no rows on or before {as_of.isoformat()}",
                    )
                )

        rows.sort(
            key=lambda row: (
                row.symbol,
                row.trading_date or date.min,
                str(row.raw_trading_date),
            )
        )
        return ProviderBatchResult(
            provider="yahoo",
            response_timestamp=response_timestamp,
            rows=tuple(rows),
            symbol_failures=tuple(failures),
            request_failure=None,
        )
