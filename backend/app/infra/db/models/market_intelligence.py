"""Additive persistence models for Phase 1 sector intelligence."""

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from app.database import Base


class MarketIntelligenceRunAudit(Base):
    __tablename__ = "market_intelligence_run_audits"

    run_id = Column(
        Integer,
        ForeignKey("feature_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    idempotency_key = Column(String(64), nullable=False)
    input_hash = Column(String(64), nullable=False)
    ingestion_status = Column(String(16), nullable=False)
    provider = Column(String(32), nullable=False)
    provider_status = Column(String(32), nullable=False)
    request_failure_json = Column(JSON, nullable=True)
    metric_version = Column(String(64), nullable=False)
    normalization_version = Column(String(64), nullable=False)
    price_basis = Column(String(64), nullable=False)
    target_session = Column(Date, nullable=False)
    counters_json = Column(JSON, nullable=False)
    missing_symbols_json = Column(JSON, nullable=False)
    provider_failures_json = Column(JSON, nullable=False)
    provider_response_at = Column(DateTime(timezone=True), nullable=True)
    source_freshness_json = Column(JSON, nullable=False)
    calculation_timestamp = Column(DateTime(timezone=True), nullable=False)
    ingestion_timestamp = Column(DateTime(timezone=True), nullable=False)
    pipeline_version = Column(String(64), nullable=True)
    failure_category = Column(String(64), nullable=True)
    stage_timings_json = Column(JSON, nullable=True)
    publication_status = Column(String(32), nullable=True)
    retry_status = Column(String(32), nullable=True)
    reuse_status = Column(String(32), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "ingestion_status IN ('SUCCEEDED', 'PARTIAL', 'FAILED')",
            name="ck_mi_run_audit_ingestion_status",
        ),
        UniqueConstraint(
            "idempotency_key", name="uq_mi_run_audit_idempotency_key"
        ),
        Index(
            "ix_mi_run_audit_latest_attempt",
            "target_session",
            "created_at",
            "run_id",
        ),
        Index(
            "ix_mi_run_audit_session_metric",
            "target_session",
            "metric_version",
        ),
    )


class MarketIntelligenceCanonicalBar(Base):
    __tablename__ = "market_intelligence_canonical_bars"

    run_id = Column(
        Integer,
        ForeignKey("feature_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    symbol = Column(String(8), primary_key=True)
    trading_date = Column(Date, primary_key=True)
    provider = Column(String(32), nullable=False)
    provider_symbol = Column(String(32), nullable=False)
    raw_trading_date = Column(Text, nullable=False)
    raw_open = Column(Numeric(24, 10), nullable=False)
    raw_high = Column(Numeric(24, 10), nullable=False)
    raw_low = Column(Numeric(24, 10), nullable=False)
    raw_close = Column(Numeric(24, 10), nullable=False)
    provider_adjusted_close = Column(Numeric(24, 10), nullable=False)
    adjustment_factor = Column(Numeric(24, 10), nullable=False)
    adjusted_open = Column(Numeric(24, 10), nullable=False)
    adjusted_high = Column(Numeric(24, 10), nullable=False)
    adjusted_low = Column(Numeric(24, 10), nullable=False)
    adjusted_close = Column(Numeric(24, 10), nullable=False)
    provider_volume = Column(Numeric(24, 10), nullable=False)
    dividend_cash = Column(Numeric(24, 10), nullable=True)
    split_ratio = Column(Numeric(24, 10), nullable=True)
    source_timestamp = Column(DateTime(timezone=True), nullable=True)
    ingestion_timestamp = Column(DateTime(timezone=True), nullable=False)
    price_basis = Column(String(64), nullable=False)
    normalization_version = Column(String(64), nullable=False)

    __table_args__ = (
        Index("ix_mi_canonical_bar_symbol_date", "symbol", "trading_date"),
    )


class MarketIntelligenceRejection(Base):
    __tablename__ = "market_intelligence_rejections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(
        Integer,
        ForeignKey("feature_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider = Column(String(32), nullable=False)
    provider_symbol = Column(String(32), nullable=False)
    symbol = Column(String(8), nullable=True)
    trading_date = Column(Date, nullable=True)
    rejection_code = Column(String(40), nullable=False)
    reason = Column(Text, nullable=False)
    raw_evidence_json = Column(JSON, nullable=False)
    ingestion_timestamp = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_mi_rejection_run_code", "run_id", "rejection_code"),
    )


class MarketIntelligenceSectorSnapshot(Base):
    __tablename__ = "market_intelligence_sector_snapshots"

    run_id = Column(
        Integer,
        ForeignKey("feature_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    symbol = Column(String(8), primary_key=True)
    trading_date = Column(Date, nullable=False)
    asset_type = Column(String(24), nullable=False)
    sector_name = Column(Text, nullable=True)
    return_1d = Column(Float, nullable=True)
    return_5d = Column(Float, nullable=True)
    return_20d = Column(Float, nullable=True)
    return_60d = Column(Float, nullable=True)
    relative_return_vs_spy_1d = Column(Float, nullable=True)
    relative_return_vs_spy_5d = Column(Float, nullable=True)
    relative_return_vs_spy_20d = Column(Float, nullable=True)
    relative_return_vs_spy_60d = Column(Float, nullable=True)
    rvol20 = Column(Float, nullable=True)
    flow_pressure_1d_proxy = Column(Float, nullable=True)
    cmf_5d_proxy = Column(Float, nullable=True)
    cmf_20d_proxy = Column(Float, nullable=True)
    cmf_60d_proxy = Column(Float, nullable=True)
    current_ranks_json = Column(JSON, nullable=False)
    previous_ranks_json = Column(JSON, nullable=False)
    rank_changes_json = Column(JSON, nullable=False)
    rank_directions_json = Column(JSON, nullable=False)
    provider = Column(String(32), nullable=False)
    source_freshness_json = Column(JSON, nullable=False)
    price_basis = Column(String(64), nullable=False)
    metric_version = Column(String(64), nullable=False)
    calculation_timestamp = Column(DateTime(timezone=True), nullable=False)
    data_quality_status = Column(String(16), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "asset_type IN ('benchmark_etf', 'sector_etf')",
            name="ck_mi_snapshot_asset_type",
        ),
        CheckConstraint(
            "data_quality_status IN ('COMPLETE', 'INCOMPLETE')",
            name="ck_mi_snapshot_data_quality_status",
        ),
        UniqueConstraint(
            "run_id", "symbol", name="uq_mi_snapshot_run_symbol"
        ),
        Index(
            "ix_mi_snapshot_date_metric",
            "trading_date",
            "metric_version",
        ),
        Index("ix_mi_snapshot_symbol_date", "symbol", "trading_date"),
    )
