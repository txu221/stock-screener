"""Idempotent Phase 1 runner with pointer-safe atomic publication."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4

from app.domain.feature_store.models import DQSeverity, RunStats, RunType
from app.domain.feature_store.quality import DQResult
from app.domain.market_intelligence.constants import (
    BENCHMARK_SYMBOL,
    LATEST_POINTER_KEY,
    MARKET_INTELLIGENCE_UNIVERSE,
    METRIC_VERSION,
    NORMALIZATION_VERSION,
    PIPELINE_NAME,
    PRICE_BASIS,
    SECTOR_SYMBOLS,
    UNIVERSE_HASH,
)
from app.domain.market_intelligence.metrics import (
    calculate_symbol_metrics,
    with_relative_returns,
)
from app.domain.market_intelligence.models import (
    CandidateSnapshot,
    IngestionStatus,
    MarketIntelligenceRunBundle,
    ProviderBatchResult,
    RawBar,
    RejectionCode,
    RunAudit,
    SectorMetrics,
    ValidationResult,
)
from app.domain.market_intelligence.ports import (
    MarketIntelligenceIdempotencyConflict,
)
from app.domain.market_intelligence.snapshot import (
    MINIMUM_HISTORY_SESSIONS,
    build_candidate_snapshot,
)
from app.domain.market_intelligence.validation import validate_provider_rows


class MarketIntelligenceProvider(Protocol):
    def fetch(
        self,
        symbols: Sequence[str],
        as_of: date,
    ) -> ProviderBatchResult:
        ...


class CompletedSessionSource(Protocol):
    def completed_sessions(
        self,
        market: str,
        as_of: date,
        minimum: int,
    ) -> Sequence[date]:
        ...


@dataclass(frozen=True)
class BuildSectorSnapshotCommand:
    as_of: date


@dataclass(frozen=True)
class BuildSectorSnapshotResult:
    run_id: int
    ingestion_status: IngestionStatus
    published: bool
    idempotency_key: str


@dataclass(frozen=True)
class _PreparedAttempt:
    batch: ProviderBatchResult
    sessions: tuple[date, ...]
    validation: ValidationResult
    metrics_by_symbol: Mapping[str, SectorMetrics]
    history_session_counts: Mapping[str, int]
    input_hash: str
    idempotency_key: str
    source_freshness: Mapping[str, Any]
    timestamp: datetime


def _stable_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return "NaN"
        return "Infinity" if value > 0 else "-Infinity"
    return value


def _raw_payload(row: RawBar) -> dict[str, Any]:
    return {
        name: _stable_value(getattr(row, name))
        for name in (
            "provider",
            "provider_symbol",
            "symbol",
            "raw_trading_date",
            "trading_date",
            "open",
            "high",
            "low",
            "close",
            "adjusted_close",
            "volume",
            "source_timestamp",
        )
    }


def _hash_payload(batch: ProviderBatchResult, sessions: Sequence[date]) -> str:
    row_payloads = [_raw_payload(row) for row in batch.rows]
    row_payloads.sort(
        key=lambda payload: json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        )
    )
    request_failure = None
    if batch.request_failure is not None:
        request_failure = {
            "code": batch.request_failure.code,
            "message": batch.request_failure.message,
        }
    payload = {
        "provider": batch.provider,
        "rows": row_payloads,
        "symbol_failures": sorted(
            (
                {
                    "symbol": failure.symbol,
                    "code": failure.code,
                    "message": failure.message,
                }
                for failure in batch.symbol_failures
            ),
            key=lambda value: (value["symbol"], value["code"], value["message"]),
        ),
        "request_failure": request_failure,
        "sessions": [session.isoformat() for session in sessions],
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _idempotency_key(as_of: date, input_hash: str) -> str:
    signature = "|".join(
        (
            PIPELINE_NAME,
            as_of.isoformat(),
            UNIVERSE_HASH,
            input_hash,
            NORMALIZATION_VERSION,
            METRIC_VERSION,
        )
    )
    return hashlib.sha256(signature.encode("ascii")).hexdigest()


def _request_failure_idempotency_key(
    as_of: date,
    input_hash: str,
    attempt_id: str,
) -> str:
    """Give transient request failures an audit identity per attempt."""
    signature = "|".join((_idempotency_key(as_of, input_hash), attempt_id))
    return hashlib.sha256(signature.encode("ascii")).hexdigest()


def _calculate_metrics(
    validation: ValidationResult,
    sessions: Sequence[date],
) -> tuple[dict[str, SectorMetrics], dict[str, int]]:
    bars_by_symbol: dict[str, list] = defaultdict(list)
    for bar in validation.canonical_bars:
        bars_by_symbol[bar.symbol].append(bar)

    metrics = {
        symbol: calculate_symbol_metrics(bars_by_symbol[symbol], sessions)
        for symbol in MARKET_INTELLIGENCE_UNIVERSE
    }
    spy_metrics = metrics[BENCHMARK_SYMBOL]
    for symbol in SECTOR_SYMBOLS:
        metrics[symbol] = with_relative_returns(metrics[symbol], spy_metrics)
    required_sessions = frozenset(sessions[-MINIMUM_HISTORY_SESSIONS:])
    history_counts = {
        symbol: len(
            required_sessions.intersection(
                bar.trading_date for bar in bars_by_symbol[symbol]
            )
        )
        for symbol in MARKET_INTELLIGENCE_UNIVERSE
    }
    return metrics, history_counts


def _source_freshness(
    validation: ValidationResult,
    *,
    as_of: date,
    response_timestamp: datetime,
) -> dict[str, Any]:
    latest_sessions: dict[str, date] = {}
    source_timestamps: list[datetime] = []
    for bar in validation.canonical_bars:
        current = latest_sessions.get(bar.symbol)
        if current is None or bar.trading_date > current:
            latest_sessions[bar.symbol] = bar.trading_date
        if bar.source_timestamp is not None:
            source_timestamps.append(bar.source_timestamp)
    stale_symbols = tuple(
        symbol
        for symbol in MARKET_INTELLIGENCE_UNIVERSE
        if latest_sessions.get(symbol) != as_of
    )
    return {
        "status": "FRESH" if not stale_symbols else "STALE_OR_MISSING",
        "as_of": as_of.isoformat(),
        "provider_response_at": response_timestamp.isoformat(),
        "latest_source_timestamp": (
            max(source_timestamps).isoformat() if source_timestamps else None
        ),
        "symbol_latest_sessions": {
            symbol: session.isoformat()
            for symbol, session in sorted(latest_sessions.items())
        },
        "stale_or_missing_symbols": list(stale_symbols),
    }


class BuildSectorSnapshotUseCase:
    def __init__(
        self,
        *,
        provider: MarketIntelligenceProvider,
        session_source: CompletedSessionSource,
        uow_factory: Callable[[], Any],
        clock: Callable[[], datetime],
        attempt_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._provider = provider
        self._session_source = session_source
        self._uow_factory = uow_factory
        self._clock = clock
        self._attempt_id_factory = attempt_id_factory or (lambda: uuid4().hex)

    def execute(
        self,
        command: BuildSectorSnapshotCommand,
    ) -> BuildSectorSnapshotResult:
        sessions = tuple(
            self._session_source.completed_sessions(
                "US", command.as_of, minimum=90
            )
        )
        if len(sessions) < 90:
            raise ValueError("at least 90 completed US sessions are required")
        batch = self._provider.fetch(MARKET_INTELLIGENCE_UNIVERSE, command.as_of)
        prepared = self._prepare(batch, sessions, command.as_of)

        try:
            with self._uow_factory() as uow:
                existing = uow.market_intelligence.find_exact(
                    prepared.idempotency_key
                )
                if existing is not None:
                    return self._result_from_existing(existing)

                previous = uow.market_intelligence.get_previous_published(
                    before=command.as_of,
                    metric_version=METRIC_VERSION,
                )
                previous_by_symbol = (
                    {snapshot.symbol: snapshot for snapshot in previous.snapshots}
                    if previous is not None
                    else {}
                )
                candidate = build_candidate_snapshot(
                    request_succeeded=batch.request_failure is None,
                    as_of=command.as_of,
                    metrics_by_symbol=prepared.metrics_by_symbol,
                    history_session_counts=prepared.history_session_counts,
                    received_symbols=prepared.validation.received_symbols,
                    rejection_count=len(prepared.validation.rejections),
                    provider_failures=batch.symbol_failures,
                    provider=batch.provider,
                    source_freshness=prepared.source_freshness,
                    calculation_timestamp=prepared.timestamp,
                    previous_published=previous_by_symbol,
                )
                audit = self._build_audit(prepared, candidate, command.as_of)
                run = uow.feature_runs.start_run(
                    as_of_date=command.as_of,
                    run_type=RunType.DAILY_SNAPSHOT,
                    universe_hash=UNIVERSE_HASH,
                    input_hash=prepared.input_hash,
                    config_json={
                        "pipeline": PIPELINE_NAME,
                        "metric_version": METRIC_VERSION,
                        "normalization_version": NORMALIZATION_VERSION,
                        "price_basis": PRICE_BASIS,
                    },
                )
                uow.market_intelligence.persist_candidate(
                    run.id,
                    audit,
                    prepared.validation.canonical_bars,
                    prepared.validation.rejections,
                    candidate.snapshots,
                )
                self._finalize_feature_run(uow, run.id, candidate)
                uow.commit()
                return BuildSectorSnapshotResult(
                    run_id=run.id,
                    ingestion_status=candidate.ingestion_status,
                    published=candidate.publishable,
                    idempotency_key=prepared.idempotency_key,
                )
        except MarketIntelligenceIdempotencyConflict:
            with self._uow_factory() as winner_uow:
                winner = winner_uow.market_intelligence.find_exact(
                    prepared.idempotency_key
                )
                if winner is None:
                    raise
                return self._result_from_existing(winner)

    def _prepare(
        self,
        batch: ProviderBatchResult,
        sessions: tuple[date, ...],
        as_of: date,
    ) -> _PreparedAttempt:
        timestamp = self._clock()
        validation = (
            ValidationResult((), (), ())
            if batch.request_failure is not None
            else validate_provider_rows(batch.rows, sessions, timestamp)
        )
        metrics, history_counts = _calculate_metrics(validation, sessions)
        input_hash = _hash_payload(batch, sessions)
        idempotency_key = _idempotency_key(as_of, input_hash)
        if batch.request_failure is not None:
            idempotency_key = _request_failure_idempotency_key(
                as_of,
                input_hash,
                self._attempt_id_factory(),
            )
        return _PreparedAttempt(
            batch=batch,
            sessions=sessions,
            validation=validation,
            metrics_by_symbol=metrics,
            history_session_counts=history_counts,
            input_hash=input_hash,
            idempotency_key=idempotency_key,
            source_freshness=_source_freshness(
                validation,
                as_of=as_of,
                response_timestamp=batch.response_timestamp,
            ),
            timestamp=timestamp,
        )

    @staticmethod
    def _build_audit(
        prepared: _PreparedAttempt,
        candidate: CandidateSnapshot,
        as_of: date,
    ) -> RunAudit:
        rejection_counts = Counter(
            rejection.code for rejection in prepared.validation.rejections
        )
        received_expected = set(prepared.validation.received_symbols).intersection(
            MARKET_INTELLIGENCE_UNIVERSE
        )
        missing = candidate.missing_symbols
        if prepared.batch.request_failure is not None:
            provider_status = "UNAVAILABLE"
        elif (
            prepared.batch.symbol_failures
            or prepared.validation.rejections
            or missing
        ):
            provider_status = "DEGRADED"
        else:
            provider_status = "AVAILABLE"
        counters = {
            "expected_symbols": len(MARKET_INTELLIGENCE_UNIVERSE),
            "symbols_received": len(received_expected),
            "valid_bars": len(prepared.validation.canonical_bars),
            "rejected_bars": len(prepared.validation.rejections),
            "missing_symbols": len(missing),
            "duplicate_rows": rejection_counts[RejectionCode.DUPLICATE_BAR],
            "invalid_volume": rejection_counts[RejectionCode.NEGATIVE_VOLUME],
            "invalid_ohlc": rejection_counts[RejectionCode.INVALID_OHLC_RELATION],
            "usable_symbols": len(candidate.usable_symbols),
            "snapshot_rows": len(candidate.snapshots),
        }
        return RunAudit(
            idempotency_key=prepared.idempotency_key,
            input_hash=prepared.input_hash,
            ingestion_status=candidate.ingestion_status,
            provider=prepared.batch.provider,
            provider_status=provider_status,
            request_failure=prepared.batch.request_failure,
            metric_version=METRIC_VERSION,
            normalization_version=NORMALIZATION_VERSION,
            price_basis=PRICE_BASIS,
            counters=counters,
            missing_symbols=missing,
            provider_failures=prepared.batch.symbol_failures,
            target_session=as_of,
            provider_response_at=(
                None
                if prepared.batch.request_failure is not None
                else prepared.batch.response_timestamp
            ),
            source_freshness=prepared.source_freshness,
            calculation_timestamp=prepared.timestamp,
            ingestion_timestamp=prepared.timestamp,
        )

    @staticmethod
    def _finalize_feature_run(
        uow: Any,
        run_id: int,
        candidate: CandidateSnapshot,
    ) -> None:
        usable_count = len(candidate.usable_symbols)
        stats = RunStats(
            total_symbols=len(MARKET_INTELLIGENCE_UNIVERSE),
            processed_symbols=usable_count,
            failed_symbols=len(MARKET_INTELLIGENCE_UNIVERSE) - usable_count,
            duration_seconds=0.0,
            passed_symbols=usable_count,
        )
        if candidate.ingestion_status is IngestionStatus.FAILED:
            uow.feature_runs.mark_failed(run_id, stats)
            return
        uow.feature_runs.mark_completed(run_id, stats)
        if candidate.ingestion_status is IngestionStatus.PARTIAL:
            uow.feature_runs.mark_quarantined(
                run_id,
                (
                    DQResult(
                        check_name="market_intelligence_completeness",
                        passed=False,
                        severity=DQSeverity.CRITICAL,
                        actual_value=float(usable_count),
                        threshold=float(len(MARKET_INTELLIGENCE_UNIVERSE)),
                        message="partial sector snapshot is audit-only and cannot publish",
                    ),
                ),
            )
            return
        uow.feature_runs.publish_atomically_if_not_older(
            run_id,
            LATEST_POINTER_KEY,
        )

    @staticmethod
    def _result_from_existing(
        bundle: MarketIntelligenceRunBundle,
    ) -> BuildSectorSnapshotResult:
        return BuildSectorSnapshotResult(
            run_id=bundle.run_id,
            ingestion_status=bundle.audit.ingestion_status,
            published=bundle.lifecycle_status == "published",
            idempotency_key=bundle.audit.idempotency_key,
        )
