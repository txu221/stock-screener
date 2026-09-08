"""Diagnostic-only Phase 2 performance observations (no SLO assertions)."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from time import perf_counter

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.market_intelligence import router
from app.database import SessionLocal
from app.infra.db.uow import SqlUnitOfWork
from app.use_cases.market_intelligence.build_sector_snapshot import (
    BuildSectorSnapshotCommand,
    BuildSectorSnapshotUseCase,
)
from tests.integration.market_intelligence.scenario import (
    ScenarioProvider,
    ScenarioSessionSource,
    scenario_rows,
    weekday_sessions,
)


@pytest.mark.performance
@pytest.mark.asyncio
async def test_fixed_universe_diagnostic_performance_baseline() -> None:
    sessions = weekday_sessions(date(2026, 8, 26), 95)
    provider = ScenarioProvider(scenario_rows(sessions))
    runner = BuildSectorSnapshotUseCase(
        provider=provider,
        session_source=ScenarioSessionSource(sessions),
        uow_factory=lambda: SqlUnitOfWork(SessionLocal),
        clock=lambda: datetime(2026, 8, 27, 1, 0, tzinfo=timezone.utc),
    )

    run_started = perf_counter()
    result = runner.execute(BuildSectorSnapshotCommand(sessions[-1]))
    total_run = perf_counter() - run_started
    assert result.published is True

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/market-intelligence")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://perf.test") as client:
        latest_started = perf_counter()
        latest = await client.get("/api/v1/market-intelligence/sectors/latest")
        latest_latency = perf_counter() - latest_started
        history_started = perf_counter()
        history = await client.get("/api/v1/market-intelligence/sectors/history")
        history_latency = perf_counter() - history_started

    assert latest.status_code == 200
    assert history.status_code == 200
    observations = {
        "environment": "sqlite_test_harness_not_postgresql",
        "symbols": 12,
        "input_bars": 12 * 95,
        "total_run_seconds": total_run,
        "latest_api_seconds": latest_latency,
        "history_api_seconds": history_latency,
    }
    assert all(
        value >= 0
        for name, value in observations.items()
        if name.endswith("_seconds")
    )
    print("PHASE2_PERFORMANCE_BASELINE " + json.dumps(observations, sort_keys=True))
