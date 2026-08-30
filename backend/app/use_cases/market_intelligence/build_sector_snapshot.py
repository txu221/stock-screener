"""Idempotent Phase 1 runner with pointer-safe atomic publication."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
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
from app.domain.market_intelligence.observability import (
    PIPELINE_VERSION,
    InsufficientMarketHistoryError,
    MarketIntelligenceErrorCategory,
    complete_stage_timings,
    elapsed_milliseconds,
    failure_category_for_exception,
    failure_category_for_request,
)
from app.domain.market_intelligence.ports import (
    MarketIntelligenceIdempotencyConflict,
)
from app.domain.market_intelligence.snapshot import (
    MINIMUM_HISTORY_SESSIONS,
    build_candidate_snapshot,
)
from app.domain.market_intelligence.validation import validate_provider_rows


logger = logging.getLogger(__name__)


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
    reuse_published: bool = False
    force_refresh: bool = False
    retry_count: int = 0
    broker_redelivered: bool = False


@dataclass(frozen=True)
class BuildSectorSnapshotResult:
    run_id: int
    ingestion_status: IngestionStatus
    published: bool
    idempotency_key: str
    reused: bool = False
    failure_category: MarketIntelligenceErrorCategory | None = None


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
    stage_timings: dict[str, float]


@dataclass
class _ExecutionState:
    stage: str = "persistence"


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
            "dividend_cash",
            "split_ratio",
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
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._provider = provider
        self._session_source = session_source
        self._uow_factory = uow_factory
        self._clock = clock
        self._attempt_id_factory = attempt_id_factory or (lambda: uuid4().hex)
        self._monotonic = monotonic

    def execute(
        self,
        command: BuildSectorSnapshotCommand,
    ) -> BuildSectorSnapshotResult:
        total_started = self._monotonic()
        execution = _ExecutionState()
        run_id: int | None = None
        provider_name: str | None = None
        retry_status = self._retry_status(command)
        reuse_status = "FORCE_REFRESH" if command.force_refresh else "NEW"
        try:
            if command.reuse_published:
                with self._uow_factory() as uow:
                    published = uow.market_intelligence.list_published_history(
                        metric_version=METRIC_VERSION,
                        date_from=command.as_of,
                        date_to=command.as_of,
                        limit=1,
                    )
                if published:
                    result = self._result_from_existing(published[0])
                    self._log_run(
                        event="market_intelligence_run_reused",
                        command=command,
                        run_id=result.run_id,
                        provider=published[0].audit.provider,
                        stage="reuse",
                        duration_ms=elapsed_milliseconds(
                            total_started, self._monotonic()
                        ),
                        publication_status="PUBLISHED",
                        retry_status=retry_status,
                        reuse_status="PUBLISHED_RESULT_REUSED",
                    )
                    return result

            execution.stage = "validation"
            sessions = tuple(
                self._session_source.completed_sessions(
                    "US", command.as_of, minimum=90
                )
            )
            if len(sessions) < 90:
                raise InsufficientMarketHistoryError(
                    "insufficient history: at least 90 completed US sessions "
                    "are required"
                )

            execution.stage = "provider_fetch"
            provider_started = self._monotonic()
            batch = self._provider.fetch(
                MARKET_INTELLIGENCE_UNIVERSE, command.as_of
            )
            provider_finished = self._monotonic()
            provider_name = batch.provider
            provider_evidence = dict(batch.stage_timings or {})
            provider_evidence.setdefault(
                "provider_fetch_ms",
                elapsed_milliseconds(provider_started, provider_finished),
            )
            provider_evidence.setdefault("normalization_ms", 0.0)
            prepared = self._prepare(
                batch,
                sessions,
                command.as_of,
                provider_evidence=provider_evidence,
                execution=execution,
            )

            execution.stage = "persistence"
            with self._uow_factory() as uow:
                existing = uow.market_intelligence.find_exact(
                    prepared.idempotency_key
                )
                if existing is not None:
                    result = self._result_from_existing(existing)
                    self._log_run(
                        event="market_intelligence_run_reused",
                        command=command,
                        run_id=result.run_id,
                        provider=existing.audit.provider,
                        stage="reuse",
                        duration_ms=elapsed_milliseconds(
                            total_started, self._monotonic()
                        ),
                        publication_status=(
                            "PUBLISHED" if result.published else "NOT_PUBLISHED"
                        ),
                        retry_status=retry_status,
                        reuse_status="IDEMPOTENT_RESULT_REUSED",
                    )
                    return result

                previous = uow.market_intelligence.get_previous_published(
                    before=command.as_of,
                    metric_version=METRIC_VERSION,
                )
                previous_by_symbol = (
                    {snapshot.symbol: snapshot for snapshot in previous.snapshots}
                    if previous is not None
                    else {}
                )
                execution.stage = "calculation"
                candidate_started = self._monotonic()
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
                prepared.stage_timings["calculation_ms"] += elapsed_milliseconds(
                    candidate_started,
                    self._monotonic(),
                )
                audit = self._build_audit(prepared, candidate, command.as_of)
                execution.stage = "persistence"
                persistence_started = self._monotonic()
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
                        "pipeline_version": PIPELINE_VERSION,
                    },
                )
                run_id = run.id
                uow.market_intelligence.persist_candidate(
                    run.id,
                    audit,
                    prepared.validation.canonical_bars,
                    prepared.validation.rejections,
                    candidate.snapshots,
                )
                publication_status = self._persist_feature_run_lifecycle(
                    uow,
                    run.id,
                    candidate,
                    duration_seconds=0.0,
                )
                prepared.stage_timings["persistence_ms"] = elapsed_milliseconds(
                    persistence_started,
                    self._monotonic(),
                )
                if candidate.publishable:
                    execution.stage = "publication"
                    publication_started = self._monotonic()
                    uow.feature_runs.publish_atomically_if_not_older(
                        run.id,
                        LATEST_POINTER_KEY,
                    )
                    publication_status = "PUBLISHED"
                    prepared.stage_timings["publication_ms"] = (
                        elapsed_milliseconds(
                            publication_started,
                            self._monotonic(),
                        )
                    )
                execution.stage = "persistence"
                prepared.stage_timings["total_ms"] = elapsed_milliseconds(
                    total_started, self._monotonic()
                )
                final_timings = complete_stage_timings(prepared.stage_timings)
                final_stats = self._run_stats(
                    candidate,
                    duration_seconds=final_timings["total_ms"] / 1000.0,
                )
                execution.stage = "persistence"
                uow.feature_runs.update_stats(run.id, final_stats)
                uow.market_intelligence.update_observability(
                    run.id,
                    stage_timings=final_timings,
                    failure_category=audit.failure_category,
                    publication_status=publication_status,
                    retry_status=retry_status,
                    reuse_status=reuse_status,
                )
                uow.commit()
                result = BuildSectorSnapshotResult(
                    run_id=run.id,
                    ingestion_status=candidate.ingestion_status,
                    published=candidate.publishable,
                    idempotency_key=prepared.idempotency_key,
                    failure_category=audit.failure_category,
                )
                self._log_run(
                    event="market_intelligence_run_completed",
                    command=command,
                    run_id=run.id,
                    provider=batch.provider,
                    stage="completed",
                    duration_ms=final_timings["total_ms"],
                    publication_status=publication_status,
                    retry_status=retry_status,
                    reuse_status=reuse_status,
                    symbol_count=len(candidate.usable_symbols),
                    snapshot_count=len(candidate.snapshots),
                    failure_category=audit.failure_category,
                )
                return result
        except MarketIntelligenceIdempotencyConflict:
            with self._uow_factory() as winner_uow:
                winner = winner_uow.market_intelligence.find_exact(
                    prepared.idempotency_key
                )
                if winner is None:
                    raise
                return self._result_from_existing(winner)
        except Exception as exc:
            category = failure_category_for_exception(exc, stage=execution.stage)
            exc.market_intelligence_failure_category = category
            exc.market_intelligence_stage = execution.stage
            self._log_run(
                event="market_intelligence_run_failed",
                command=command,
                run_id=run_id,
                provider=provider_name,
                stage=execution.stage,
                duration_ms=elapsed_milliseconds(total_started, self._monotonic()),
                publication_status="FAILED",
                retry_status=retry_status,
                reuse_status=reuse_status,
                failure_category=category,
                level=logging.ERROR,
                exc_info=True,
            )
            raise

    def _prepare(
        self,
        batch: ProviderBatchResult,
        sessions: tuple[date, ...],
        as_of: date,
        *,
        provider_evidence: Mapping[str, float],
        execution: _ExecutionState,
    ) -> _PreparedAttempt:
        timestamp = self._clock()
        stage_timings = complete_stage_timings(provider_evidence)
        execution.stage = "validation"
        validation_started = self._monotonic()
        validation = (
            ValidationResult((), (), ())
            if batch.request_failure is not None
            else validate_provider_rows(batch.rows, sessions, timestamp)
        )
        stage_timings["validation_ms"] = elapsed_milliseconds(
            validation_started, self._monotonic()
        )
        execution.stage = "calculation"
        calculation_started = self._monotonic()
        metrics, history_counts = _calculate_metrics(validation, sessions)
        input_hash = _hash_payload(batch, sessions)
        idempotency_key = _idempotency_key(as_of, input_hash)
        if batch.request_failure is not None:
            idempotency_key = _request_failure_idempotency_key(
                as_of,
                input_hash,
                self._attempt_id_factory(),
            )
        source_freshness = _source_freshness(
            validation,
            as_of=as_of,
            response_timestamp=batch.response_timestamp,
        )
        stage_timings["calculation_ms"] = elapsed_milliseconds(
            calculation_started, self._monotonic()
        )
        return _PreparedAttempt(
            batch=batch,
            sessions=sessions,
            validation=validation,
            metrics_by_symbol=metrics,
            history_session_counts=history_counts,
            input_hash=input_hash,
            idempotency_key=idempotency_key,
            source_freshness=source_freshness,
            timestamp=timestamp,
            stage_timings=stage_timings,
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
        failure_category = BuildSectorSnapshotUseCase._candidate_failure_category(
            prepared,
            candidate,
        )
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
            pipeline_version=PIPELINE_VERSION,
            failure_category=failure_category,
            stage_timings=complete_stage_timings(prepared.stage_timings),
            publication_status="PENDING",
            retry_status=None,
            reuse_status=None,
        )

    @staticmethod
    def _candidate_failure_category(
        prepared: _PreparedAttempt,
        candidate: CandidateSnapshot,
    ) -> MarketIntelligenceErrorCategory | None:
        if prepared.batch.request_failure is not None:
            return failure_category_for_request(prepared.batch.request_failure.code)
        if prepared.validation.rejections:
            return MarketIntelligenceErrorCategory.INVALID_MARKET_DATA
        if prepared.batch.symbol_failures:
            return MarketIntelligenceErrorCategory.PROVIDER_FAILURE
        if prepared.source_freshness.get("stale_or_missing_symbols"):
            return MarketIntelligenceErrorCategory.STALE_DATA
        if candidate.ingestion_status is not IngestionStatus.SUCCEEDED:
            return MarketIntelligenceErrorCategory.INSUFFICIENT_HISTORY
        return None

    @staticmethod
    def _retry_status(command: BuildSectorSnapshotCommand) -> str:
        if command.broker_redelivered:
            return "BROKER_REDELIVERED"
        if command.retry_count > 0:
            return "RETRY"
        return "INITIAL"

    @staticmethod
    def _run_stats(
        candidate: CandidateSnapshot,
        *,
        duration_seconds: float,
    ) -> RunStats:
        usable_count = len(candidate.usable_symbols)
        return RunStats(
            total_symbols=len(MARKET_INTELLIGENCE_UNIVERSE),
            processed_symbols=usable_count,
            failed_symbols=len(MARKET_INTELLIGENCE_UNIVERSE) - usable_count,
            duration_seconds=duration_seconds,
            passed_symbols=usable_count,
        )

    @classmethod
    def _persist_feature_run_lifecycle(
        cls,
        uow: Any,
        run_id: int,
        candidate: CandidateSnapshot,
        *,
        duration_seconds: float,
    ) -> str:
        usable_count = len(candidate.usable_symbols)
        stats = cls._run_stats(
            candidate,
            duration_seconds=duration_seconds,
        )
        if candidate.ingestion_status is IngestionStatus.FAILED:
            uow.feature_runs.mark_failed(run_id, stats)
            return "FAILED"
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
            return "QUARANTINED"
        return "PENDING_PUBLICATION"

    @staticmethod
    def _log_run(
        *,
        event: str,
        command: BuildSectorSnapshotCommand,
        run_id: int | None,
        provider: str | None,
        stage: str,
        duration_ms: float,
        publication_status: str,
        retry_status: str,
        reuse_status: str,
        symbol_count: int | None = None,
        snapshot_count: int | None = None,
        failure_category: MarketIntelligenceErrorCategory | None = None,
        level: int = logging.INFO,
        exc_info: bool = False,
    ) -> None:
        logger.log(
            level,
            event,
            extra={
                "event": event,
                "task_id": None,
                "run_id": run_id,
                "as_of_date": command.as_of.isoformat(),
                "pipeline_version": PIPELINE_VERSION,
                "metric_version": METRIC_VERSION,
                "provider": provider,
                "stage": stage,
                "duration_ms": duration_ms,
                "symbol_count": symbol_count,
                "snapshot_count": snapshot_count,
                "publication_status": publication_status,
                "retry_status": retry_status,
                "reuse_status": reuse_status,
                "failure_category": (
                    None if failure_category is None else failure_category.value
                ),
            },
            exc_info=exc_info,
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
            reused=True,
            failure_category=bundle.audit.failure_category,
        )
