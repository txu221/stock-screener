"""Deterministic end-to-end scenario shared by Phase 2 integration tests."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.domain.market_intelligence.constants import MARKET_INTELLIGENCE_UNIVERSE
from app.domain.market_intelligence.models import (
    ProviderBatchResult,
    RawBar,
    RequestFailure,
)


def weekday_sessions(end: date, count: int) -> tuple[date, ...]:
    values: list[date] = []
    candidate = end
    while len(values) < count:
        if candidate.weekday() < 5:
            values.append(candidate)
        candidate -= timedelta(days=1)
    return tuple(reversed(values))


def scenario_rows(sessions: tuple[date, ...]) -> tuple[RawBar, ...]:
    rows: list[RawBar] = []
    for symbol_index, symbol in enumerate(MARKET_INTELLIGENCE_UNIVERSE):
        start = 80.0 + symbol_index * 4.0
        daily_return = -0.0004 + symbol_index * 0.00011
        base_volume = 800_000.0 + symbol_index * 75_000.0
        for index, session in enumerate(sessions):
            close = start * (1.0 + daily_return) ** index
            rows.append(
                RawBar(
                    provider="phase2_fixture_yahoo",
                    provider_symbol=symbol,
                    symbol=symbol,
                    raw_trading_date=session.isoformat(),
                    trading_date=session,
                    open=close * 0.996,
                    high=close * 1.012,
                    low=close * 0.989,
                    close=close,
                    adjusted_close=close * 0.997,
                    volume=base_volume + index * 2_000.0,
                    source_timestamp=datetime.combine(
                        session, datetime.min.time(), timezone.utc
                    ).replace(hour=21, minute=5),
                )
            )
    return tuple(rows)


class ScenarioSessionSource:
    def __init__(self, sessions: tuple[date, ...]) -> None:
        self.sessions = sessions

    def completed_sessions(
        self,
        market: str,
        as_of: date,
        minimum: int,
    ) -> tuple[date, ...]:
        assert market == "US"
        selected = tuple(session for session in self.sessions if session <= as_of)
        assert selected and selected[-1] == as_of
        assert len(selected) >= minimum
        return selected


class ScenarioProvider:
    def __init__(
        self,
        rows: tuple[RawBar, ...],
        *,
        missing_by_date: dict[date, str] | None = None,
        failed_dates: set[date] | None = None,
    ) -> None:
        self.rows = rows
        self.missing_by_date = missing_by_date or {}
        self.failed_dates = failed_dates or set()
        self.requests: list[tuple[tuple[str, ...], date]] = []

    def fetch(self, symbols, as_of: date) -> ProviderBatchResult:
        requested = tuple(symbols)
        assert requested == MARKET_INTELLIGENCE_UNIVERSE
        self.requests.append((requested, as_of))
        timestamp = datetime.combine(
            as_of, datetime.min.time(), timezone.utc
        ).replace(hour=21, minute=6)
        if as_of in self.failed_dates:
            return ProviderBatchResult(
                provider="phase2_fixture_yahoo",
                response_timestamp=timestamp,
                rows=(),
                symbol_failures=(),
                request_failure=RequestFailure(
                    code="PROVIDER_TIMEOUT",
                    message="controlled Phase 2 request failure",
                ),
            )
        missing = self.missing_by_date.get(as_of)
        selected = tuple(
            row
            for row in self.rows
            if row.trading_date <= as_of and row.symbol != missing
        )
        return ProviderBatchResult(
            provider="phase2_fixture_yahoo",
            response_timestamp=timestamp,
            rows=selected,
            symbol_failures=(),
            request_failure=None,
        )
