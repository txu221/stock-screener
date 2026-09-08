"""Chronological five-session replay with no future leakage."""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.database import SessionLocal
from app.domain.market_intelligence.constants import LATEST_POINTER_KEY, METRIC_VERSION
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


def test_five_session_replay_is_chronological_rank_continuous_and_idempotent() -> None:
    sessions = weekday_sessions(date(2026, 8, 26), 95)
    replay_dates = sessions[-5:]
    provider = ScenarioProvider(scenario_rows(sessions))
    runner = BuildSectorSnapshotUseCase(
        provider=provider,
        session_source=ScenarioSessionSource(sessions),
        uow_factory=lambda: SqlUnitOfWork(SessionLocal),
        clock=lambda: datetime(2026, 8, 27, 1, 0, tzinfo=timezone.utc),
    )

    results = [
        runner.execute(BuildSectorSnapshotCommand(as_of))
        for as_of in replay_dates
    ]
    rerun = runner.execute(BuildSectorSnapshotCommand(replay_dates[-1]))

    assert all(result.published for result in results)
    assert rerun.run_id == results[-1].run_id
    assert rerun.idempotency_key == results[-1].idempotency_key

    bundles = []
    with SqlUnitOfWork(SessionLocal) as uow:
        for result, as_of in zip(results, replay_dates):
            bundle = uow.market_intelligence.find_exact(result.idempotency_key)
            assert bundle is not None
            assert bundle.as_of_date == as_of
            assert max(bar.trading_date for bar in bundle.canonical_bars) <= as_of
            assert bundle.audit.metric_version == METRIC_VERSION
            bundles.append(bundle)
        latest = uow.market_intelligence.get_latest_published(LATEST_POINTER_KEY)
        history = uow.market_intelligence.list_published_history(
            metric_version=METRIC_VERSION,
            limit=10,
        )

    assert latest is not None and latest.run_id == results[-1].run_id
    assert [bundle.as_of_date for bundle in history] == list(reversed(replay_dates))
    for index, bundle in enumerate(bundles):
        sector_snapshots = [
            snapshot for snapshot in bundle.snapshots if snapshot.symbol != "SPY"
        ]
        rank_records = [
            rank
            for snapshot in sector_snapshots
            for rank in snapshot.ranks.values()
        ]
        if index == 0:
            assert all(rank.previous_rank is None for rank in rank_records)
        else:
            previous_snapshots = {
                snapshot.symbol: snapshot for snapshot in bundles[index - 1].snapshots
            }
            for snapshot in sector_snapshots:
                previous = previous_snapshots[snapshot.symbol]
                for metric_name, rank in snapshot.ranks.items():
                    expected_previous = previous.ranks[metric_name].current_rank
                    assert rank.previous_rank == expected_previous
                    assert rank.rank_change == expected_previous - rank.current_rank

    assert [request[1] for request in provider.requests] == [*replay_dates, replay_dates[-1]]
