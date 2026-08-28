"""Merge the breadth and Market Intelligence migration branches.

Revision ID: 20260828_0033
Revises: 20260825_0032, 20260826_0031
Create Date: 2026-08-28
"""

from __future__ import annotations


revision = "20260828_0033"
down_revision = ("20260825_0032", "20260826_0031")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join the two additive schema branches without changing data."""


def downgrade() -> None:
    """Expose both parent heads again without changing their schemas."""
