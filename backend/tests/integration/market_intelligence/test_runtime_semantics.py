"""End-to-end controlled state semantics through the production use case/UoW."""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.database import SessionLocal
from app.domain.market_intelligence.constants import LATEST_POINTER_KEY
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


def test_controlled_succeeded_partial_failed_attempts_preserve_latest_and_health_truth() -> None:
    final = date(2026, 8, 26)
    sessions = weekday_sessions(final, 93)
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
        attempt_id_factory=lambda: "controlled-failed-attempt",
    )

    succeeded = runner.execute(BuildSectorSnapshotCommand(succeeded_date))
    partial = runner.execute(BuildSectorSnapshotCommand(partial_date))
    failed = runner.execute(BuildSectorSnapshotCommand(failed_date))

    assert succeeded.ingestion_status is IngestionStatus.SUCCEEDED
    assert succeeded.published is True
    assert partial.ingestion_status is IngestionStatus.PARTIAL
    assert partial.published is False
    assert failed.ingestion_status is IngestionStatus.FAILED
    assert failed.published is False

    with SqlUnitOfWork(SessionLocal) as uow:
        latest = uow.market_intelligence.get_latest_published(LATEST_POINTER_KEY)
        latest_attempt = uow.market_intelligence.get_latest_attempt()
        partial_bundle = uow.market_intelligence.find_exact(partial.idempotency_key)
        failed_bundle = uow.market_intelligence.find_exact(failed.idempotency_key)

    assert latest is not None and latest.run_id == succeeded.run_id
    assert latest_attempt is not None and latest_attempt.run_id == failed.run_id
    assert partial_bundle is not None
    assert partial_bundle.lifecycle_status == "quarantined"
    assert partial_bundle.audit.counters["missing_symbols"] == 1
    assert partial_bundle.audit.counters["rejected_bars"] == 0
    assert failed_bundle is not None
    assert failed_bundle.lifecycle_status == "failed"
    assert failed_bundle.audit.request_failure is not None
    assert failed_bundle.audit.counters["rejected_bars"] == 0
    assert failed_bundle.rejections == ()
