from __future__ import annotations

import json
import threading
import time
from datetime import date

import pytest


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.get_error: Exception | None = None
        self.set_error: Exception | None = None
        self.setex_calls: list[tuple[str, int, str]] = []
        self._guard = threading.Lock()

    def get(self, key: str) -> bytes | None:
        if self.get_error is not None:
            raise self.get_error
        with self._guard:
            return self.values.get(key)

    def setex(self, key: str, ttl_seconds: int, value: str) -> bool:
        if self.set_error is not None:
            raise self.set_error
        with self._guard:
            self.setex_calls.append((key, ttl_seconds, value))
            self.values[key] = value.encode("utf-8")
        return True


@pytest.fixture
def cache_module(monkeypatch):
    from app.services import market_intelligence_read_cache as module

    redis = FakeRedis()
    monkeypatch.setattr(module.redis_pool, "get_redis_client", lambda: redis)
    assert module._local_cache_lock_count() == 0
    yield module, redis
    assert module._local_cache_lock_count() == 0


def _parts(module, *, run_id: int = 101, endpoint: str = "movers", params=None):
    return module.MarketIntelligenceCacheKeyParts(
        endpoint=endpoint,
        stable_run_id=run_id,
        stable_trading_date=date(2026, 8, 26),
        metric_version="market_intelligence_v1",
        params=params or {},
    )


def test_key_contains_stable_identity_and_normalizes_request_parameters(cache_module):
    module, _ = cache_module
    first = _parts(
        module,
        params={
            "sector": "  technology ",
            "direction": "gainers",
            "limit": 20,
            "min_rvol": None,
            "min_price": 5.0,
            "zero": -0.0,
        },
    )
    second = _parts(
        module,
        params={
            "min_price": 5,
            "zero": 0,
            "limit": 20,
            "direction": "gainers",
            "sector": "technology",
        },
    )

    first_key = module.build_market_intelligence_cache_key(first)
    second_key = module.build_market_intelligence_cache_key(second)

    assert first_key == second_key
    assert first_key.startswith("market-intelligence:read:v1:movers:")
    assert ":run:101:" in first_key
    assert ":date:2026-08-26:" in first_key
    assert ":metric:market_intelligence_v1:" in first_key
    assert ":params:" in first_key


def test_endpoint_identity_and_parameters_create_distinct_keys(cache_module):
    module, _ = cache_module

    base = module.build_market_intelligence_cache_key(
        _parts(module, endpoint="movers", params={"limit": 20})
    )
    endpoint_changed = module.build_market_intelligence_cache_key(
        _parts(module, endpoint="etfs", params={"limit": 20})
    )
    params_changed = module.build_market_intelligence_cache_key(
        _parts(module, endpoint="movers", params={"limit": 50})
    )

    assert len({base, endpoint_changed, params_changed}) == 3


def test_a_success_b_partial_c_failed_d_success_cache_transition(cache_module):
    module, redis = cache_module
    stable_a = _parts(module, run_id=101)
    compute_calls: list[str] = []

    def compute(value: str):
        return lambda: compute_calls.append(value) or {"publication": value}

    assert module.cached_market_intelligence_payload(stable_a, compute("A")) == {
        "publication": "A"
    }
    # B (partial) and C (failed) do not move the stable pointer. Their request
    # paths therefore retain A's identity and cannot replace its cached value.
    assert module.cached_market_intelligence_payload(stable_a, compute("B")) == {
        "publication": "A"
    }
    assert module.cached_market_intelligence_payload(stable_a, compute("C")) == {
        "publication": "A"
    }

    stable_d = module.MarketIntelligenceCacheKeyParts(
        endpoint="movers",
        stable_run_id=104,
        stable_trading_date=date(2026, 8, 29),
        metric_version="market_intelligence_v1",
        params={},
    )
    assert module.cached_market_intelligence_payload(stable_d, compute("D")) == {
        "publication": "D"
    }

    assert compute_calls == ["A", "D"]
    assert len(redis.values) == 2


def test_missing_stable_pointer_bypasses_redis_and_never_caches(cache_module):
    module, redis = cache_module

    result = module.cached_market_intelligence_payload(
        None,
        lambda: {"unavailable_reason": "no_published_run"},
    )

    assert result == {"unavailable_reason": "no_published_run"}
    assert redis.values == {}
    assert redis.setex_calls == []


def test_malformed_cached_json_falls_back_to_compute_and_repairs_entry(cache_module):
    module, redis = cache_module
    parts = _parts(module)
    key = module.build_market_intelligence_cache_key(parts)
    redis.values[key] = b"{not-json"

    result = module.cached_market_intelligence_payload(parts, lambda: {"ok": True})

    assert result == {"ok": True}
    assert json.loads(redis.values[key]) == {"ok": True}


