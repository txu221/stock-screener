"""Opt-in fixtures for Phase 2 service and provider validation."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from tests.integration.market_intelligence.support import (
    enabled_by_environment,
    explicitly_enabled,
    require_postgresql_url,
)


def _require_opt_in(name: str) -> None:
    if not enabled_by_environment(os.environ.get(name)):
        pytest.skip(f"set {name}=1 to run this opt-in Phase 2 check")


@pytest.fixture(scope="session")
def phase2_postgresql_url() -> str:
    _require_opt_in("RUN_MARKET_INTELLIGENCE_POSTGRES")
    if not explicitly_enabled(
        os.environ.get("PHASE2_ALLOW_DESTRUCTIVE_POSTGRES_TESTS")
    ):
        pytest.skip(
            "set PHASE2_ALLOW_DESTRUCTIVE_POSTGRES_TESTS=1 for a disposable database"
        )
    value = os.environ.get("PHASE2_POSTGRES_URL")
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


@pytest.fixture
def phase2_postgresql_engine(phase2_postgresql_url: str):
    """Yield a real PostgreSQL engine isolated to a generated test schema."""

    schema = f"mi_phase2_{uuid4().hex}"
    admin_engine = create_engine(phase2_postgresql_url, pool_pre_ping=True)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_engine(
        phase2_postgresql_url,
        connect_args={"options": f"-csearch_path={schema}"},
        pool_pre_ping=True,
    )
    try:
        if engine.dialect.name != "postgresql":
            pytest.fail("Phase 2 PostgreSQL fixture connected to a non-PostgreSQL engine")
        yield engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()
