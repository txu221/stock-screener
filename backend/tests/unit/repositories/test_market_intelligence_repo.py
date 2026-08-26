"""Repository contract for persisted Phase 1 sector intelligence."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
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
from app.domain.market_intelligence.models import (
    BarRejection,
    CanonicalBar,
    IngestionStatus,
    ProviderSymbolFailure,
    RankDirection,
    RankRecord,
    RejectionCode,
    RequestFailure,
    RunAudit,
    SectorMetrics,
    SectorSnapshot,
)
from app.domain.market_intelligence.ports import (
    MarketIntelligenceIdempotencyConflict,
)
from app.infra.db.models.feature_store import FeatureRunPointer
from app.infra.db.models.market_intelligence import (
    MarketIntelligenceCanonicalBar,
    MarketIntelligenceRejection,
    MarketIntelligenceRunAudit,
    MarketIntelligenceSectorSnapshot,
)
from app.infra.db.uow import SqlUnitOfWork

AS_OF = date(2026, 5, 15)
NOW = datetime(2026, 5, 15, 21, 10, tzinfo=timezone.utc)


def _audit(
    *,
    key: str = "a" * 64,
    status: IngestionStatus = IngestionStatus.SUCCEEDED,
    target_session: date = AS_OF,
) -> RunAudit:
    return RunAudit(
        idempotency_key=key,
        input_hash="b" * 64,
        ingestion_status=status,
        provider="yahoo",
        provider_status="AVAILABLE",
        request_failure=None,
        metric_version=METRIC_VERSION,
        normalization_version=NORMALIZATION_VERSION,
        price_basis=PRICE_BASIS,
        counters={"expected_symbols": 12, "valid_bars": 1092},
        missing_symbols=(),
        provider_failures=(),
        target_session=target_session,
        provider_response_at=NOW,
        source_freshness={"status": "FRESH", "as_of": target_session.isoformat()},
        calculation_timestamp=NOW,
        ingestion_timestamp=NOW,
    )


def _bar(*, symbol: str = "SPY", trading_date: date = AS_OF) -> CanonicalBar:
    return CanonicalBar(
        provider="yahoo",
        provider_symbol=symbol,
        symbol=symbol,
        raw_trading_date=trading_date.isoformat(),
        trading_date=trading_date,
        raw_open=100.0,
        raw_high=103.0,
        raw_low=99.0,
        raw_close=102.0,
        provider_adjusted_close=101.49,
        adjustment_factor=0.995,
        adjusted_open=99.5,
        adjusted_high=102.485,
        adjusted_low=98.505,
        adjusted_close=101.49,
        provider_volume=1_250_000.0,
        source_timestamp=NOW,
        ingestion_timestamp=NOW,
        price_basis=PRICE_BASIS,
        normalization_version=NORMALIZATION_VERSION,
    )


def _rejection() -> BarRejection:
    return BarRejection(
        provider="yahoo",
        provider_symbol="XLK",
        symbol="XLK",
        trading_date=AS_OF,
        code=RejectionCode.NEGATIVE_VOLUME,
        reason="volume must be >= 0",
        raw_evidence={"volume": -10, "row": 4},
        ingestion_timestamp=NOW,
    )


def _metrics(value: float = 0.01) -> SectorMetrics:
    return SectorMetrics(
        return_1d=value,
        return_5d=value * 2,
        return_20d=value * 3,
        return_60d=value * 4,
        relative_return_vs_spy_1d=value / 2,
        relative_return_vs_spy_5d=value,
        relative_return_vs_spy_20d=value * 1.5,
        relative_return_vs_spy_60d=value * 2,
        rvol20=1.25,
        flow_pressure_1d_proxy=0.5,
        cmf_5d_proxy=0.2,
        cmf_20d_proxy=0.15,
        cmf_60d_proxy=0.1,
    )


def _snapshot(
    *,
    symbol: str = "XLK",
    trading_date: date = AS_OF,
    metric_version: str = METRIC_VERSION,
    rank: int = 1,
) -> SectorSnapshot:
    return SectorSnapshot(
        trading_date=trading_date,
        symbol=symbol,
        asset_type="sector_etf",
        sector_name="Technology",
        metrics=_metrics(),
        ranks={
            "relative_return_vs_spy_20d": RankRecord(
                current_rank=rank,
                previous_rank=3,
                rank_change=3 - rank,
                rank_direction=RankDirection.IMPROVED,
            )
        },
        provider="yahoo",
        source_freshness={"status": "FRESH", "as_of": trading_date.isoformat()},
        price_basis=PRICE_BASIS,
        metric_version=metric_version,
        calculation_timestamp=NOW,
        data_quality_status="COMPLETE",
    )


def _start(uow: SqlUnitOfWork, *, as_of: date = AS_OF, input_hash: str = "b" * 64):
    return uow.feature_runs.start_run(
        as_of_date=as_of,
        run_type=RunType.DAILY_SNAPSHOT,
        universe_hash=UNIVERSE_HASH,
        input_hash=input_hash,
        config_json={"pipeline": PIPELINE_NAME},
    )


def _publish(uow: SqlUnitOfWork, run_id: int) -> None:
    uow.feature_runs.mark_completed(
        run_id,
        RunStats(12, 12, 0, 1.0, passed_symbols=12),
    )
    uow.feature_runs.publish_atomically(run_id, LATEST_POINTER_KEY)


def _counts(factory) -> dict[str, int]:
    with factory() as session:
        return {
            "audits": session.query(func.count(MarketIntelligenceRunAudit.run_id)).scalar(),
            "bars": session.query(func.count(MarketIntelligenceCanonicalBar.run_id)).scalar(),
            "rejections": session.query(func.count(MarketIntelligenceRejection.id)).scalar(),
            "snapshots": session.query(func.count(MarketIntelligenceSectorSnapshot.run_id)).scalar(),
            "pointers": session.query(func.count(FeatureRunPointer.key)).scalar(),
        }


def test_market_intelligence_repository_shares_uow_transaction(engine) -> None:
    factory = sessionmaker(bind=engine)
    with SqlUnitOfWork(factory) as uow:
        assert uow.market_intelligence._session is uow.feature_runs._session


def test_persisted_bundle_round_trips_lineage_rejection_audit_and_snapshot(engine) -> None:
    factory = sessionmaker(bind=engine)
    rejection = _rejection()
    audit = replace(
        _audit(status=IngestionStatus.PARTIAL),
        counters={"expected_symbols": 12, "valid_bars": 90, "rejected_bars": 1},
        missing_symbols=("XLU",),
        provider_failures=(ProviderSymbolFailure("XLU", "NO_DATA", "missing"),),
    )
    with SqlUnitOfWork(factory) as uow:
        run = _start(uow)
        uow.market_intelligence.persist_candidate(
            run.id,
            audit,
            (_bar(),),
            (rejection,),
            (_snapshot(),),
        )
        uow.commit()

    with SqlUnitOfWork(factory) as uow:
        bundle = uow.market_intelligence.find_exact(audit.idempotency_key)

    assert bundle is not None
    assert bundle.audit == audit
    assert bundle.canonical_bars == (_bar(),)
    assert bundle.rejections == (rejection,)
    assert bundle.snapshots == (_snapshot(),)
    assert bundle.lifecycle_status == "running"


def test_rollback_removes_all_market_intelligence_rows(engine) -> None:
    factory = sessionmaker(bind=engine)
    with pytest.raises(RuntimeError, match="force rollback"):
        with SqlUnitOfWork(factory) as uow:
            run = _start(uow)
            uow.market_intelligence.persist_candidate(
                run.id, _audit(), (_bar(),), (_rejection(),), (_snapshot(),)
            )
            raise RuntimeError("force rollback")

    assert _counts(factory) == {
        "audits": 0,
        "bars": 0,
        "rejections": 0,
        "snapshots": 0,
        "pointers": 0,
    }


def test_idempotency_key_conflict_is_translated_for_use_case_retry(engine) -> None:
    factory = sessionmaker(bind=engine)
    with pytest.raises(MarketIntelligenceIdempotencyConflict):
        with SqlUnitOfWork(factory) as uow:
            first = _start(uow, input_hash="1" * 64)
            second = _start(uow, input_hash="2" * 64)
            uow.market_intelligence.persist_candidate(first.id, _audit(), (), (), ())
            uow.market_intelligence.persist_candidate(second.id, _audit(), (), (), ())
            uow.commit()


def test_duplicate_snapshot_identity_is_rejected(engine) -> None:
    factory = sessionmaker(bind=engine)
    with pytest.raises(IntegrityError):
        with SqlUnitOfWork(factory) as uow:
            run = _start(uow)
            duplicate = _snapshot()
            uow.market_intelligence.persist_candidate(
                run.id, _audit(), (), (), (duplicate, duplicate)
            )
            uow.commit()


def test_latest_attempt_includes_failed_or_quarantined_run(engine) -> None:
    factory = sessionmaker(bind=engine)
    with SqlUnitOfWork(factory) as uow:
        published = _start(uow, as_of=date(2026, 5, 14), input_hash="1" * 64)
        uow.market_intelligence.persist_candidate(
            published.id,
            _audit(key="1" * 64, target_session=date(2026, 5, 14)),
            (), (), (_snapshot(trading_date=date(2026, 5, 14)),),
        )
        _publish(uow, published.id)
        failed = _start(uow, input_hash="2" * 64)
        uow.market_intelligence.persist_candidate(
            failed.id,
            replace(
                _audit(key="2" * 64, status=IngestionStatus.FAILED),
                request_failure=RequestFailure("TIMEOUT", "provider unavailable"),
            ),
            (), (), (),
        )
        uow.feature_runs.mark_failed(failed.id, RunStats(12, 0, 12, 1.0))
        uow.commit()

    with SqlUnitOfWork(factory) as uow:
        latest_attempt = uow.market_intelligence.get_latest_attempt()
        latest_published = uow.market_intelligence.get_latest_published(LATEST_POINTER_KEY)

    assert latest_attempt is not None and latest_attempt.run_id == failed.id
    assert latest_attempt.audit.ingestion_status is IngestionStatus.FAILED
    assert latest_attempt.audit.request_failure == RequestFailure(
        "TIMEOUT", "provider unavailable"
    )
    assert latest_published is not None and latest_published.run_id == published.id


def test_previous_published_excludes_same_date_partial_and_failed(engine) -> None:
    factory = sessionmaker(bind=engine)
    monday = date(2026, 5, 11)
    tuesday = date(2026, 5, 12)
    wednesday = date(2026, 5, 13)
    with SqlUnitOfWork(factory) as uow:
        good = _start(uow, as_of=monday, input_hash="1" * 64)
        uow.market_intelligence.persist_candidate(
            good.id,
            _audit(key="1" * 64, target_session=monday),
            (), (), (_snapshot(trading_date=monday, rank=7),),
        )
        _publish(uow, good.id)

        partial = _start(uow, as_of=tuesday, input_hash="2" * 64)
        uow.market_intelligence.persist_candidate(
            partial.id,
            _audit(key="2" * 64, status=IngestionStatus.PARTIAL, target_session=tuesday),
            (), (), (_snapshot(trading_date=tuesday, rank=4),),
        )
        uow.feature_runs.mark_completed(partial.id, RunStats(12, 11, 1, 1.0, passed_symbols=11))
        uow.feature_runs.mark_quarantined(partial.id, ())

        same_day = _start(uow, as_of=wednesday, input_hash="3" * 64)
        uow.market_intelligence.persist_candidate(
            same_day.id,
            _audit(key="3" * 64, target_session=wednesday),
            (), (), (_snapshot(trading_date=wednesday, rank=2),),
        )
        _publish(uow, same_day.id)
        uow.commit()

    with SqlUnitOfWork(factory) as uow:
        previous = uow.market_intelligence.get_previous_published(
            before=wednesday,
            metric_version=METRIC_VERSION,
        )

    assert previous is not None and previous.run_id == good.id
    assert previous.snapshots[0].ranks["relative_return_vs_spy_20d"].current_rank == 7


def test_history_filters_metric_version_and_keeps_newest_revision_per_session(engine) -> None:
    factory = sessionmaker(bind=engine)
    monday = date(2026, 5, 11)
    with SqlUnitOfWork(factory) as uow:
        old = _start(uow, as_of=monday, input_hash="1" * 64)
        uow.market_intelligence.persist_candidate(
            old.id,
            _audit(key="1" * 64, target_session=monday),
            (), (), (_snapshot(trading_date=monday, rank=7),),
        )
        _publish(uow, old.id)

        revision = _start(uow, as_of=monday, input_hash="2" * 64)
        uow.market_intelligence.persist_candidate(
            revision.id,
            _audit(key="2" * 64, target_session=monday),
            (), (), (_snapshot(trading_date=monday, rank=2),),
        )
        _publish(uow, revision.id)

        legacy = _start(uow, as_of=date(2026, 5, 12), input_hash="3" * 64)
        legacy_audit = replace(
            _audit(key="3" * 64, target_session=date(2026, 5, 12)),
            metric_version="legacy_v0",
        )
        uow.market_intelligence.persist_candidate(
            legacy.id,
            legacy_audit,
            (), (), (_snapshot(trading_date=date(2026, 5, 12), metric_version="legacy_v0"),),
        )
        _publish(uow, legacy.id)
        uow.commit()

    with SqlUnitOfWork(factory) as uow:
        history = uow.market_intelligence.list_published_history(
            metric_version=METRIC_VERSION,
            symbol="XLK",
            limit=10,
        )

    assert [bundle.run_id for bundle in history] == [revision.id]
    assert history[0].snapshots[0].ranks["relative_return_vs_spy_20d"].current_rank == 2
