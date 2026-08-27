"""Explicit opt-in live Yahoo acceptance check for exactly 12 ETFs."""

from __future__ import annotations

import pytest

from app.domain.market_intelligence.constants import MARKET_INTELLIGENCE_UNIVERSE
from scripts.validate_market_intelligence_live import run_live_validation


@pytest.mark.integration
@pytest.mark.live_provider
@pytest.mark.manual_provider
def test_live_yahoo_fixed_universe_metrics_and_replay(
    phase2_live_provider_enabled: bool,
) -> None:
    assert phase2_live_provider_enabled

    summary = run_live_validation()

    assert summary["requested"] == list(MARKET_INTELLIGENCE_UNIVERSE)
    assert summary["requested_count"] == 12
    assert summary["request_failure"] is None
    assert summary["returned_count"] == 12
    assert summary["missing"] == []
    assert summary["rejected_rows"] == 0
    assert summary["freshness"]["complete_through_target"] is True
    assert summary["candidate_status"] == "SUCCEEDED"
    assert summary["snapshot_count"] == 12
    assert all(
        evidence["all_metrics_match"]
        for evidence in summary["manual_checks"].values()
    )

    replay = summary["historical_replay_using_real_provider_data"]
    assert len(replay) == 5
    assert [item["as_of"] for item in replay] == sorted(
        item["as_of"] for item in replay
    )
    assert all(item["max_input_date"] <= item["as_of"] for item in replay)
    assert all(item["status"] == "SUCCEEDED" for item in replay)
    assert all(item["snapshot_count"] == 12 for item in replay)
    assert replay[0]["previous_rank_count"] == 0
    assert all(item["previous_rank_count"] > 0 for item in replay[1:])
