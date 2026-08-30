"""Redis and real-worker validation for the Phase 2 sector task."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from celery import Celery
from redis import Redis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.domain.market_intelligence.constants import LATEST_POINTER_KEY, METRIC_VERSION
from app.infra.db.models.feature_store import FeatureRunPointer
from app.infra.db.models.market_intelligence import MarketIntelligenceRunAudit
from app.services import market_intelligence_read_cache
from app.services.market_intelligence_read_cache import (
    MAX_TTL_SECONDS,
    MIN_TTL_SECONDS,
    MarketIntelligenceCacheKeyParts,
    build_market_intelligence_cache_key,
    cached_market_intelligence_payload,
)
from app.tasks.market_intelligence_tasks import (
    calculate_sector_intelligence_snapshot,
)


TASK_NAME = (
    "app.tasks.market_intelligence_tasks."
    "calculate_sector_intelligence_snapshot"
)
BACKEND_DIR = Path(__file__).resolve().parents[3]


def _result_backend_url() -> str:
    value = (os.environ.get("PHASE2_CELERY_RESULT_URL") or "").strip()
    if not value.startswith(("redis://", "rediss://")):
        pytest.fail(
            "PHASE2_CELERY_RESULT_URL must explicitly select a Redis result database"
        )
    return value


def _redis_endpoint(value: str) -> tuple[str | None, int, int]:
    parsed = urlsplit(value)
    database_text = parsed.path.lstrip("/") or "0"
    try:
        database = int(database_text)
    except ValueError:
        pytest.fail("Phase 2 Redis URLs must use a numeric database path")
    return parsed.hostname, parsed.port or 6379, database


def test_market_intelligence_task_is_registered_on_us_market_queue() -> None:
    assert calculate_sector_intelligence_snapshot.name == TASK_NAME
    assert calculate_sector_intelligence_snapshot._get_exec_options()["queue"] == (
        "market_jobs_us"
    )
    assert getattr(calculate_sector_intelligence_snapshot, "autoretry_for", None) is None


@pytest.mark.integration
@pytest.mark.redis_integration
def test_real_redis_connectivity_uses_scoped_round_trip(phase2_redis_url: str) -> None:
    client = Redis.from_url(phase2_redis_url, socket_connect_timeout=3)
    key = f"phase2:market-intelligence:{uuid4().hex}"
    try:
        assert client.ping() is True
        assert client.set(key, "ok", ex=30, nx=True) is True
        assert client.get(key) == b"ok"
    finally:
        client.delete(key)
        client.close()


@pytest.mark.integration
@pytest.mark.redis_integration
def test_real_redis_market_intelligence_read_cache_round_trip_and_ttl(
    phase2_redis_url: str,
    monkeypatch,
) -> None:
    client = Redis.from_url(phase2_redis_url, socket_connect_timeout=3)
    scope = uuid4().hex
    identity = int(scope[:7], 16) + 1
    parts = MarketIntelligenceCacheKeyParts(
        endpoint="integration-contract",
        stable_run_id=identity,
        stable_trading_date=date(2026, 8, 26),
        metric_version=METRIC_VERSION,
        params={"symbol": " xlk ", "limit": 10, "scope": scope},
    )
    key = build_market_intelligence_cache_key(parts)
    calls = 0

    def compute() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"run_id": identity, "source": "postgres"}

    monkeypatch.setattr(
        market_intelligence_read_cache.redis_pool,
        "get_redis_client",
        lambda: client,
    )
    try:
        client.delete(key)
        first = cached_market_intelligence_payload(parts, compute)
        second = cached_market_intelligence_payload(parts, compute)

        assert first == second == {"run_id": identity, "source": "postgres"}
        assert calls == 1
        assert client.get(key) is not None
        assert MIN_TTL_SECONDS <= client.ttl(key) <= MAX_TTL_SECONDS
    finally:
        client.delete(key)
        client.close()


@pytest.mark.integration
@pytest.mark.postgresql_integration
@pytest.mark.redis_integration
@pytest.mark.celery_integration
@pytest.mark.live_provider
@pytest.mark.manual_provider
def test_real_celery_worker_one_shot_and_idempotent_rerun(
    phase2_postgresql_url: str,
    phase2_redis_url: str,
    phase2_celery_enabled: bool,
    phase2_live_provider_enabled: bool,
) -> None:
    assert phase2_celery_enabled and phase2_live_provider_enabled
    calculation_date = (os.environ.get("PHASE2_COMPLETED_SESSION") or "").strip()
    try:
        date.fromisoformat(calculation_date)
    except ValueError:
        pytest.fail("PHASE2_COMPLETED_SESSION must be an explicit ISO date")

    result_backend = _result_backend_url()
    if _redis_endpoint(phase2_redis_url) == _redis_endpoint(result_backend):
        pytest.fail("Phase 2 Celery broker and result backend must use separate Redis DBs")
    identity = uuid4().hex
    worker_name = f"phase2-mi-{identity}@{socket.gethostname()}"
    queue_name = f"phase2_market_intelligence_{identity}"
    worker_environment = os.environ.copy()
    worker_environment.update(
        {
            "DATABASE_URL": phase2_postgresql_url,
            "CELERY_BROKER_URL": phase2_redis_url,
            "CELERY_RESULT_BACKEND": result_backend,
            "ENABLED_MARKETS": "US",
        }
    )
    command = [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "app.celery_app",
        "worker",
        "--pool=solo",
        "--concurrency=1",
        "--loglevel=warning",
        "--without-mingle",
        "--without-gossip",
        "--without-heartbeat",
        "-Q",
        queue_name,
        "-n",
        worker_name,
    ]
    worker = subprocess.Popen(
        command,
        cwd=BACKEND_DIR,
        env=worker_environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    client = Celery("phase2_market_intelligence_client")
    client.conf.update(
        broker_url=phase2_redis_url,
        result_backend=result_backend,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
    )
    submitted_results = []
    try:
        ready = False
        for _ in range(40):
            if worker.poll() is not None:
                pytest.fail(f"Celery worker exited early with code {worker.returncode}")
            if client.control.ping(destination=[worker_name], timeout=1):
                ready = True
                break
            time.sleep(0.5)
        assert ready, "Celery worker did not become ready"

        first_result = client.send_task(
            TASK_NAME,
            args=[calculation_date],
            queue=queue_name,
        )
        submitted_results.append(first_result)
        first = first_result.get(timeout=180)
        second_result = client.send_task(
            TASK_NAME,
            args=[calculation_date],
            queue=queue_name,
        )
        submitted_results.append(second_result)
        second = second_result.get(timeout=180)

        assert first["status"] == "SUCCEEDED"
        assert first["published"] is True
        assert first["metric_version"] == METRIC_VERSION
        assert second["status"] == "SUCCEEDED"
        assert second["run_id"] == first["run_id"]
        assert second["idempotency_key"] == first["idempotency_key"]

        engine = create_engine(phase2_postgresql_url, pool_pre_ping=True)
        factory = sessionmaker(bind=engine)
        try:
            with factory() as session:
                pointer = session.get(FeatureRunPointer, LATEST_POINTER_KEY)
                assert pointer is not None and pointer.run_id == first["run_id"]
                assert (
                    session.query(MarketIntelligenceRunAudit)
                    .filter(
                        MarketIntelligenceRunAudit.idempotency_key
                        == first["idempotency_key"]
                    )
                    .count()
                    == 1
                )
        finally:
            engine.dispose()
    finally:
        for result in submitted_results:
            try:
                result.forget()
            except Exception:
                pass
        worker.terminate()
        try:
            worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait(timeout=10)
        client.close()
