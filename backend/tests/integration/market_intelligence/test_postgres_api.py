"""PostgreSQL-backed API validation for the existing Market Intelligence router."""

from __future__ import annotations

from datetime import date, datetime, timezone

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from app.api.v1.market_intelligence import router
from app.database import get_db
from app.domain.market_intelligence.constants import METRIC_VERSION
from app.domain.market_intelligence.models import IngestionStatus
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
from tests.integration.market_intelligence.test_postgres_publication import (
    _create_tables,
)


pytestmark = [pytest.mark.integration, pytest.mark.postgresql_integration]


@pytest.mark.asyncio
async def test_postgresql_backed_latest_history_health_match_production_state(
    phase2_postgresql_engine,
) -> None:
    _create_tables(phase2_postgresql_engine)
    factory = sessionmaker(bind=phase2_postgresql_engine, expire_on_commit=False)
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
        uow_factory=lambda: SqlUnitOfWork(factory),
        clock=lambda: datetime(2026, 8, 27, 1, 0, tzinfo=timezone.utc),
        attempt_id_factory=lambda: "postgres-api-failed-attempt",
    )

    succeeded = runner.execute(BuildSectorSnapshotCommand(succeeded_date))
    partial = runner.execute(BuildSectorSnapshotCommand(partial_date))
    failed = runner.execute(BuildSectorSnapshotCommand(failed_date))

    assert succeeded.ingestion_status is IngestionStatus.SUCCEEDED
    assert partial.ingestion_status is IngestionStatus.PARTIAL
    assert failed.ingestion_status is IngestionStatus.FAILED

    api_app = FastAPI()
    api_app.include_router(router, prefix="/api/v1/market-intelligence")

    def override_get_db():
        with factory() as session:
            yield session

    api_app.dependency_overrides[get_db] = override_get_db
    try:
        transport = httpx.ASGITransport(app=api_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://phase2-postgres.test",
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
    finally:
        api_app.dependency_overrides.clear()

    assert latest_response.status_code == 200
    latest = latest_response.json()
    assert latest["run_id"] == succeeded.run_id
    assert latest["metric_version"] == METRIC_VERSION
    assert latest["benchmark"]["symbol"] == "SPY"
    assert len(latest["sectors"]) == 11

    assert history_response.status_code == 200
    history = history_response.json()
    assert history["metric_version"] == METRIC_VERSION
    assert [item["run_id"] for item in history["items"]] == [succeeded.run_id]

    assert health_response.status_code == 200
    health = health_response.json()
    assert health["latest_attempt"]["run_id"] == failed.run_id
    assert health["latest_attempt"]["status"] == "FAILED"
    assert health["latest_published"]["run_id"] == succeeded.run_id
    assert health["publication_occurred"] is False
