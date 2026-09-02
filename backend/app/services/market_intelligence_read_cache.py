"""Pointer-versioned Redis cache for stable Market Intelligence reads.

The publication pointer is the invalidation boundary: a newly published run
gets new keys immediately, while partial and failed attempts keep readers on
the previous generation. Redis is an optional acceleration layer; every
failure path falls back to the caller's PostgreSQL-backed computation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
import json
import logging
import math
import threading
from typing import Any
from urllib.parse import quote

from app.config import settings
from app.services import redis_pool


logger = logging.getLogger(__name__)

CACHE_FORMAT_VERSION = "v2"
MIN_TTL_SECONDS = 60
MAX_TTL_SECONDS = 7 * 24 * 60 * 60
_CACHE_KEY_PREFIX = f"market-intelligence:read:{CACHE_FORMAT_VERSION}"
_CACHE_MISS = object()


def get_redis_client() -> Any:
    """Resolve the shared client lazily so runtime/test pool changes are honored."""
    return redis_pool.get_redis_client()


@dataclass(frozen=True)
class MarketIntelligenceCacheKeyParts:
    """Stable publication identity and endpoint inputs for one cache key."""

    endpoint: str
    stable_run_id: int
    stable_trading_date: date
    metric_version: str
    stable_pointer_revision: datetime | None = None
    published_generation: str | None = None
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class _LocalKeyLock:
    lock: threading.Lock = field(default_factory=threading.Lock)
    users: int = 0
    result_ready: bool = False
    result: Any = None
    error: BaseException | None = None


_local_cache_locks: dict[str, _LocalKeyLock] = {}
_local_cache_locks_guard = threading.Lock()


def _normalize_json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Enum):
        return _normalize_json_value(value.value)
    if isinstance(value, datetime):
        normalized = value
        if normalized.tzinfo is not None:
            normalized = normalized.astimezone(timezone.utc)
        return normalized.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("cache key parameters must be finite")
        if value == 0:
            return 0
        return int(value) if value.is_integer() else value
    if isinstance(value, Mapping):
        return {
            str(key): _normalize_json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if item is not None
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(
            (_normalize_json_value(item) for item in value),
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
        )
    raise TypeError(f"unsupported cache key parameter type: {type(value).__name__}")


def _canonical_params(params: Mapping[str, Any]) -> str:
    return json.dumps(
        _normalize_json_value(params),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def build_market_intelligence_cache_key(
    parts: MarketIntelligenceCacheKeyParts,
) -> str:
    """Build a deterministic key containing the complete stable identity."""
    endpoint = parts.endpoint.strip().casefold()
    metric_version = parts.metric_version.strip()
    if not endpoint or not metric_version:
        raise ValueError("endpoint and metric_version are required")
    if int(parts.stable_run_id) <= 0:
        raise ValueError("stable_run_id must be positive")
    if not isinstance(parts.stable_trading_date, date):
        raise TypeError("stable_trading_date must be a date")
    return (
        f"{_CACHE_KEY_PREFIX}:{quote(endpoint, safe='')}:"
        f"run:{int(parts.stable_run_id)}:"
        f"date:{parts.stable_trading_date.isoformat()}:"
        f"metric:{quote(metric_version, safe='')}:"
        f"revision:{quote(str(_normalize_json_value(parts.stable_pointer_revision)), safe='')}:"
        f"generation:{quote(str(parts.published_generation), safe='')}:"
        f"params:{quote(_canonical_params(parts.params), safe='')}"
    )


def _cache_ttl_seconds() -> int:
    configured = int(getattr(settings, "cache_ttl_seconds", MAX_TTL_SECONDS))
    return min(MAX_TTL_SECONDS, max(MIN_TTL_SECONDS, configured))


def _decode_cached_value(raw: Any, *, key: str) -> Any:
    if raw is None:
        return _CACHE_MISS

    def reject_non_finite(value: str) -> None:
        raise ValueError(f"non-finite JSON constant: {value}")

    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw, parse_constant=reject_non_finite)
    except (TypeError, UnicodeDecodeError, ValueError) as exc:
        logger.warning("Market Intelligence cache JSON is invalid for %s: %s", key, exc)
        return _CACHE_MISS


def _read_cached_value(
    client: Any,
    *,
    key: str,
    validate_cached: Callable[[Any], Any] | None = None,
) -> tuple[Any, bool]:
    """Return ``(value, client_usable)`` with `_CACHE_MISS` on any miss."""
    try:
        raw = client.get(key)
    except Exception as exc:  # noqa: BLE001 - Redis must remain fail-open.
        logger.warning("Market Intelligence cache read failed for %s: %s", key, exc)
        return _CACHE_MISS, False
    decoded = _decode_cached_value(raw, key=key)
    if decoded is _CACHE_MISS or validate_cached is None:
        return decoded, True
    try:
        return validate_cached(decoded), True
    except Exception as exc:  # noqa: BLE001 - incompatible cache data is a miss.
        logger.warning(
            "Market Intelligence cached payload schema is invalid for %s: %s",
            key,
            exc,
        )
        return _CACHE_MISS, True


def _serialize_payload(value: Any) -> str:
    return json.dumps(
        value,
        default=lambda item: item.isoformat()
        if isinstance(item, (date, datetime))
        else str(item),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _acquire_local_key_lock(key: str) -> _LocalKeyLock:
    with _local_cache_locks_guard:
        entry = _local_cache_locks.get(key)
        if entry is None:
            entry = _LocalKeyLock()
            _local_cache_locks[key] = entry
        entry.users += 1
    entry.lock.acquire()
    return entry


def _release_local_key_lock(key: str, entry: _LocalKeyLock) -> None:
    entry.lock.release()
    with _local_cache_locks_guard:
        entry.users -= 1
        if entry.users == 0 and _local_cache_locks.get(key) is entry:
            del _local_cache_locks[key]


def _local_cache_lock_count() -> int:
    """Return current per-key lock count for diagnostics and leak assertions."""
    with _local_cache_locks_guard:
        return len(_local_cache_locks)


def cached_market_intelligence_payload(
    key_parts: MarketIntelligenceCacheKeyParts | None,
    compute: Callable[[], Any],
    *,
    is_still_stable: Callable[[], bool] | None = None,
    validate_cached: Callable[[Any], Any] | None = None,
) -> Any:
    """Return a stable cached payload, computing on miss.

    ``None`` key parts mean no valid published pointer exists, so the result is
    computed but never cached. Concurrent misses for one key share one local
    computation; different keys never share a lock.
    """
    if key_parts is None:
        return compute()

    key = build_market_intelligence_cache_key(key_parts)
    try:
        client = get_redis_client()
    except Exception as exc:  # noqa: BLE001 - client creation must remain fail-open.
        logger.warning("Market Intelligence Redis client is unavailable: %s", exc)
        client = None
    if client is not None:
        cached, client_usable = _read_cached_value(
            client,
            key=key,
            validate_cached=validate_cached,
        )
        if cached is not _CACHE_MISS:
            return cached
        if not client_usable:
            client = None

    entry = _acquire_local_key_lock(key)
    try:
        if entry.result_ready:
            if entry.error is not None:
                raise entry.error
            return entry.result

        if client is not None:
            cached, client_usable = _read_cached_value(
                client,
                key=key,
                validate_cached=validate_cached,
            )
            if cached is not _CACHE_MISS:
                entry.result = cached
                entry.result_ready = True
                return cached
            if not client_usable:
                client = None

        value = compute()
        entry.result = value
        entry.result_ready = True
        should_write = client is not None
        if should_write and is_still_stable is not None:
            try:
                should_write = bool(is_still_stable())
            except Exception as exc:  # noqa: BLE001 - recheck only gates cache writes.
                logger.warning(
                    "Market Intelligence pointer recheck failed for %s: %s",
                    key,
                    exc,
                )
                should_write = False
        if should_write:
            try:
                client.setex(key, _cache_ttl_seconds(), _serialize_payload(value))
            except Exception as exc:  # noqa: BLE001 - Redis must remain fail-open.
                logger.warning("Market Intelligence cache write failed for %s: %s", key, exc)
        return value
    except BaseException as exc:
        entry.error = exc
        entry.result_ready = True
        raise
    finally:
        _release_local_key_lock(key, entry)
