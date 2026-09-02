"""Add Phase 1 Market Intelligence audit, lineage, quarantine, and snapshots.

Revision ID: 20260826_0031
Revises: 20260823_0030
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260826_0031"
down_revision = "20260823_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_intelligence_run_audits",
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("feature_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("ingestion_status", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_status", sa.String(length=32), nullable=False),
        sa.Column("request_failure_json", sa.JSON(), nullable=True),
        sa.Column("metric_version", sa.String(length=64), nullable=False),
        sa.Column("normalization_version", sa.String(length=64), nullable=False),
        sa.Column("price_basis", sa.String(length=64), nullable=False),
        sa.Column("target_session", sa.Date(), nullable=False),
        sa.Column("counters_json", sa.JSON(), nullable=False),
        sa.Column("missing_symbols_json", sa.JSON(), nullable=False),
        sa.Column("provider_failures_json", sa.JSON(), nullable=False),
        sa.Column("provider_response_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_freshness_json", sa.JSON(), nullable=False),
        sa.Column("calculation_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingestion_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "ingestion_status IN ('SUCCEEDED', 'PARTIAL', 'FAILED')",
            name="ck_mi_run_audit_ingestion_status",
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_mi_run_audit_idempotency_key"
        ),
    )
    op.create_index(
        "ix_mi_run_audit_latest_attempt",
        "market_intelligence_run_audits",
        ["target_session", "created_at", "run_id"],
    )
    op.create_index(
        "ix_mi_run_audit_session_metric",
        "market_intelligence_run_audits",
        ["target_session", "metric_version"],
    )

    op.create_table(
        "market_intelligence_canonical_bars",
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("feature_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("symbol", sa.String(length=8), primary_key=True),
        sa.Column("trading_date", sa.Date(), primary_key=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_symbol", sa.String(length=32), nullable=False),
        sa.Column("raw_trading_date", sa.Text(), nullable=False),
        sa.Column("raw_open", sa.Numeric(24, 10), nullable=False),
        sa.Column("raw_high", sa.Numeric(24, 10), nullable=False),
        sa.Column("raw_low", sa.Numeric(24, 10), nullable=False),
        sa.Column("raw_close", sa.Numeric(24, 10), nullable=False),
        sa.Column("provider_adjusted_close", sa.Numeric(24, 10), nullable=False),
        sa.Column("adjustment_factor", sa.Numeric(24, 10), nullable=False),
        sa.Column("adjusted_open", sa.Numeric(24, 10), nullable=False),
        sa.Column("adjusted_high", sa.Numeric(24, 10), nullable=False),
        sa.Column("adjusted_low", sa.Numeric(24, 10), nullable=False),
        sa.Column("adjusted_close", sa.Numeric(24, 10), nullable=False),
        sa.Column("provider_volume", sa.Numeric(24, 10), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingestion_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price_basis", sa.String(length=64), nullable=False),
        sa.Column("normalization_version", sa.String(length=64), nullable=False),
    )
    op.create_index(
        "ix_mi_canonical_bar_symbol_date",
        "market_intelligence_canonical_bars",
        ["symbol", "trading_date"],
    )

    op.create_table(
        "market_intelligence_rejections",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("feature_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_symbol", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=8), nullable=True),
        sa.Column("trading_date", sa.Date(), nullable=True),
        sa.Column("rejection_code", sa.String(length=40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("raw_evidence_json", sa.JSON(), nullable=False),
        sa.Column("ingestion_timestamp", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_mi_rejection_run_code",
        "market_intelligence_rejections",
        ["run_id", "rejection_code"],
    )

    op.create_table(
        "market_intelligence_sector_snapshots",
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("feature_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("symbol", sa.String(length=8), primary_key=True),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("asset_type", sa.String(length=24), nullable=False),
        sa.Column("sector_name", sa.Text(), nullable=True),
        sa.Column("return_1d", sa.Float(), nullable=True),
        sa.Column("return_5d", sa.Float(), nullable=True),
        sa.Column("return_20d", sa.Float(), nullable=True),
        sa.Column("return_60d", sa.Float(), nullable=True),
        sa.Column("relative_return_vs_spy_1d", sa.Float(), nullable=True),
        sa.Column("relative_return_vs_spy_5d", sa.Float(), nullable=True),
        sa.Column("relative_return_vs_spy_20d", sa.Float(), nullable=True),
        sa.Column("relative_return_vs_spy_60d", sa.Float(), nullable=True),
        sa.Column("rvol20", sa.Float(), nullable=True),
        sa.Column("flow_pressure_1d_proxy", sa.Float(), nullable=True),
        sa.Column("cmf_5d_proxy", sa.Float(), nullable=True),
        sa.Column("cmf_20d_proxy", sa.Float(), nullable=True),
        sa.Column("cmf_60d_proxy", sa.Float(), nullable=True),
        sa.Column("current_ranks_json", sa.JSON(), nullable=False),
        sa.Column("previous_ranks_json", sa.JSON(), nullable=False),
        sa.Column("rank_changes_json", sa.JSON(), nullable=False),
        sa.Column("rank_directions_json", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("source_freshness_json", sa.JSON(), nullable=False),
        sa.Column("price_basis", sa.String(length=64), nullable=False),
        sa.Column("metric_version", sa.String(length=64), nullable=False),
        sa.Column("calculation_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_quality_status", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "asset_type IN ('benchmark_etf', 'sector_etf')",
            name="ck_mi_snapshot_asset_type",
        ),
        sa.CheckConstraint(
            "data_quality_status IN ('COMPLETE', 'INCOMPLETE')",
            name="ck_mi_snapshot_data_quality_status",
        ),
        sa.UniqueConstraint(
            "run_id", "symbol", name="uq_mi_snapshot_run_symbol"
        ),
    )
    op.create_index(
        "ix_mi_snapshot_date_metric",
        "market_intelligence_sector_snapshots",
        ["trading_date", "metric_version"],
    )
    op.create_index(
        "ix_mi_snapshot_symbol_date",
        "market_intelligence_sector_snapshots",
        ["symbol", "trading_date"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mi_snapshot_symbol_date",
        table_name="market_intelligence_sector_snapshots",
    )
    op.drop_index(
        "ix_mi_snapshot_date_metric",
        table_name="market_intelligence_sector_snapshots",
    )
    op.drop_table("market_intelligence_sector_snapshots")

    op.drop_index(
        "ix_mi_rejection_run_code",
        table_name="market_intelligence_rejections",
    )
    op.drop_table("market_intelligence_rejections")

    op.drop_index(
        "ix_mi_canonical_bar_symbol_date",
        table_name="market_intelligence_canonical_bars",
    )
    op.drop_table("market_intelligence_canonical_bars")

    op.drop_index(
        "ix_mi_run_audit_session_metric",
        table_name="market_intelligence_run_audits",
    )
    op.drop_index(
        "ix_mi_run_audit_latest_attempt",
        table_name="market_intelligence_run_audits",
    )
    op.drop_table("market_intelligence_run_audits")
