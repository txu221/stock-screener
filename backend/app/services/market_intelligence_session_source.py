"""Completed-market-session adapter for the Phase 1 historical window."""

from __future__ import annotations

from datetime import date, timedelta


class SessionWindowUnavailable(RuntimeError):
    def __init__(
        self,
        *,
        market: str,
        as_of: date,
        required_count: int,
        available_count: int,
    ) -> None:
        self.market = market
        self.as_of = as_of
        self.required_count = required_count
        self.available_count = available_count
        super().__init__(
            f"{market} needs {required_count} completed sessions through "
            f"{as_of.isoformat()}, found {available_count}"
        )


class CompletedSessionSource:
    def __init__(self, calendar) -> None:
        self._calendar = calendar

    def completed_sessions(
        self,
        market: str,
        as_of: date,
        minimum: int = 90,
    ) -> tuple[date, ...]:
        if minimum <= 0:
            raise ValueError("minimum must be positive")
        start = as_of - timedelta(days=minimum * 2 + 30)
        sessions = tuple(self._calendar.trading_days(market, start, as_of))
        if (
            len(sessions) < minimum
            or not sessions
            or sessions[-1] != as_of
        ):
            raise SessionWindowUnavailable(
                market=market,
                as_of=as_of,
                required_count=minimum,
                available_count=len(sessions),
            )
        # Retain the full bounded calendar window so every provider row in the
        # 6-month fetch can be validated and preserved as evidence. Metrics
        # still select their exact trailing completed-session anchors.
        return sessions
