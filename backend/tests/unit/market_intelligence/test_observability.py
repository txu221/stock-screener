"""Stable Market Intelligence observability contracts."""

from __future__ import annotations

import math

import pytest

from app.domain.market_intelligence.observability import (
    MARKET_INTELLIGENCE_STAGE_TIMING_KEYS,
    MarketIntelligenceErrorCategory,
    complete_stage_timings,
    elapsed_milliseconds,
    failure_category_for_request,
)


def test_error_category_taxonomy_is_stable() -> None:
    assert {category.value for category in MarketIntelligenceErrorCategory} == {
        "PROVIDER_FAILURE",
        "PROVIDER_SCHEMA_DRIFT",
        "INVALID_MARKET_DATA",
        "DATABASE_FAILURE",
        "LOCK_TIMEOUT",
        "PUBLICATION_FAILURE",
        "CELERY_DELIVERY_FAILURE",
        "STALE_DATA",
        "INSUFFICIENT_HISTORY",
        "CORPORATE_ACTION_RECONCILIATION_FAILURE",
    }


def test_complete_stage_timings_always_has_finite_nonnegative_milliseconds() -> None:
    timings = complete_stage_timings(
        {"provider_fetch_ms": 12.5, "validation_ms": 3.25}
    )

    assert tuple(timings) == MARKET_INTELLIGENCE_STAGE_TIMING_KEYS
    assert timings["provider_fetch_ms"] == 12.5
    assert timings["normalization_ms"] == 0.0
    assert all(math.isfinite(value) and value >= 0 for value in timings.values())


@pytest.mark.parametrize("invalid", (-1.0, math.inf, -math.inf, math.nan))
def test_complete_stage_timings_rejects_invalid_evidence(invalid: float) -> None:
    with pytest.raises(ValueError, match="finite and nonnegative"):
        complete_stage_timings({"provider_fetch_ms": invalid})


@pytest.mark.parametrize(
    ("start", "end"),
    ((2.0, 1.0), (1.0, math.inf), (math.nan, 1.0)),
)
def test_elapsed_milliseconds_rejects_invalid_monotonic_evidence(
    start: float,
    end: float,
) -> None:
    with pytest.raises(ValueError, match="finite and nonnegative"):
        elapsed_milliseconds(start, end)


def test_request_failure_mapping_distinguishes_schema_drift() -> None:
    assert failure_category_for_request("PROVIDER_SCHEMA_DRIFT") is (
        MarketIntelligenceErrorCategory.PROVIDER_SCHEMA_DRIFT
    )
    assert failure_category_for_request("PROVIDER_TIMEOUT") is (
        MarketIntelligenceErrorCategory.PROVIDER_FAILURE
    )
