"""Vertical-slice tests for idempotent, atomic sector publication."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

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
) -> BuildSectorSnapshotUseCase:
    return BuildSectorSnapshotUseCase(
        provider=_StaticProvider(batch),
        session_source=_StaticSessions(sessions),
        uow_factory=uow_factory or (lambda: SqlUnitOfWork(SessionLocal)),
        clock=lambda: NOW,
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


def test_commit_failure_rolls_back_run_snapshot_and_pointer() -> None:
    as_of = date(2026, 5, 15)

    class _FailingCommitUow(SqlUnitOfWork):
        def commit(self) -> None:
            raise RuntimeError("commit failed")

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
