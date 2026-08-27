"""Real PostgreSQL transaction and concurrent-publication validation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from threading import Barrier, Event

import pytest
from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from app.domain.feature_store.models import RunStats, RunType
from app.domain.market_intelligence.constants import (
    LATEST_POINTER_KEY,
    METRIC_VERSION,
    NORMALIZATION_VERSION,
    PIPELINE_NAME,
    PRICE_BASIS,
    UNIVERSE_HASH,
)
from app.domain.market_intelligence.models import IngestionStatus, RunAudit
from app.domain.market_intelligence.ports import MarketIntelligenceIdempotencyConflict
from app.infra.db.models.feature_store import FeatureRun, FeatureRunPointer
from app.infra.db.models.market_intelligence import MarketIntelligenceRunAudit
from app.infra.db.uow import SqlUnitOfWork


pytestmark = [
    pytest.mark.integration,
    pytest.mark.postgresql_integration,
]

NOW = datetime(2026, 8, 27, 21, 0, tzinfo=timezone.utc)


def _create_tables(engine) -> None:
    tables = [
        FeatureRun.__table__,
        FeatureRunPointer.__table__,
        MarketIntelligenceRunAudit.__table__,
    ]
    FeatureRun.metadata.create_all(engine, tables=tables)


def _audit(key: str, as_of: date) -> RunAudit:
    return RunAudit(
        idempotency_key=key,
        input_hash=key[::-1],
        ingestion_status=IngestionStatus.SUCCEEDED,
        provider="yahoo",
        provider_status="AVAILABLE",
        request_failure=None,
        metric_version=METRIC_VERSION,
        normalization_version=NORMALIZATION_VERSION,
        price_basis=PRICE_BASIS,
        counters={"expected_symbols": 12, "usable_symbols": 12},
        missing_symbols=(),
        provider_failures=(),
        target_session=as_of,
        provider_response_at=NOW,
        source_freshness={"status": "FRESH", "as_of": as_of.isoformat()},
        calculation_timestamp=NOW,
        ingestion_timestamp=NOW,
    )


def _create_candidate(factory, as_of: date, key: str) -> int:
    with SqlUnitOfWork(factory) as uow:
        run = uow.feature_runs.start_run(
            as_of_date=as_of,
            run_type=RunType.DAILY_SNAPSHOT,
            universe_hash=UNIVERSE_HASH,
            input_hash=key[::-1],
            config_json={"pipeline": PIPELINE_NAME},
        )
        uow.market_intelligence.persist_candidate(
            run.id, _audit(key, as_of), (), (), ()
        )
        uow.feature_runs.mark_completed(run.id, RunStats(12, 12, 0, 0.1, 12))
        uow.commit()
        return run.id


def _publish(factory, run_id: int, barrier: Barrier | None = None) -> int:
    with SqlUnitOfWork(factory) as uow:
        if barrier is not None:
            barrier.wait(timeout=10)
        uow.feature_runs.publish_atomically_if_not_older(
            run_id, LATEST_POINTER_KEY
        )
        uow.commit()
    return run_id


@pytest.fixture
def pg_factory(phase2_postgresql_engine):
    _create_tables(phase2_postgresql_engine)
    return sessionmaker(bind=phase2_postgresql_engine, expire_on_commit=False)


def _pointer(factory) -> int | None:
    with factory() as session:
        pointer = session.get(FeatureRunPointer, LATEST_POINTER_KEY)
        return None if pointer is None else pointer.run_id


def test_exception_before_pointer_update_rolls_back_candidate(pg_factory) -> None:
    with pytest.raises(RuntimeError, match="before pointer"):
        with SqlUnitOfWork(pg_factory) as uow:
            run = uow.feature_runs.start_run(
                as_of_date=date(2026, 8, 26),
                run_type=RunType.DAILY_SNAPSHOT,
                universe_hash=UNIVERSE_HASH,
                input_hash="a" * 64,
                config_json={"pipeline": PIPELINE_NAME},
            )
            uow.market_intelligence.persist_candidate(
                run.id, _audit("a" * 64, date(2026, 8, 26)), (), (), ()
            )
            raise RuntimeError("before pointer")

    with pg_factory() as session:
        assert session.query(func.count(FeatureRun.id)).scalar() == 0
        assert session.query(func.count(MarketIntelligenceRunAudit.run_id)).scalar() == 0
        assert session.query(func.count(FeatureRunPointer.key)).scalar() == 0


def test_exception_after_pointer_update_rolls_back_pointer_and_candidate(pg_factory) -> None:
    old_id = _create_candidate(pg_factory, date(2026, 8, 25), "1" * 64)
    _publish(pg_factory, old_id)

    with pytest.raises(RuntimeError, match="after pointer"):
        with SqlUnitOfWork(pg_factory) as uow:
            run = uow.feature_runs.start_run(
                as_of_date=date(2026, 8, 26),
                run_type=RunType.DAILY_SNAPSHOT,
                universe_hash=UNIVERSE_HASH,
                input_hash="2" * 64,
                config_json={"pipeline": PIPELINE_NAME},
            )
            uow.market_intelligence.persist_candidate(
                run.id, _audit("2" * 64, date(2026, 8, 26)), (), (), ()
            )
            uow.feature_runs.mark_completed(run.id, RunStats(12, 12, 0, 0.1, 12))
            uow.feature_runs.publish_atomically_if_not_older(
                run.id, LATEST_POINTER_KEY
            )
            raise RuntimeError("after pointer")

    assert _pointer(pg_factory) == old_id
    with pg_factory() as session:
        assert session.query(func.count(FeatureRun.id)).scalar() == 1
        assert session.query(func.count(MarketIntelligenceRunAudit.run_id)).scalar() == 1


def test_concurrent_case_a_same_day_history_winner_matches_pointer(pg_factory) -> None:
    as_of = date(2026, 8, 26)
    first = _create_candidate(pg_factory, as_of, "1" * 64)
    second = _create_candidate(pg_factory, as_of, "2" * 64)
    barrier = Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(
            future.result(timeout=20)
            for future in (
                pool.submit(_publish, pg_factory, first, barrier),
                pool.submit(_publish, pg_factory, second, barrier),
            )
        )

    assert set(results) == {first, second}
    with pg_factory() as session:
        winner = (
            session.query(FeatureRun.id)
            .filter(FeatureRun.as_of_date == as_of)
            .order_by(FeatureRun.published_at.desc(), FeatureRun.id.desc())
            .limit(1)
            .scalar()
        )
    assert _pointer(pg_factory) == winner


def test_concurrent_case_b_old_backfill_cannot_replace_newer_date(pg_factory) -> None:
    old = _create_candidate(pg_factory, date(2026, 8, 25), "3" * 64)
    new = _create_candidate(pg_factory, date(2026, 8, 26), "4" * 64)
    barrier = Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (
            pool.submit(_publish, pg_factory, old, barrier),
            pool.submit(_publish, pg_factory, new, barrier),
        )
        for future in futures:
            future.result(timeout=20)

    assert _pointer(pg_factory) == new


def test_concurrent_case_c_new_starts_first_old_commits_first(pg_factory) -> None:
    old = _create_candidate(pg_factory, date(2026, 8, 25), "5" * 64)
    new = _create_candidate(pg_factory, date(2026, 8, 26), "6" * 64)
    new_started = Event()
    old_committed = Event()

    def publish_new() -> None:
        with SqlUnitOfWork(pg_factory) as uow:
            uow.feature_runs.get_run(new)
            new_started.set()
            assert old_committed.wait(timeout=10)
            uow.feature_runs.publish_atomically_if_not_older(
                new, LATEST_POINTER_KEY
            )
            uow.commit()

    def publish_old() -> None:
        assert new_started.wait(timeout=10)
        _publish(pg_factory, old)
        old_committed.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (pool.submit(publish_new), pool.submit(publish_old))
        for future in futures:
            future.result(timeout=20)

    assert _pointer(pg_factory) == new


def test_same_day_revision_advances_pointer_and_old_backfill_stays_historical(
    pg_factory,
) -> None:
    day = date(2026, 8, 26)
    first = _create_candidate(pg_factory, day, "7" * 64)
    _publish(pg_factory, first)
    revision = _create_candidate(pg_factory, day, "8" * 64)
    _publish(pg_factory, revision)
    backfill = _create_candidate(pg_factory, date(2026, 8, 25), "9" * 64)
    _publish(pg_factory, backfill)

    assert _pointer(pg_factory) == revision
    with pg_factory() as session:
        assert session.get(FeatureRun, backfill).status == "published"


def test_concurrent_idempotency_key_has_one_committed_winner(pg_factory) -> None:
    first = _create_candidate(pg_factory, date(2026, 8, 25), "a" * 64)
    second = _create_candidate(pg_factory, date(2026, 8, 26), "b" * 64)
    with pg_factory.begin() as session:
        session.query(MarketIntelligenceRunAudit).delete()

    barrier = Barrier(2)

    def persist(run_id: int) -> str:
        try:
            with SqlUnitOfWork(pg_factory) as uow:
                barrier.wait(timeout=10)
                uow.market_intelligence.persist_candidate(
                    run_id,
                    _audit("c" * 64, date(2026, 8, 26)),
                    (), (), (),
                )
                uow.commit()
            return "committed"
        except MarketIntelligenceIdempotencyConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (pool.submit(persist, first), pool.submit(persist, second))
        outcomes = {future.result(timeout=20) for future in futures}

    assert outcomes == {"committed", "conflict"}
    with pg_factory() as session:
        assert session.query(func.count(MarketIntelligenceRunAudit.run_id)).scalar() == 1
