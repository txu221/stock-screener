"""Completed US session selection at production calendar boundaries."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import Mock

import pandas as pd
import pytest

from app.domain.market_intelligence.constants import MARKET_INTELLIGENCE_UNIVERSE
from app.infra.providers.market_intelligence_yahoo import (
    YahooMarketIntelligenceProvider,
)
from app.services.market_calendar_service import MarketCalendarService


@pytest.mark.parametrize(
    ("now", "expected"),
    (
        ("2026-08-24T12:00:00-04:00", date(2026, 8, 21)),
        ("2026-08-24T16:31:00-04:00", date(2026, 8, 24)),
        ("2026-08-29T12:00:00-04:00", date(2026, 8, 28)),
        ("2026-09-07T12:00:00-04:00", date(2026, 9, 4)),
        ("2026-11-27T13:29:00-05:00", date(2026, 11, 25)),
        ("2026-11-27T13:31:00-05:00", date(2026, 11, 27)),
    ),
)
def test_us_completed_session_policy_uses_calendar_and_close_buffer(
    now: str,
    expected: date,
) -> None:
    service = MarketCalendarService()

    assert service.last_completed_trading_day(
        "US", now=datetime.fromisoformat(now)
    ) == expected


def test_unfinished_current_session_bar_is_excluded_by_provider_boundary() -> None:
    completed = date(2026, 8, 24)
    unfinished = date(2026, 8, 25)
    frame = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [103.0, 104.0],
            "Low": [99.0, 100.0],
            "Close": [102.0, 103.0],
            "Adj Close": [102.0, 103.0],
            "Volume": [1_000_000.0, 250_000.0],
        },
        index=[pd.Timestamp(completed), pd.Timestamp(unfinished)],
    )
    fetcher = Mock()
    fetcher.fetch_batch_prices.return_value = {
        symbol: {
            "symbol": symbol,
            "price_data": frame,
            "has_error": False,
            "error": None,
            "as_of": datetime(2026, 8, 25, 16, 0, tzinfo=timezone.utc),
        }
        for symbol in MARKET_INTELLIGENCE_UNIVERSE
    }

    result = YahooMarketIntelligenceProvider(
        fetcher,
        clock=lambda: datetime(2026, 8, 25, 16, 0, tzinfo=timezone.utc),
    ).fetch(MARKET_INTELLIGENCE_UNIVERSE, completed)

    assert len(result.rows) == 12
    assert {row.trading_date for row in result.rows} == {completed}
    assert all(row.trading_date <= completed for row in result.rows)
