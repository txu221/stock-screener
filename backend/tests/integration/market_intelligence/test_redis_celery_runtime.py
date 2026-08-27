"""Redis and real-worker validation for the Phase 2 sector task."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from celery import Celery
from redis import Redis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.domain.market_intelligence.constants import LATEST_POINTER_KEY, METRIC_VERSION
from app.infra.db.models.feature_store import FeatureRunPointer
from app.infra.db.models.market_intelligence import MarketIntelligenceRunAudit
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
    worker_name = f"phase2-mi-{uuid4().hex}@%h"
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
        "market_jobs_us",
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
    try:
        ready = False
        for _ in range(40):
            if worker.poll() is not None:
                pytest.fail(f"Celery worker exited early with code {worker.returncode}")
            if client.control.ping(timeout=1):
                ready = True
                break
            time.sleep(0.5)
        assert ready, "Celery worker did not become ready"

        first = client.send_task(
            TASK_NAME,
            args=[calculation_date],
            queue="market_jobs_us",
        ).get(timeout=180)
        second = client.send_task(
            TASK_NAME,
            args=[calculation_date],
            queue="market_jobs_us",
        ).get(timeout=180)

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
        worker.terminate()
        try:
            worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait(timeout=10)
        client.close()
