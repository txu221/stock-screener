"""Opt-in fixtures for Phase 2 service and provider validation."""

from __future__ import annotations

import os

import pytest

from tests.integration.market_intelligence.support import (
    enabled_by_environment,
    require_postgresql_url,
)


def _require_opt_in(name: str) -> None:
    if not enabled_by_environment(os.environ.get(name)):
        pytest.skip(f"set {name}=1 to run this opt-in Phase 2 check")


@pytest.fixture(scope="session")
def phase2_postgresql_url() -> str:
    _require_opt_in("RUN_MARKET_INTELLIGENCE_POSTGRES")
    value = os.environ.get("PHASE2_POSTGRES_URL") or os.environ.get("DATABASE_URL")
    return require_postgresql_url(value)


@pytest.fixture(scope="session")
def phase2_redis_url() -> str:
    _require_opt_in("RUN_MARKET_INTELLIGENCE_REDIS")
    value = (os.environ.get("PHASE2_REDIS_URL") or "").strip()
    if not value.startswith(("redis://", "rediss://")):
        pytest.fail("PHASE2_REDIS_URL must be an explicit Redis URL")
    return value


@pytest.fixture(scope="session")
def phase2_celery_enabled() -> bool:
    _require_opt_in("RUN_MARKET_INTELLIGENCE_CELERY")
    return True


@pytest.fixture(scope="session")
def phase2_live_provider_enabled() -> bool:
    _require_opt_in("RUN_MARKET_INTELLIGENCE_LIVE")
    return True
