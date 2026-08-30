"""Vertical-slice tests for idempotent, atomic sector publication."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func

from app.database import SessionLocal
from app.domain.market_intelligence.constants import (
    LATEST_POINTER_KEY,
    MARKET_INTELLIGENCE_UNIVERSE,
    METRIC_VERSION,
)
from app.domain.market_intelligence.models import (
    IngestionStatus,
    ProviderBatchResult,
    RawBar,
    RequestFailure,
)
from app.domain.market_intelligence.observability import (
    MARKET_INTELLIGENCE_STAGE_TIMING_KEYS,
    PIPELINE_VERSION,
    MarketIntelligenceErrorCategory,
)
from app.domain.market_intelligence.ports import (
    MarketIntelligenceIdempotencyConflict,
)
from app.infra.db.models.feature_store import FeatureRun, FeatureRunPointer
from app.infra.db.models.market_intelligence import (
    MarketIntelligenceCanonicalBar,
    MarketIntelligenceRejection,
    MarketIntelligenceRunAudit,
    MarketIntelligenceSectorSnapshot,
)
from app.infra.db.uow import SqlUnitOfWork
from app.use_cases.market_intelligence.build_sector_snapshot import (
    BuildSectorSnapshotCommand,
    BuildSectorSnapshotUseCase,
    _hash_payload,
)

SCENARIO_PATH = (
    Path(__file__).parents[2]
    / "fixtures"
    / "market_intelligence"
    / "sector_golden_scenario.json"
)
NOW = datetime(2026, 5, 15, 21, 10, tzinfo=timezone.utc)


def _sessions(end: date, count: int = 91) -> tuple[date, ...]:
    result: list[date] = []
    candidate = end
    while len(result) < count:
        if candidate.weekday() < 5:
            result.append(candidate)
        candidate -= timedelta(days=1)
    return tuple(reversed(result))


def _golden_rows(end: date) -> tuple[RawBar, ...]:
    scenario = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    symbol_overrides = scenario["symbols"]
    default_sector = scenario["default_sector"]
    adjustment_factor = float(scenario["adjustment_factor"])
    source_timestamp = datetime.combine(
        end, datetime.min.time(), timezone.utc
    ).replace(hour=21, minute=5)
    rows: list[RawBar] = []
    for symbol in MARKET_INTELLIGENCE_UNIVERSE:
        config = dict(default_sector)
        config.update(symbol_overrides.get(symbol, {}))
        for index, session in enumerate(_sessions(end)):
            close = float(config["start_close"]) * (
                1.0 + float(config["daily_return"])
            ) ** index
            rows.append(
                RawBar(
                    provider="yahoo",
                    provider_symbol=symbol,
                    symbol=symbol,
                    raw_trading_date=session.isoformat(),
                    trading_date=session,
                    open=close * 0.995,
                    high=close * 1.01,
                    low=close * 0.99,
                    close=close,
                    adjusted_close=close * adjustment_factor,
                    volume=float(config["base_volume"]) + index * 1_000.0,
                    source_timestamp=source_timestamp,
                )
            )
    return tuple(rows)


def _batch(
    end: date,
    *,
    missing_symbol: str | None = None,
    request_failure: RequestFailure | None = None,
) -> ProviderBatchResult:
    rows = () if request_failure else _golden_rows(end)
    if missing_symbol is not None:
        rows = tuple(row for row in rows if row.symbol != missing_symbol)
    return ProviderBatchResult(
        provider="yahoo",
        response_timestamp=datetime.combine(
            end, datetime.min.time(), timezone.utc
        ).replace(hour=21, minute=6),
        rows=rows,
        symbol_failures=(),
        request_failure=request_failure,
    )


def test_input_hash_changes_when_yahoo_action_provenance_changes() -> None:
    baseline = _batch(date(2026, 5, 15))
    changed_rows = list(baseline.rows)
    changed_rows[0] = replace(
        changed_rows[0], dividend_cash=1.25, split_ratio=2.0
    )
    changed = replace(baseline, rows=tuple(changed_rows))
    sessions = _sessions(date(2026, 5, 15))

    assert _hash_payload(baseline, sessions) != _hash_payload(changed, sessions)


class _StaticProvider:
    def __init__(self, batch: ProviderBatchResult) -> None:
        self.batch = batch

    def fetch(self, symbols, as_of):
        assert tuple(symbols) == MARKET_INTELLIGENCE_UNIVERSE
        return self.batch


class _StaticSessions:
    def __init__(self, sessions: tuple[date, ...]) -> None:
        self.sessions = sessions

    def completed_sessions(self, market: str, as_of: date, minimum: int):
        assert market == "US"
        assert self.sessions[-1] == as_of
        assert minimum == 90
        return self.sessions


def _runner(
    batch: ProviderBatchResult,
    sessions: tuple[date, ...],
    *,
    uow_factory=None,
    monotonic=None,
) -> BuildSectorSnapshotUseCase:
    kwargs = {}
    if monotonic is not None:
        kwargs["monotonic"] = monotonic
    return BuildSectorSnapshotUseCase(
        provider=_StaticProvider(batch),
        session_source=_StaticSessions(sessions),
        uow_factory=uow_factory or (lambda: SqlUnitOfWork(SessionLocal)),
        clock=lambda: NOW,
        **kwargs,
    )


def _command(as_of: date) -> BuildSectorSnapshotCommand:
    return BuildSectorSnapshotCommand(as_of=as_of)


def _pointer_run_id() -> int | None:
    with SessionLocal() as session:
        pointer = session.get(FeatureRunPointer, LATEST_POINTER_KEY)
        return None if pointer is None else pointer.run_id


def _table_counts(run_id: int | None = None) -> dict[str, int]:
    with SessionLocal() as session:
        filters = (() if run_id is None else (MarketIntelligenceRunAudit.run_id == run_id,))
        return {
            "runs": int(session.query(func.count(FeatureRun.id)).scalar() or 0),
            "audits": int(
                session.query(func.count(MarketIntelligenceRunAudit.run_id))
                .filter(*filters)
                .scalar()
                or 0
            ),
            "bars": int(
                session.query(func.count(MarketIntelligenceCanonicalBar.run_id))
                .filter(*(() if run_id is None else (MarketIntelligenceCanonicalBar.run_id == run_id,)))
                .scalar()
                or 0
            ),
            "rejections": int(
                session.query(func.count(MarketIntelligenceRejection.id))
                .filter(*(() if run_id is None else (MarketIntelligenceRejection.run_id == run_id,)))
                .scalar()
                or 0
            ),
            "snapshots": int(
                session.query(func.count(MarketIntelligenceSectorSnapshot.run_id))
                .filter(*(() if run_id is None else (MarketIntelligenceSectorSnapshot.run_id == run_id,)))
                .scalar()
                or 0
            ),
            "pointers": int(session.query(func.count(FeatureRunPointer.key)).scalar() or 0),
        }


def test_case_a_complete_run_publishes_twelve_rows() -> None:
    as_of = date(2026, 5, 15)
    result = _runner(_batch(as_of), _sessions(as_of)).execute(_command(as_of))

    assert result.ingestion_status is IngestionStatus.SUCCEEDED
    assert result.published is True
    assert _pointer_run_id() == result.run_id
    assert _table_counts(result.run_id)["snapshots"] == 12
    assert result.reused is False


def test_run_persists_complete_final_observability_and_measured_duration() -> None:
    as_of = date(2026, 5, 15)
    batch = replace(
        _batch(as_of),
        stage_timings={"provider_fetch_ms": 125.0, "normalization_ms": 25.0},
    )

    class _StepTimer:
        value = 100.0

        def __call__(self) -> float:
            self.value += 0.01
            return self.value

    result = _runner(
        batch,
        _sessions(as_of),
        monotonic=_StepTimer(),
    ).execute(
        BuildSectorSnapshotCommand(
            as_of=as_of,
            reuse_published=False,
            force_refresh=True,
            retry_count=2,
            broker_redelivered=True,
        )
    )

    with SessionLocal() as session:
        audit = session.get(MarketIntelligenceRunAudit, result.run_id)
        run = session.get(FeatureRun, result.run_id)

    assert audit.pipeline_version == PIPELINE_VERSION
    assert audit.failure_category is None
    assert tuple(audit.stage_timings_json) == MARKET_INTELLIGENCE_STAGE_TIMING_KEYS
    assert audit.stage_timings_json["provider_fetch_ms"] == 125.0
    assert audit.stage_timings_json["normalization_ms"] == 25.0
    assert all(
        math.isfinite(value) and value >= 0
        for value in audit.stage_timings_json.values()
    )
    assert audit.publication_status == "PUBLISHED"
    assert audit.retry_status == "BROKER_REDELIVERED"
    assert audit.reuse_status == "FORCE_REFRESH"
    assert run.stats_json["duration_seconds"] == pytest.approx(
        audit.stage_timings_json["total_ms"] / 1000.0
    )


@pytest.mark.parametrize(
    ("batch", "expected_category"),
    (
        (
            _batch(
                date(2026, 5, 15),
                request_failure=RequestFailure(
                    "PROVIDER_SCHEMA_DRIFT", "Yahoo changed its schema"
                ),
            ),
            MarketIntelligenceErrorCategory.PROVIDER_SCHEMA_DRIFT.value,
        ),
        (
            _batch(
                date(2026, 5, 15),
                request_failure=RequestFailure("PROVIDER_TIMEOUT", "Yahoo timed out"),
            ),
            MarketIntelligenceErrorCategory.PROVIDER_FAILURE.value,
        ),
    ),
)
def test_request_failures_persist_stable_failure_category(
    batch: ProviderBatchResult,
    expected_category: str,
) -> None:
    as_of = date(2026, 5, 15)
    result = _runner(batch, _sessions(as_of)).execute(_command(as_of))

    with SessionLocal() as session:
        audit = session.get(MarketIntelligenceRunAudit, result.run_id)

    assert audit.failure_category == expected_category
    assert audit.publication_status == "FAILED"


def test_case_b_eleven_of_twelve_is_partial_and_keeps_pointer() -> None:
    monday = date(2026, 5, 11)
    tuesday = date(2026, 5, 12)
    previous = _runner(_batch(monday), _sessions(monday)).execute(_command(monday))
    partial = _runner(
        _batch(tuesday, missing_symbol="XLU"), _sessions(tuesday)
    ).execute(_command(tuesday))

    assert partial.ingestion_status is IngestionStatus.PARTIAL
    assert partial.published is False
    assert _pointer_run_id() == previous.run_id
    with SessionLocal() as session:
        assert session.get(FeatureRun, partial.run_id).status == "quarantined"


def test_case_c_zero_usable_symbols_is_failed() -> None:
    as_of = date(2026, 5, 15)
    empty = ProviderBatchResult(
        provider="yahoo",
        response_timestamp=NOW,
        rows=(),
        symbol_failures=(),
        request_failure=None,
    )
    result = _runner(empty, _sessions(as_of)).execute(_command(as_of))

    assert result.ingestion_status is IngestionStatus.FAILED
    assert result.published is False
    assert _pointer_run_id() is None
    assert _table_counts(result.run_id)["snapshots"] == 0


def test_case_d_request_failure_has_no_fabricated_row_rejections() -> None:
    as_of = date(2026, 5, 15)
    batch = _batch(
        as_of,
        request_failure=RequestFailure("PROVIDER_TIMEOUT", "Yahoo timed out"),
    )
    result = _runner(batch, _sessions(as_of)).execute(_command(as_of))

    assert result.ingestion_status is IngestionStatus.FAILED
    counts = _table_counts(result.run_id)
    assert counts["rejections"] == 0
    with SessionLocal() as session:
        audit = session.get(MarketIntelligenceRunAudit, result.run_id)
        assert audit.request_failure_json == {
            "code": "PROVIDER_TIMEOUT",
            "message": "Yahoo timed out",
        }


def test_repeated_request_failures_create_distinct_audited_attempts() -> None:
    as_of = date(2026, 5, 15)
    batch = _batch(
        as_of,
        request_failure=RequestFailure("PROVIDER_TIMEOUT", "Yahoo timed out"),
    )

    first = _runner(batch, _sessions(as_of)).execute(_command(as_of))
    second = _runner(batch, _sessions(as_of)).execute(_command(as_of))

    assert first.run_id != second.run_id
    assert first.idempotency_key != second.idempotency_key
    assert _table_counts()["audits"] == 2
    with SqlUnitOfWork(SessionLocal) as uow:
        latest_attempt = uow.market_intelligence.get_latest_attempt()
    assert latest_attempt is not None
    assert latest_attempt.run_id == second.run_id
    assert latest_attempt.audit.ingestion_status is IngestionStatus.FAILED


def test_case_e_latest_read_remains_previous_complete_after_partial() -> None:
    monday = date(2026, 5, 11)
    tuesday = date(2026, 5, 12)
    complete = _runner(_batch(monday), _sessions(monday)).execute(_command(monday))
    partial = _runner(
        _batch(tuesday, missing_symbol="XLU"), _sessions(tuesday)
    ).execute(_command(tuesday))

    with SqlUnitOfWork(SessionLocal) as uow:
        latest = uow.market_intelligence.get_latest_published(LATEST_POINTER_KEY)
        attempt = uow.market_intelligence.get_latest_attempt()

    assert latest is not None and latest.run_id == complete.run_id
    assert attempt is not None and attempt.run_id == partial.run_id


def test_case_f_wednesday_rank_change_uses_monday_not_tuesday_partial() -> None:
    monday = date(2026, 5, 11)
    tuesday = date(2026, 5, 12)
    wednesday = date(2026, 5, 13)
    monday_result = _runner(
        _batch(monday), _sessions(monday)
    ).execute(_command(monday))
    _runner(
        _batch(tuesday, missing_symbol="XLU"), _sessions(tuesday)
    ).execute(_command(tuesday))
    wednesday_result = _runner(
        _batch(wednesday), _sessions(wednesday)
    ).execute(_command(wednesday))

    with SqlUnitOfWork(SessionLocal) as uow:
        monday_bundle = uow.market_intelligence.find_exact(monday_result.idempotency_key)
        wednesday_bundle = uow.market_intelligence.find_exact(
            wednesday_result.idempotency_key
        )
    assert monday_bundle is not None and wednesday_bundle is not None
    monday_xlk = next(row for row in monday_bundle.snapshots if row.symbol == "XLK")
    wednesday_xlk = next(
        row for row in wednesday_bundle.snapshots if row.symbol == "XLK"
    )
    for metric_name, rank in wednesday_xlk.ranks.items():
        assert rank.previous_rank == monday_xlk.ranks[metric_name].current_rank


def test_case_g_identical_input_rerun_reuses_run_and_row_counts() -> None:
    as_of = date(2026, 5, 15)
    batch = _batch(as_of)
    first = _runner(batch, _sessions(as_of)).execute(_command(as_of))
    before = _table_counts()
    reordered_retry = replace(
        batch,
        response_timestamp=batch.response_timestamp + timedelta(minutes=1),
        rows=tuple(reversed(batch.rows)),
    )
    second = _runner(reordered_retry, _sessions(as_of)).execute(_command(as_of))

    assert second.run_id == first.run_id
    assert second.idempotency_key == first.idempotency_key
    assert _table_counts() == before


def test_scheduled_retry_reuses_published_session_before_provider_fetch() -> None:
    as_of = date(2026, 5, 15)
    first = _runner(_batch(as_of), _sessions(as_of)).execute(_command(as_of))

    class _ProviderMustNotRun:
        def fetch(self, symbols, requested_as_of):
            raise AssertionError(
                "published scheduled retry must not refetch provider data"
            )

    retry_runner = BuildSectorSnapshotUseCase(
        provider=_ProviderMustNotRun(),
        session_source=_StaticSessions(_sessions(as_of)),
        uow_factory=lambda: SqlUnitOfWork(SessionLocal),
        clock=lambda: NOW + timedelta(minutes=5),
    )

    retry = retry_runner.execute(
        BuildSectorSnapshotCommand(as_of=as_of, reuse_published=True)
    )

    assert retry.run_id == first.run_id
    assert retry.idempotency_key == first.idempotency_key
    assert retry.ingestion_status is IngestionStatus.SUCCEEDED
    assert retry.published is True
    assert retry.reused is True
    assert _table_counts()["runs"] == 1


def test_scheduled_retry_rebuilds_unpublished_partial_session() -> None:
    as_of = date(2026, 5, 15)
    partial = _runner(
        _batch(as_of, missing_symbol="XLU"),
        _sessions(as_of),
    ).execute(_command(as_of))

    recovered = _runner(_batch(as_of), _sessions(as_of)).execute(
        BuildSectorSnapshotCommand(as_of=as_of, reuse_published=True)
    )

    assert partial.ingestion_status is IngestionStatus.PARTIAL
    assert recovered.ingestion_status is IngestionStatus.SUCCEEDED
    assert recovered.published is True
    assert recovered.run_id != partial.run_id
    assert _pointer_run_id() == recovered.run_id


def test_commit_failure_rolls_back_run_snapshot_and_pointer(caplog) -> None:
    as_of = date(2026, 5, 15)

    class _FailingCommitUow(SqlUnitOfWork):
        def commit(self) -> None:
            raise RuntimeError("commit failed")

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="commit failed"):
            _runner(
                _batch(as_of),
                _sessions(as_of),
                uow_factory=lambda: _FailingCommitUow(SessionLocal),
            ).execute(_command(as_of))

    assert _table_counts() == {
        "runs": 0,
        "audits": 0,
        "bars": 0,
        "rejections": 0,
        "snapshots": 0,
        "pointers": 0,
    }
    failure = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "market_intelligence_run_failed"
    )
    assert failure.failure_category == "DATABASE_FAILURE"
    assert failure.stage == "persistence"
    assert failure.as_of_date == as_of.isoformat()
    assert failure.pipeline_version == PIPELINE_VERSION
    assert failure.metric_version == METRIC_VERSION


@pytest.mark.parametrize("failing_method", ("update_stats", "update_observability"))
def test_observability_persistence_failure_is_database_failure(
    failing_method,
    caplog,
) -> None:
    as_of = date(2026, 5, 15)

    class _FailingObservabilityUow(SqlUnitOfWork):
        def __enter__(self):
            uow = super().__enter__()
            repository = (
                uow.feature_runs
                if failing_method == "update_stats"
                else uow.market_intelligence
            )

            def fail(*args, **kwargs):
                raise RuntimeError(f"{failing_method} failed")

            setattr(repository, failing_method, fail)
            return uow

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match=f"{failing_method} failed") as raised:
            _runner(
                _batch(as_of),
                _sessions(as_of),
                uow_factory=lambda: _FailingObservabilityUow(SessionLocal),
            ).execute(_command(as_of))

    assert raised.value.market_intelligence_failure_category is (
        MarketIntelligenceErrorCategory.DATABASE_FAILURE
    )
    assert raised.value.market_intelligence_stage == "persistence"
    failure = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "market_intelligence_run_failed"
    )
    assert failure.failure_category == "DATABASE_FAILURE"
    assert failure.stage == "persistence"


def test_validation_exception_is_logged_at_validation_boundary(
    monkeypatch,
    caplog,
) -> None:
    from app.use_cases.market_intelligence import build_sector_snapshot as module

    as_of = date(2026, 5, 15)

    def fail_validation(*args, **kwargs):
        raise ValueError("invalid provider row")

    monkeypatch.setattr(module, "validate_provider_rows", fail_validation)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(ValueError, match="invalid provider row"):
            _runner(_batch(as_of), _sessions(as_of)).execute(_command(as_of))

    failure = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "market_intelligence_run_failed"
    )
    assert failure.failure_category == "INVALID_MARKET_DATA"
    assert failure.stage == "validation"


def test_calculation_exception_is_logged_at_calculation_boundary(
    monkeypatch,
    caplog,
) -> None:
    from app.use_cases.market_intelligence import build_sector_snapshot as module

    as_of = date(2026, 5, 15)

    def fail_calculation(*args, **kwargs):
        raise ArithmeticError("metric calculation failed")

    monkeypatch.setattr(module, "_calculate_metrics", fail_calculation)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(ArithmeticError, match="metric calculation failed"):
            _runner(_batch(as_of), _sessions(as_of)).execute(_command(as_of))

    failure = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "market_intelligence_run_failed"
    )
    assert failure.failure_category == "INVALID_MARKET_DATA"
    assert failure.stage == "calculation"


def test_provider_exception_is_logged_as_provider_failure(
    monkeypatch,
    caplog,
) -> None:
    as_of = date(2026, 5, 15)

    def fail_fetch(self, symbols, requested_as_of):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(_StaticProvider, "fetch", fail_fetch)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="provider unavailable"):
            _runner(_batch(as_of), _sessions(as_of)).execute(_command(as_of))

    failure = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "market_intelligence_run_failed"
    )
    assert failure.failure_category == "PROVIDER_FAILURE"
    assert failure.stage == "provider_fetch"


def test_short_session_history_uses_typed_insufficient_history_category(
    caplog,
) -> None:
    as_of = date(2026, 5, 15)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(ValueError, match="at least 90 completed US sessions"):
            _runner(_batch(as_of), _sessions(as_of, count=89)).execute(
                _command(as_of)
            )

    failure = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "market_intelligence_run_failed"
    )
    assert failure.failure_category == "INSUFFICIENT_HISTORY"
    assert failure.stage == "validation"


def test_candidate_assembly_exception_is_logged_at_calculation_boundary(
    monkeypatch,
    caplog,
) -> None:
    from app.use_cases.market_intelligence import build_sector_snapshot as module

    as_of = date(2026, 5, 15)

    def fail_candidate_assembly(*args, **kwargs):
        raise ArithmeticError("candidate assembly failed")

    monkeypatch.setattr(
        module,
        "build_candidate_snapshot",
        fail_candidate_assembly,
    )

    with caplog.at_level(logging.ERROR):
        with pytest.raises(ArithmeticError, match="candidate assembly failed"):
            _runner(_batch(as_of), _sessions(as_of)).execute(_command(as_of))

    failure = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "market_intelligence_run_failed"
    )
    assert failure.failure_category == "INVALID_MARKET_DATA"
    assert failure.stage == "calculation"


@pytest.mark.parametrize(
    ("lifecycle_method", "batch"),
    (
        ("mark_completed", _batch(date(2026, 5, 15))),
        (
            "mark_failed",
            _batch(
                date(2026, 5, 15),
                request_failure=RequestFailure(
                    "PROVIDER_TIMEOUT",
                    "Yahoo timed out",
                ),
            ),
        ),
    ),
)
def test_feature_run_lifecycle_failure_is_database_failure(
    lifecycle_method,
    batch,
    caplog,
) -> None:
    as_of = date(2026, 5, 15)

    class _FailingLifecycleUow(SqlUnitOfWork):
        def __enter__(self):
            uow = super().__enter__()

            def fail(*args, **kwargs):
                raise RuntimeError(f"{lifecycle_method} failed")

            setattr(uow.feature_runs, lifecycle_method, fail)
            return uow

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match=f"{lifecycle_method} failed"):
            _runner(
                batch,
                _sessions(as_of),
                uow_factory=lambda: _FailingLifecycleUow(SessionLocal),
            ).execute(_command(as_of))

    failure = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "market_intelligence_run_failed"
    )
    assert failure.failure_category == "DATABASE_FAILURE"
    assert failure.stage == "persistence"


def test_lifecycle_time_is_persistence_and_pointer_swap_time_is_publication() -> None:
    as_of = date(2026, 5, 15)

    class _ControlledTimer:
        value = 100.0

        def __call__(self) -> float:
            return self.value

        def advance(self, seconds: float) -> None:
            self.value += seconds

    timer = _ControlledTimer()

    class _TimedUow(SqlUnitOfWork):
        def __enter__(self):
            uow = super().__enter__()

            def add_time(repository, method_name, seconds):
                original = getattr(repository, method_name)

                def timed(*args, **kwargs):
                    timer.advance(seconds)
                    return original(*args, **kwargs)

                setattr(repository, method_name, timed)

            add_time(uow.market_intelligence, "persist_candidate", 0.1)
            add_time(uow.feature_runs, "mark_completed", 0.2)
            add_time(
                uow.feature_runs,
                "publish_atomically_if_not_older",
                0.3,
            )
            return uow

    result = _runner(
        _batch(as_of),
        _sessions(as_of),
        uow_factory=lambda: _TimedUow(SessionLocal),
        monotonic=timer,
    ).execute(_command(as_of))

    with SessionLocal() as session:
        audit = session.get(MarketIntelligenceRunAudit, result.run_id)

    assert tuple(audit.stage_timings_json) == MARKET_INTELLIGENCE_STAGE_TIMING_KEYS
    assert all(
        math.isfinite(value) and value >= 0
        for value in audit.stage_timings_json.values()
    )
    assert audit.stage_timings_json["persistence_ms"] == pytest.approx(300.0)
    assert audit.stage_timings_json["publication_ms"] == pytest.approx(300.0)


def test_provider_history_outside_session_reference_is_rejected_not_silently_cropped() -> None:
    as_of = date(2026, 5, 15)
    provider_batch = _batch(as_of)
    exact_window = _sessions(as_of)[-90:]
    oldest = min(row.trading_date for row in provider_batch.rows)
    rows = tuple(
        row
        for row in provider_batch.rows
        if row.trading_date != oldest or row.symbol == "SPY"
    )
    provider_batch = replace(provider_batch, rows=rows)

    result = _runner(provider_batch, exact_window).execute(_command(as_of))

    assert result.ingestion_status is IngestionStatus.PARTIAL
    counts = _table_counts(result.run_id)
    assert counts["bars"] == 12 * 90
    assert counts["rejections"] == 1
    with SessionLocal() as session:
        rejection = (
            session.query(MarketIntelligenceRejection)
            .filter(MarketIntelligenceRejection.run_id == result.run_id)
            .one()
        )
    assert rejection.symbol == "SPY"
    assert rejection.rejection_code == "INVALID_TRADING_DATE"


def test_negative_volume_in_extended_completed_session_is_quarantined() -> None:
    as_of = date(2026, 5, 15)
    sessions = _sessions(as_of)
    provider_batch = _batch(as_of)
    oldest = sessions[0]
    rows = tuple(
        replace(row, volume=-1.0)
        if row.symbol == "SPY" and row.trading_date == oldest
        else row
        for row in provider_batch.rows
    )

    result = _runner(
        replace(provider_batch, rows=rows), sessions
    ).execute(_command(as_of))

    assert result.ingestion_status is IngestionStatus.PARTIAL
    with SessionLocal() as session:
        audit = session.get(MarketIntelligenceRunAudit, result.run_id)
        rejection = (
            session.query(MarketIntelligenceRejection)
            .filter(MarketIntelligenceRejection.run_id == result.run_id)
            .one()
        )
    assert audit.counters_json["invalid_volume"] == 1
    assert rejection.rejection_code == "NEGATIVE_VOLUME"


def test_trailing_ninety_requires_exact_session_coverage() -> None:
    as_of = date(2026, 5, 15)
    sessions = _sessions(as_of)
    provider_batch = _batch(as_of)
    missing_non_anchor = sessions[-80]
    rows = tuple(
        row
        for row in provider_batch.rows
        if not (
            row.symbol == "SPY"
            and row.trading_date == missing_non_anchor
        )
    )

    result = _runner(
        replace(provider_batch, rows=rows), sessions
    ).execute(_command(as_of))

    assert result.ingestion_status is IngestionStatus.PARTIAL
    assert result.published is False
    assert _pointer_run_id() is None
    with SessionLocal() as session:
        audit = session.get(MarketIntelligenceRunAudit, result.run_id)
    assert audit.counters_json["usable_symbols"] == 11


def test_older_successful_backfill_cannot_move_latest_pointer_backward() -> None:
    tuesday = date(2026, 5, 12)
    wednesday = date(2026, 5, 13)
    newest = _runner(
        _batch(wednesday), _sessions(wednesday)
    ).execute(_command(wednesday))
    older = _runner(
        _batch(tuesday), _sessions(tuesday)
    ).execute(_command(tuesday))

    assert newest.ingestion_status is IngestionStatus.SUCCEEDED
    assert older.ingestion_status is IngestionStatus.SUCCEEDED
    assert _pointer_run_id() == newest.run_id
    with SqlUnitOfWork(SessionLocal) as uow:
        older_bundle = uow.market_intelligence.find_exact(older.idempotency_key)
    assert older_bundle is not None
    assert older_bundle.lifecycle_status == "published"


def test_concurrent_idempotency_conflict_reads_committed_winner() -> None:
    as_of = date(2026, 5, 15)
    created_uows = []

    class _MarketIntelligenceRepo:
        def __init__(self, *, winner: bool) -> None:
            self._winner = winner

        def find_exact(self, idempotency_key):
            if not self._winner:
                return None
            return SimpleNamespace(
                run_id=77,
                lifecycle_status="published",
                audit=SimpleNamespace(
                    ingestion_status=IngestionStatus.SUCCEEDED,
                    idempotency_key=idempotency_key,
                    failure_category=None,
                ),
            )

        def get_previous_published(self, **kwargs):
            return None

        def persist_candidate(self, *args, **kwargs):
            raise MarketIntelligenceIdempotencyConflict

    class _RaceUow:
        def __init__(self, *, winner: bool) -> None:
            self.market_intelligence = _MarketIntelligenceRepo(winner=winner)
            self.feature_runs = SimpleNamespace(
                start_run=lambda **kwargs: SimpleNamespace(id=99)
            )

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def commit(self):
            raise AssertionError("conflicting transaction must not commit")

    def uow_factory():
        uow = _RaceUow(winner=bool(created_uows))
        created_uows.append(uow)
        return uow

    result = _runner(
        _batch(as_of),
        _sessions(as_of),
        uow_factory=uow_factory,
    ).execute(_command(as_of))

    assert len(created_uows) == 2
    assert result.run_id == 77
    assert result.ingestion_status is IngestionStatus.SUCCEEDED
    assert result.published is True
