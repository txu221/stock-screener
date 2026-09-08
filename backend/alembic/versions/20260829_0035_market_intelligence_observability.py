"""Add persisted Market Intelligence pipeline observability.

Revision ID: 20260829_0035
Revises: 20260828_0034
Create Date: 2026-08-29
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op


revision = "20260829_0035"
down_revision = "20260828_0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "market_intelligence_run_audits",
        sa.Column("pipeline_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "market_intelligence_run_audits",
        sa.Column("failure_category", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "market_intelligence_run_audits",
        sa.Column("stage_timings_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "market_intelligence_run_audits",
        sa.Column("publication_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "market_intelligence_run_audits",
        sa.Column("retry_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "market_intelligence_run_audits",
        sa.Column("reuse_status", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("market_intelligence_run_audits", "reuse_status")
    op.drop_column("market_intelligence_run_audits", "retry_status")
    op.drop_column("market_intelligence_run_audits", "publication_status")
    op.drop_column("market_intelligence_run_audits", "stage_timings_json")
    op.drop_column("market_intelligence_run_audits", "failure_category")
    op.drop_column("market_intelligence_run_audits", "pipeline_version")
