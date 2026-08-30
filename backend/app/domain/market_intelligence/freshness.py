"""Completed-session freshness classification for published snapshots."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date


FRESHNESS_STALE_THRESHOLD_COMPLETED_SESSIONS = 2


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
