"""Pure content identity for corporate-action price evidence."""

from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
import json
from typing import Any, Mapping


def _timestamp_as_utc_iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        timestamp = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    elif isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
    else:
        return None
    return timestamp.astimezone(timezone.utc).isoformat()


def price_row_content_hash(evidence: Mapping[str, Any]) -> str:
    """Return the stable identity of the provider evidence in one price row."""
    row_date = evidence.get("date")
    content = {
        "symbol": evidence.get("symbol"),
        "date": row_date.isoformat() if isinstance(row_date, date) else row_date,
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
        "source_timestamp": _timestamp_as_utc_iso(
            evidence.get("source_timestamp")
        ),
        "normalization_version": evidence.get("normalization_version"),
    }
    return sha256(
        json.dumps(
            content,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
