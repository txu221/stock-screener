"""Celery entrypoint for the US sector-intelligence daily slice."""

from __future__ import annotations

from datetime import date

from app.celery_app import celery_app
from app.domain.market_intelligence.constants import METRIC_VERSION
from app.tasks.market_queues import market_jobs_queue_for_market
from app.use_cases.market_intelligence.build_sector_snapshot import (
    BuildSectorSnapshotCommand,
)
from app.wiring.bootstrap import (
    get_market_calendar_service,
    get_market_intelligence_runner,
)


@celery_app.task(
    name=(
        "app.tasks.market_intelligence_tasks."
        "calculate_sector_intelligence_snapshot"
    ),
    queue=market_jobs_queue_for_market("US"),
)
def calculate_sector_intelligence_snapshot(
    calculation_date: str | None = None,
) -> dict:
    as_of = (
        date.fromisoformat(calculation_date)
        if calculation_date is not None
        else get_market_calendar_service().last_completed_trading_day("US")
    )
    result = get_market_intelligence_runner().execute(
        BuildSectorSnapshotCommand(as_of=as_of)
    )
    return {
        "status": result.ingestion_status.value,
        "run_id": result.run_id,
        "published": result.published,
        "as_of": as_of.isoformat(),
        "metric_version": METRIC_VERSION,
        "idempotency_key": result.idempotency_key,
    }
