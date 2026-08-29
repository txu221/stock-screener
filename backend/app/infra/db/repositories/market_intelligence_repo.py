"""SQLAlchemy repository for Phase 1 sector-intelligence evidence."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.feature_store.models import RunStatus
from app.domain.market_intelligence.models import (
    BarRejection,
    CanonicalBar,
    IngestionStatus,
    MarketIntelligenceRunBundle,
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
    MarketIntelligenceRepository,
)
from app.infra.db.models.feature_store import FeatureRun, FeatureRunPointer
from app.infra.db.models.market_intelligence import (
    MarketIntelligenceCanonicalBar,
    MarketIntelligenceRejection,
    MarketIntelligenceRunAudit,
    MarketIntelligenceSectorSnapshot,
)

_METRIC_FIELDS = (
    "return_1d",
    "return_5d",
    "return_20d",
    "return_60d",
    "relative_return_vs_spy_1d",
    "relative_return_vs_spy_5d",
    "relative_return_vs_spy_20d",
    "relative_return_vs_spy_60d",
    "rvol20",
    "flow_pressure_1d_proxy",
    "cmf_5d_proxy",
    "cmf_20d_proxy",
    "cmf_60d_proxy",
)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _is_idempotency_conflict(exc: IntegrityError) -> bool:
    original = getattr(exc, "orig", None)
    diagnostics = getattr(original, "diag", None)
    if (
        getattr(diagnostics, "constraint_name", None)
        == "uq_mi_run_audit_idempotency_key"
    ):
        return True
    message = str(original or exc).lower()
    return (
        "uq_mi_run_audit_idempotency_key" in message
        or "market_intelligence_run_audits.idempotency_key" in message
    )


class SqlMarketIntelligenceRepository(MarketIntelligenceRepository):
    """Persist evidence in the caller-owned Unit of Work transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def persist_candidate(
        self,
        run_id: int,
        audit: RunAudit,
        canonical_bars: Sequence[CanonicalBar],
        rejections: Sequence[BarRejection],
        snapshots: Sequence[SectorSnapshot],
    ) -> None:
        self._session.add(self._audit_row(run_id, audit))
        try:
            self._session.flush()
        except IntegrityError as exc:
            if _is_idempotency_conflict(exc):
                raise MarketIntelligenceIdempotencyConflict from exc
            raise
        self._session.add_all(
            self._canonical_row(run_id, bar) for bar in canonical_bars
        )
        self._session.add_all(
            self._rejection_row(run_id, rejection) for rejection in rejections
        )
        self._session.add_all(
            self._snapshot_row(run_id, snapshot) for snapshot in snapshots
        )
        self._session.flush()

    def find_exact(self, idempotency_key: str) -> MarketIntelligenceRunBundle | None:
        row = (
            self._session.query(MarketIntelligenceRunAudit)
            .filter(MarketIntelligenceRunAudit.idempotency_key == idempotency_key)
            .one_or_none()
        )
        return None if row is None else self._load_bundle(row.run_id)

    def get_previous_published(
        self,
        *,
        before: date,
        metric_version: str,
    ) -> MarketIntelligenceRunBundle | None:
        run_id = (
            self._session.query(FeatureRun.id)
            .join(
                MarketIntelligenceRunAudit,
                MarketIntelligenceRunAudit.run_id == FeatureRun.id,
            )
            .filter(
                FeatureRun.status == RunStatus.PUBLISHED.value,
                FeatureRun.as_of_date < before,
                MarketIntelligenceRunAudit.metric_version == metric_version,
                MarketIntelligenceRunAudit.ingestion_status
                == IngestionStatus.SUCCEEDED.value,
            )
            .order_by(
                FeatureRun.as_of_date.desc(),
                FeatureRun.published_at.desc(),
                FeatureRun.id.desc(),
            )
            .limit(1)
            .scalar()
        )
        return (
            None
            if run_id is None
            else self._load_bundle(run_id, include_evidence=False)
        )

    def get_latest_attempt(self) -> MarketIntelligenceRunBundle | None:
        run_id = (
            self._session.query(FeatureRun.id)
            .join(
                MarketIntelligenceRunAudit,
                MarketIntelligenceRunAudit.run_id == FeatureRun.id,
            )
            .order_by(FeatureRun.created_at.desc(), FeatureRun.id.desc())
            .limit(1)
            .scalar()
        )
        return (
            None
            if run_id is None
            else self._load_bundle(run_id, include_evidence=False)
        )

    def get_latest_published(
        self,
        pointer_key: str,
    ) -> MarketIntelligenceRunBundle | None:
        run_id = (
            self._session.query(FeatureRunPointer.run_id)
            .join(FeatureRun, FeatureRun.id == FeatureRunPointer.run_id)
            .join(
                MarketIntelligenceRunAudit,
                MarketIntelligenceRunAudit.run_id == FeatureRun.id,
            )
            .filter(
                FeatureRunPointer.key == pointer_key,
                FeatureRun.status == RunStatus.PUBLISHED.value,
                MarketIntelligenceRunAudit.ingestion_status
                == IngestionStatus.SUCCEEDED.value,
            )
            .scalar()
        )
        return (
            None
            if run_id is None
            else self._load_bundle(run_id, include_evidence=False)
        )

    def list_published_history(
        self,
        *,
        metric_version: str,
        symbol: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 60,
    ) -> tuple[MarketIntelligenceRunBundle, ...]:
        if limit <= 0:
            return ()
        query = (
            self._session.query(FeatureRun.id, FeatureRun.as_of_date)
            .join(
                MarketIntelligenceRunAudit,
                MarketIntelligenceRunAudit.run_id == FeatureRun.id,
            )
            .filter(
                FeatureRun.status == RunStatus.PUBLISHED.value,
                MarketIntelligenceRunAudit.ingestion_status
                == IngestionStatus.SUCCEEDED.value,
                MarketIntelligenceRunAudit.metric_version == metric_version,
            )
        )
        if symbol is not None:
            query = query.join(
                MarketIntelligenceSectorSnapshot,
                MarketIntelligenceSectorSnapshot.run_id == FeatureRun.id,
            ).filter(MarketIntelligenceSectorSnapshot.symbol == symbol.upper())
        if date_from is not None:
            query = query.filter(FeatureRun.as_of_date >= date_from)
        if date_to is not None:
            query = query.filter(FeatureRun.as_of_date <= date_to)
        revisions = query.order_by(
            FeatureRun.as_of_date.desc(),
            FeatureRun.published_at.desc(),
            FeatureRun.id.desc(),
        ).all()

        bundles: list[MarketIntelligenceRunBundle] = []
        seen_sessions: set[date] = set()
        for run_id, as_of_date in revisions:
            if as_of_date in seen_sessions:
                continue
            seen_sessions.add(as_of_date)
            bundles.append(
                self._load_bundle(
                    run_id,
                    symbol=symbol,
                    include_evidence=False,
                )
            )
            if len(bundles) == limit:
                break
        return tuple(bundles)

    def _load_bundle(
        self,
        run_id: int,
        *,
        symbol: str | None = None,
        include_evidence: bool = True,
    ) -> MarketIntelligenceRunBundle:
        run = self._session.get(FeatureRun, run_id)
        audit = self._session.get(MarketIntelligenceRunAudit, run_id)
        if run is None or audit is None:
            raise LookupError(f"market-intelligence run {run_id} is incomplete")

        canonical_rows = []
        rejection_rows = []
        if include_evidence:
            canonical_rows = (
                self._session.query(MarketIntelligenceCanonicalBar)
                .filter(MarketIntelligenceCanonicalBar.run_id == run_id)
                .order_by(
                    MarketIntelligenceCanonicalBar.symbol,
                    MarketIntelligenceCanonicalBar.trading_date,
                )
                .all()
            )
            rejection_rows = (
                self._session.query(MarketIntelligenceRejection)
                .filter(MarketIntelligenceRejection.run_id == run_id)
                .order_by(MarketIntelligenceRejection.id)
                .all()
            )
        snapshot_query = self._session.query(
            MarketIntelligenceSectorSnapshot
        ).filter(MarketIntelligenceSectorSnapshot.run_id == run_id)
        if symbol is not None:
            snapshot_query = snapshot_query.filter(
                MarketIntelligenceSectorSnapshot.symbol == symbol.upper()
            )
        snapshot_rows = snapshot_query.order_by(
            MarketIntelligenceSectorSnapshot.symbol
        ).all()

        return MarketIntelligenceRunBundle(
            run_id=run.id,
            as_of_date=run.as_of_date,
            lifecycle_status=run.status,
            created_at=_aware(run.created_at),
            published_at=_aware(run.published_at),
            audit=self._to_audit(audit),
            canonical_bars=tuple(self._to_canonical(row) for row in canonical_rows),
            rejections=tuple(self._to_rejection(row) for row in rejection_rows),
            snapshots=tuple(self._to_snapshot(row) for row in snapshot_rows),
        )

    @staticmethod
    def _audit_row(run_id: int, audit: RunAudit) -> MarketIntelligenceRunAudit:
        request_failure = None
        if audit.request_failure is not None:
            request_failure = {
                "code": audit.request_failure.code,
                "message": audit.request_failure.message,
            }
        return MarketIntelligenceRunAudit(
            run_id=run_id,
            idempotency_key=audit.idempotency_key,
            input_hash=audit.input_hash,
            ingestion_status=audit.ingestion_status.value,
            provider=audit.provider,
            provider_status=audit.provider_status,
            request_failure_json=request_failure,
            metric_version=audit.metric_version,
            normalization_version=audit.normalization_version,
            price_basis=audit.price_basis,
            target_session=audit.target_session,
            counters_json=dict(audit.counters),
            missing_symbols_json=list(audit.missing_symbols),
            provider_failures_json=[
                {"symbol": failure.symbol, "code": failure.code, "message": failure.message}
                for failure in audit.provider_failures
            ],
            provider_response_at=audit.provider_response_at,
            source_freshness_json=dict(audit.source_freshness),
            calculation_timestamp=audit.calculation_timestamp,
            ingestion_timestamp=audit.ingestion_timestamp,
        )

    @staticmethod
    def _canonical_row(
        run_id: int, bar: CanonicalBar
    ) -> MarketIntelligenceCanonicalBar:
        return MarketIntelligenceCanonicalBar(
            run_id=run_id,
            symbol=bar.symbol,
            trading_date=bar.trading_date,
            provider=bar.provider,
            provider_symbol=bar.provider_symbol,
            raw_trading_date=str(bar.raw_trading_date),
            raw_open=bar.raw_open,
            raw_high=bar.raw_high,
            raw_low=bar.raw_low,
            raw_close=bar.raw_close,
            provider_adjusted_close=bar.provider_adjusted_close,
            adjustment_factor=bar.adjustment_factor,
            adjusted_open=bar.adjusted_open,
            adjusted_high=bar.adjusted_high,
            adjusted_low=bar.adjusted_low,
            adjusted_close=bar.adjusted_close,
            provider_volume=bar.provider_volume,
            dividend_cash=bar.dividend_cash,
            split_ratio=bar.split_ratio,
            source_timestamp=bar.source_timestamp,
            ingestion_timestamp=bar.ingestion_timestamp,
            price_basis=bar.price_basis,
            normalization_version=bar.normalization_version,
        )

    @staticmethod
    def _rejection_row(
        run_id: int, rejection: BarRejection
    ) -> MarketIntelligenceRejection:
        return MarketIntelligenceRejection(
            run_id=run_id,
            provider=rejection.provider,
            provider_symbol=rejection.provider_symbol,
            symbol=rejection.symbol,
            trading_date=rejection.trading_date,
            rejection_code=rejection.code.value,
            reason=rejection.reason,
            raw_evidence_json=dict(rejection.raw_evidence),
            ingestion_timestamp=rejection.ingestion_timestamp,
        )

    @staticmethod
    def _snapshot_row(
        run_id: int, snapshot: SectorSnapshot
    ) -> MarketIntelligenceSectorSnapshot:
        ranks = snapshot.ranks
        values: dict[str, Any] = {
            name: getattr(snapshot.metrics, name) for name in _METRIC_FIELDS
        }
        return MarketIntelligenceSectorSnapshot(
            run_id=run_id,
            symbol=snapshot.symbol,
            trading_date=snapshot.trading_date,
            asset_type=snapshot.asset_type,
            sector_name=snapshot.sector_name,
            **values,
            current_ranks_json={name: rank.current_rank for name, rank in ranks.items()},
            previous_ranks_json={name: rank.previous_rank for name, rank in ranks.items()},
            rank_changes_json={name: rank.rank_change for name, rank in ranks.items()},
            rank_directions_json={name: rank.rank_direction.value for name, rank in ranks.items()},
            provider=snapshot.provider,
            source_freshness_json=dict(snapshot.source_freshness),
            price_basis=snapshot.price_basis,
            metric_version=snapshot.metric_version,
            calculation_timestamp=snapshot.calculation_timestamp,
            data_quality_status=snapshot.data_quality_status,
        )

    @staticmethod
    def _to_audit(row: MarketIntelligenceRunAudit) -> RunAudit:
        request_failure = None
        if row.request_failure_json is not None:
            request_failure = RequestFailure(**row.request_failure_json)
        return RunAudit(
            idempotency_key=row.idempotency_key,
            input_hash=row.input_hash,
            ingestion_status=IngestionStatus(row.ingestion_status),
            provider=row.provider,
            provider_status=row.provider_status,
            request_failure=request_failure,
            metric_version=row.metric_version,
            normalization_version=row.normalization_version,
            price_basis=row.price_basis,
            counters=dict(row.counters_json),
            missing_symbols=tuple(row.missing_symbols_json),
            provider_failures=tuple(
                ProviderSymbolFailure(**failure)
                for failure in row.provider_failures_json
            ),
            target_session=row.target_session,
            provider_response_at=_aware(row.provider_response_at),
            source_freshness=dict(row.source_freshness_json),
            calculation_timestamp=_aware(row.calculation_timestamp),
            ingestion_timestamp=_aware(row.ingestion_timestamp),
        )

    @staticmethod
    def _to_canonical(row: MarketIntelligenceCanonicalBar) -> CanonicalBar:
        return CanonicalBar(
            provider=row.provider,
            provider_symbol=row.provider_symbol,
            symbol=row.symbol,
            raw_trading_date=row.raw_trading_date,
            trading_date=row.trading_date,
            raw_open=float(row.raw_open),
            raw_high=float(row.raw_high),
            raw_low=float(row.raw_low),
            raw_close=float(row.raw_close),
            provider_adjusted_close=float(row.provider_adjusted_close),
            adjustment_factor=float(row.adjustment_factor),
            adjusted_open=float(row.adjusted_open),
            adjusted_high=float(row.adjusted_high),
            adjusted_low=float(row.adjusted_low),
            adjusted_close=float(row.adjusted_close),
            provider_volume=float(row.provider_volume),
            dividend_cash=(
                None if row.dividend_cash is None else float(row.dividend_cash)
            ),
            split_ratio=(
                None if row.split_ratio is None else float(row.split_ratio)
            ),
            source_timestamp=_aware(row.source_timestamp),
            ingestion_timestamp=_aware(row.ingestion_timestamp),
            price_basis=row.price_basis,
            normalization_version=row.normalization_version,
        )

    @staticmethod
    def _to_rejection(row: MarketIntelligenceRejection) -> BarRejection:
        return BarRejection(
            provider=row.provider,
            provider_symbol=row.provider_symbol,
            symbol=row.symbol,
            trading_date=row.trading_date,
            code=RejectionCode(row.rejection_code),
            reason=row.reason,
            raw_evidence=dict(row.raw_evidence_json),
            ingestion_timestamp=_aware(row.ingestion_timestamp),
        )

    @staticmethod
    def _to_snapshot(row: MarketIntelligenceSectorSnapshot) -> SectorSnapshot:
        metric_values = {name: getattr(row, name) for name in _METRIC_FIELDS}
        ranks = {
            name: RankRecord(
                current_rank=current_rank,
                previous_rank=row.previous_ranks_json.get(name),
                rank_change=row.rank_changes_json.get(name),
                rank_direction=RankDirection(row.rank_directions_json[name]),
            )
            for name, current_rank in row.current_ranks_json.items()
        }
        return SectorSnapshot(
            trading_date=row.trading_date,
            symbol=row.symbol,
            asset_type=row.asset_type,
            sector_name=row.sector_name,
            metrics=SectorMetrics(**metric_values),
            ranks=ranks,
            provider=row.provider,
            source_freshness=dict(row.source_freshness_json),
            price_basis=row.price_basis,
            metric_version=row.metric_version,
            calculation_timestamp=_aware(row.calculation_timestamp),
            data_quality_status=row.data_quality_status,
        )
