"""API read contracts over controlled production-use-case attempts."""

from __future__ import annotations

from datetime import date, datetime, timezone

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.market_intelligence import router
from app.database import SessionLocal
from app.domain.market_intelligence.constants import METRIC_VERSION, SECTOR_SYMBOLS
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


api_app = FastAPI()
api_app.include_router(router, prefix="/api/v1/market-intelligence")


@pytest.mark.asyncio
async def test_latest_history_health_match_committed_attempts() -> None:
    sessions = weekday_sessions(date(2026, 8, 26), 93)
    succeeded_date, partial_date, failed_date = sessions[-3:]
    provider = ScenarioProvider(
        scenario_rows(sessions),
        missing_by_date={partial_date: "XLU"},
        failed_dates={failed_date},
    )
    runner = BuildSectorSnapshotUseCase(
        provider=provider,
        session_source=ScenarioSessionSource(sessions),
        uow_factory=lambda: SqlUnitOfWork(SessionLocal),
        clock=lambda: datetime(2026, 8, 27, 1, 0, tzinfo=timezone.utc),
        attempt_id_factory=lambda: "api-failed-attempt",
    )
    succeeded = runner.execute(BuildSectorSnapshotCommand(succeeded_date))
    runner.execute(BuildSectorSnapshotCommand(partial_date))
    failed = runner.execute(BuildSectorSnapshotCommand(failed_date))

    transport = httpx.ASGITransport(app=api_app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://phase2.test",
    ) as client:
        latest_response = await client.get(
            "/api/v1/market-intelligence/sectors/latest"
        )
        history_response = await client.get(
            "/api/v1/market-intelligence/sectors/history"
        )
        health_response = await client.get(
            "/api/v1/market-intelligence/sectors/health"
        )

    assert latest_response.status_code == 200
    latest = latest_response.json()
    assert latest["run_id"] == succeeded.run_id
    assert latest["as_of"] == succeeded_date.isoformat()
    assert latest["metric_version"] == METRIC_VERSION
    assert latest["benchmark"]["symbol"] == "SPY"
    assert [item["symbol"] for item in latest["sectors"]] == list(SECTOR_SYMBOLS)
    assert len(latest["sectors"]) == 11

    assert history_response.status_code == 200
    history = history_response.json()
    assert [item["run_id"] for item in history["items"]] == [succeeded.run_id]
    assert history["metric_version"] == METRIC_VERSION

    assert health_response.status_code == 200
    health = health_response.json()
    assert health["latest_attempt"]["run_id"] == failed.run_id
    assert health["latest_attempt"]["status"] == "FAILED"
    assert health["latest_attempt"]["counters"]["rejected_bars"] == 0
    assert health["latest_published"]["run_id"] == succeeded.run_id
    assert health["publication_occurred"] is False