@pytest.mark.parametrize(
    "cached_json",
    (b"null", b"7", b'{"legacy":true}'),
)
def test_schema_invalid_cached_json_falls_back_and_repairs_entry(
    cache_module,
    cached_json,
):
    module, redis = cache_module
    parts = _parts(module)
    key = module.build_market_intelligence_cache_key(parts)
    redis.values[key] = cached_json

    def validate(value):
        if not isinstance(value, dict) or value.get("ok") is not True:
            raise ValueError("incompatible response schema")
        return value

    result = module.cached_market_intelligence_payload(
        parts,
        lambda: {"ok": True},
        validate_cached=validate,
    )

    assert result == {"ok": True}
    assert json.loads(redis.values[key]) == {"ok": True}


def test_redis_get_failure_falls_back_without_raising(cache_module):
    module, redis = cache_module
    redis.get_error = ConnectionError("redis unavailable")

    result = module.cached_market_intelligence_payload(
        _parts(module),
        lambda: {"source": "postgres"},
    )

    assert result == {"source": "postgres"}


def test_redis_client_creation_failure_falls_back_without_raising(
    cache_module,
    monkeypatch,
):
    module, _ = cache_module

    def fail_to_create_client():
        raise ConnectionError("pool unavailable")

    monkeypatch.setattr(module.redis_pool, "get_redis_client", fail_to_create_client)

    result = module.cached_market_intelligence_payload(
        _parts(module),
        lambda: {"source": "postgres"},
    )

    assert result == {"source": "postgres"}


def test_redis_set_failure_returns_computed_payload_without_raising(cache_module):
    module, redis = cache_module
    redis.set_error = ConnectionError("redis unavailable")

    result = module.cached_market_intelligence_payload(
        _parts(module),
        lambda: {"source": "postgres"},
    )

    assert result == {"source": "postgres"}
    assert redis.values == {}


def test_cache_write_uses_positive_bounded_configured_ttl(cache_module, monkeypatch):
    module, redis = cache_module
    for run_id, configured in enumerate((0, 3600, 10**9), start=1):
        monkeypatch.setattr(module.settings, "cache_ttl_seconds", configured)
        module.cached_market_intelligence_payload(
            _parts(module, run_id=run_id),
            lambda: {"ok": True},
        )

    assert [call[1] for call in redis.setex_calls] == [
        module.MIN_TTL_SECONDS,
        3600,
        module.MAX_TTL_SECONDS,
    ]


def test_pointer_change_during_compute_prevents_old_generation_write(cache_module):
    module, redis = cache_module

    result = module.cached_market_intelligence_payload(
        _parts(module, run_id=101),
        lambda: {"publication": "D"},
        is_still_stable=lambda: False,
    )

    assert result == {"publication": "D"}
    assert redis.values == {}
    assert redis.setex_calls == []


def test_concurrent_same_key_miss_computes_once(cache_module):
    module, _ = cache_module
    parts = _parts(module)
    barrier = threading.Barrier(8)
    compute_started = threading.Event()
    release_compute = threading.Event()
    calls = 0
    calls_guard = threading.Lock()
    results: list[dict[str, int]] = []

    def compute() -> dict[str, int]:
        nonlocal calls
        with calls_guard:
            calls += 1
        compute_started.set()
        assert release_compute.wait(timeout=2)
        return {"value": 7}

    def worker() -> None:
        barrier.wait(timeout=2)
        results.append(module.cached_market_intelligence_payload(parts, compute))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    assert compute_started.wait(timeout=2)
    time.sleep(0.05)
    release_compute.set()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert calls == 1
    assert results == [{"value": 7}] * 8
    assert module._local_cache_lock_count() == 0


def test_different_keys_do_not_serialize_computation(cache_module):
    module, _ = cache_module
    both_started = threading.Barrier(3)
    release = threading.Event()
    results: list[int] = []

    def worker(run_id: int) -> None:
        def compute() -> int:
            both_started.wait(timeout=2)
            assert release.wait(timeout=2)
            return run_id

        results.append(
            module.cached_market_intelligence_payload(
                _parts(module, run_id=run_id),
                compute,
            )
        )

    threads = [threading.Thread(target=worker, args=(run_id,)) for run_id in (1, 2)]
    for thread in threads:
        thread.start()
    both_started.wait(timeout=2)
    release.set()
    for thread in threads:
        thread.join(timeout=2)

    assert sorted(results) == [1, 2]
    assert module._local_cache_lock_count() == 0


def test_compute_exception_releases_keyed_lock_for_retry(cache_module):
    module, _ = cache_module
    parts = _parts(module)

    with pytest.raises(RuntimeError, match="database failed"):
        module.cached_market_intelligence_payload(
            parts,
            lambda: (_ for _ in ()).throw(RuntimeError("database failed")),
        )

    assert module._local_cache_lock_count() == 0
    assert module.cached_market_intelligence_payload(parts, lambda: {"ok": True}) == {
        "ok": True
    }
    assert module._local_cache_lock_count() == 0
