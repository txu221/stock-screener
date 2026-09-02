"""Completed-session freshness semantics for Market Intelligence."""

from datetime import date

import pytest

from app.domain.market_intelligence.freshness import (
    classify_completed_session_freshness,
    collect_completed_sessions,
)


FRIDAY = date(2026, 7, 10)
MONDAY = date(2026, 7, 13)
TUESDAY = date(2026, 7, 14)
WEDNESDAY = date(2026, 7, 15)


def test_latest_completed_session_is_fresh() -> None:
    assert (
        classify_completed_session_freshness(TUESDAY, (MONDAY, TUESDAY))
        == "FRESH"
    )


def test_exactly_one_completed_session_behind_is_aging() -> None:
    assert (
        classify_completed_session_freshness(MONDAY, (FRIDAY, MONDAY, TUESDAY))
        == "AGING"
    )


def test_weekend_does_not_add_staleness() -> None:
    assert classify_completed_session_freshness(FRIDAY, (FRIDAY,)) == "FRESH"


def test_market_holiday_does_not_add_staleness() -> None:
    thursday_before_holiday = date(2026, 7, 2)
    monday_after_holiday = date(2026, 7, 6)
    assert (
        classify_completed_session_freshness(
            thursday_before_holiday,
            (thursday_before_holiday, monday_after_holiday),
        )
        == "AGING"
    )


def test_two_or_more_completed_sessions_behind_is_stale() -> None:
    assert (
        classify_completed_session_freshness(
            MONDAY,
            (FRIDAY, MONDAY, TUESDAY, WEDNESDAY),
        )
        == "STALE"
    )


def test_completed_session_collection_widens_across_extended_closure() -> None:
    sessions = (
        date(2025, 12, 1),
        date(2026, 1, 5),
        date(2026, 1, 31),
    )
    lookbacks: list[int] = []

    def load_sessions(start: date, end: date) -> tuple[date, ...]:
        lookbacks.append((end - start).days)
        return tuple(session for session in sessions if start <= session <= end)

    completed = collect_completed_sessions(
        sessions[-1],
        load_sessions,
    )

    assert completed == sessions
    assert lookbacks == [14, 28, 56, 112]
    assert classify_completed_session_freshness(sessions[0], completed) == "STALE"


@pytest.mark.parametrize(
    ("as_of", "completed_sessions"),
    ((None, (MONDAY,)), (MONDAY, ()), (WEDNESDAY, (MONDAY, TUESDAY))),
)
def test_missing_or_unusable_snapshot_is_unavailable(
    as_of: date | None,
    completed_sessions: tuple[date, ...],
) -> None:
    assert (
        classify_completed_session_freshness(as_of, completed_sessions)
        == "UNAVAILABLE"
    )
