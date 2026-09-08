"""Completed-session freshness classification for published snapshots."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, timedelta


FRESHNESS_STALE_THRESHOLD_COMPLETED_SESSIONS = 2
_INITIAL_COMPLETED_SESSION_LOOKBACK_DAYS = 14
_MAX_COMPLETED_SESSION_LOOKBACK_DAYS = 366
_MINIMUM_COMPLETED_SESSIONS = 3


def collect_completed_sessions(
    latest_completed_session: date,
    load_sessions: Callable[[date, date], Iterable[date]],
) -> tuple[date, ...]:
    """Load enough completed sessions to classify the two-session stale boundary."""
    lookback_days = _INITIAL_COMPLETED_SESSION_LOOKBACK_DAYS
    while True:
        sessions = tuple(
            sorted(
                {
                    session
                    for session in load_sessions(
                        latest_completed_session - timedelta(days=lookback_days),
                        latest_completed_session,
                    )
                    if session <= latest_completed_session
                }
            )
        )
        if (
            len(sessions) >= _MINIMUM_COMPLETED_SESSIONS
            or lookback_days >= _MAX_COMPLETED_SESSION_LOOKBACK_DAYS
        ):
            return sessions
        lookback_days = min(
            lookback_days * 2,
            _MAX_COMPLETED_SESSION_LOOKBACK_DAYS,
        )


def classify_completed_session_freshness(
    as_of: date | None,
    completed_sessions: Iterable[date],
) -> str:
    """Classify a snapshot by completed exchange sessions, not calendar days."""
    sessions = tuple(sorted(set(completed_sessions)))
    if as_of is None or not sessions:
        return "UNAVAILABLE"

    latest = sessions[-1]
    if as_of == latest:
        return "FRESH"
    if as_of > latest:
        return "UNAVAILABLE"

    sessions_behind = sum(session > as_of for session in sessions)
    if sessions_behind == 1:
        return "AGING"
    return "STALE"
