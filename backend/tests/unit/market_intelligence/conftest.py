from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.domain.market_intelligence.constants import MARKET_INTELLIGENCE_UNIVERSE
from app.domain.market_intelligence.models import RawBar

_SCENARIO_PATH = (
    Path(__file__).parents[2]
    / "fixtures"
    / "market_intelligence"
    / "sector_golden_scenario.json"
)


def _weekday_sessions(*, end: date, count: int) -> tuple[date, ...]:
    sessions: list[date] = []
    candidate = end
    while len(sessions) < count:
        if candidate.weekday() < 5:
            sessions.append(candidate)
        candidate -= timedelta(days=1)
    return tuple(reversed(sessions))


@pytest.fixture
def golden_scenario() -> dict[str, object]:
    return json.loads(_SCENARIO_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def golden_sessions(golden_scenario: dict[str, object]) -> tuple[date, ...]:
    return _weekday_sessions(
        end=date.fromisoformat(str(golden_scenario["as_of"])),
        count=int(golden_scenario["session_count"]),
    )


@pytest.fixture
def golden_raw_bars(
    golden_scenario: dict[str, object],
    golden_sessions: tuple[date, ...],
) -> tuple[RawBar, ...]:
    symbol_overrides = golden_scenario["symbols"]
    default_sector = golden_scenario["default_sector"]
    adjustment_factor = float(golden_scenario["adjustment_factor"])
    source_timestamp = datetime(2026, 5, 15, 21, 5, tzinfo=timezone.utc)
    rows: list[RawBar] = []

    for symbol in MARKET_INTELLIGENCE_UNIVERSE:
        config = dict(default_sector)
        config.update(symbol_overrides.get(symbol, {}))
        start_close = float(config["start_close"])
        daily_return = float(config["daily_return"])
        base_volume = float(config["base_volume"])
        for index, session in enumerate(golden_sessions):
            close = start_close * (1.0 + daily_return) ** index
            rows.append(
                RawBar(
                    provider="fixture_yahoo",
                    provider_symbol=symbol,
                    symbol=symbol,
                    raw_trading_date=session.isoformat(),
                    trading_date=session,
                    open=close * 0.995,
                    high=close * 1.01,
                    low=close * 0.99,
                    close=close,
                    adjusted_close=close * adjustment_factor,
                    volume=base_volume + index * 1_000.0,
                    source_timestamp=source_timestamp,
                )
            )
    return tuple(rows)
