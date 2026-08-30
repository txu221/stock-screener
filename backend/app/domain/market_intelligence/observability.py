"""Stable observability vocabulary for the Market Intelligence pipeline."""

from __future__ import annotations

import math
from collections.abc import Mapping
from enum import Enum


PIPELINE_VERSION = "market_intelligence_pipeline_v2"

MARKET_INTELLIGENCE_STAGE_TIMING_KEYS = (
    "provider_fetch_ms",
    "normalization_ms",
    "validation_ms",
    "calculation_ms",
    "persistence_ms",
    "publication_ms",
    "total_ms",
)


class MarketIntelligenceErrorCategory(str, Enum):
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    PROVIDER_SCHEMA_DRIFT = "PROVIDER_SCHEMA_DRIFT"
    INVALID_MARKET_DATA = "INVALID_MARKET_DATA"
    DATABASE_FAILURE = "DATABASE_FAILURE"
    LOCK_TIMEOUT = "LOCK_TIMEOUT"
    PUBLICATION_FAILURE = "PUBLICATION_FAILURE"
    CELERY_DELIVERY_FAILURE = "CELERY_DELIVERY_FAILURE"
    STALE_DATA = "STALE_DATA"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    CORPORATE_ACTION_RECONCILIATION_FAILURE = (
        "CORPORATE_ACTION_RECONCILIATION_FAILURE"
    )


class InsufficientMarketHistoryError(ValueError):
    """The completed-session input cannot satisfy the pipeline contract."""


def elapsed_milliseconds(start: float, end: float) -> float:
    """Convert monotonic evidence to finite, nonnegative milliseconds."""
    value = (end - start) * 1000.0
    if not math.isfinite(value) or value < 0:
        raise ValueError("stage timing evidence must be finite and nonnegative")
    return value


def complete_stage_timings(
    evidence: Mapping[str, float] | None = None,
) -> dict[str, float]:
    """Return the complete ordered timing contract, rejecting invalid evidence."""
    supplied = evidence or {}
    unknown = set(supplied).difference(MARKET_INTELLIGENCE_STAGE_TIMING_KEYS)
    if unknown:
        raise ValueError(f"unknown stage timing keys: {sorted(unknown)}")
    completed: dict[str, float] = {}
    for key in MARKET_INTELLIGENCE_STAGE_TIMING_KEYS:
        value = float(supplied.get(key, 0.0))
        if not math.isfinite(value) or value < 0:
            raise ValueError("stage timing evidence must be finite and nonnegative")
        completed[key] = value
    return completed


def failure_category_for_request(
    request_failure_code: str,
) -> MarketIntelligenceErrorCategory:
    if request_failure_code == "PROVIDER_SCHEMA_DRIFT":
        return MarketIntelligenceErrorCategory.PROVIDER_SCHEMA_DRIFT
    if request_failure_code == "CORPORATE_ACTION_RECONCILIATION_FAILURE":
        return MarketIntelligenceErrorCategory.CORPORATE_ACTION_RECONCILIATION_FAILURE
    return MarketIntelligenceErrorCategory.PROVIDER_FAILURE


def failure_category_for_exception(
    error: BaseException,
    *,
    stage: str,
) -> MarketIntelligenceErrorCategory:
    """Map infrastructure exceptions without making exception text the taxonomy."""
    if isinstance(error, InsufficientMarketHistoryError):
        return MarketIntelligenceErrorCategory.INSUFFICIENT_HISTORY
    if stage == "celery_delivery":
        return MarketIntelligenceErrorCategory.CELERY_DELIVERY_FAILURE
    if stage == "provider_fetch":
        return MarketIntelligenceErrorCategory.PROVIDER_FAILURE
    if stage == "corporate_action_reconciliation":
        return MarketIntelligenceErrorCategory.CORPORATE_ACTION_RECONCILIATION_FAILURE
    if stage == "persistence":
        return MarketIntelligenceErrorCategory.DATABASE_FAILURE
    if stage == "publication":
        if isinstance(error, TimeoutError):
            return MarketIntelligenceErrorCategory.LOCK_TIMEOUT
        return MarketIntelligenceErrorCategory.PUBLICATION_FAILURE
    return MarketIntelligenceErrorCategory.INVALID_MARKET_DATA
