from datetime import date

import pandas as pd
from sqlalchemy import event

from app.models.stock import StockPrice
from app.services.point_in_time_universe_service import (
    PointInTimeUniverse,
    hash_point_in_time_universe_symbols,
)
from app.services.static_breadth_eligibility import classify_static_breadth_eligibility


class _UniverseResolver:
    def __init__(self, symbols_by_date, policies_by_date):
        self._symbols_by_date = symbols_by_date
        self._policies_by_date = policies_by_date

    def resolve(self, _db, *, market, as_of_date):
        symbols = tuple(self._symbols_by_date[as_of_date])
        return PointInTimeUniverse(
            market=market,
            as_of_date=as_of_date,
            symbols=symbols,
            universe_hash=hash_point_in_time_universe_symbols(symbols),
        )

    def policy_for(self, _market, as_of_date):
        return self._policies_by_date[as_of_date]


def _add_prices(db, symbol, dates, *, invalid_date=None):
    for row_date in dates:
        value = None if row_date == invalid_date else 100.0
        db.add(
            StockPrice(
                symbol=symbol,
                date=row_date,
                open=value,
                high=value,
                low=value,
                close=value,
                volume=1000,
            )
        )


def test_classifies_point_in_time_eligibility_and_price_exclusions(universe_session):
    first_date = date(2026, 4, 9)
    second_date = date(2026, 4, 10)
    first_70 = tuple(pd.bdate_range(end=first_date, periods=70).date)
    first_69 = first_70[1:]
    second_70 = (*first_69, second_date)
    _add_prices(universe_session, "READY", first_70)
    _add_prices(universe_session, "BECOMES_READY", second_70)
    _add_prices(universe_session, "DATE_GAP", first_70)
    _add_prices(
        universe_session,
        "NULL_BAR",
        first_70,
        invalid_date=first_date,
    )
    universe_session.commit()
    resolver = _UniverseResolver(
        {
            first_date: (
                "READY",
                "BECOMES_READY",
                "DATE_GAP",
                "NULL_BAR",
                "ABC-W",
            ),
            second_date: ("BECOMES_READY", "DATE_GAP"),
        },
        {
            first_date: "point_in_time",
            second_date: "current_active_fallback_v1",
        },
    )
    price_queries = []

    def record_price_query(_conn, _cursor, statement, *_args):
        if "stock_prices" in statement:
            price_queries.append(statement)

    event.listen(
        universe_session.get_bind(),
        "before_cursor_execute",
        record_price_query,
    )

    result = classify_static_breadth_eligibility(
        universe_session,
        market="US",
        calculation_dates=(first_date, second_date),
        universe_resolver=resolver,
    )

    assert result.candidate_counts_by_date == {first_date: 5, second_date: 2}
    assert result.eligible_symbols_by_date == {
        first_date: ("ABC-W", "BECOMES_READY", "DATE_GAP", "NULL_BAR", "READY"),
        second_date: ("BECOMES_READY", "DATE_GAP"),
    }
    assert result.eligible_counts_by_date == {first_date: 5, second_date: 2}
    assert result.universe_policy_by_date == {
        first_date: "point_in_time",
        second_date: "current_active_fallback_v1",
    }
    assert result.by_date[first_date].candidate_count == 5
    assert result.by_date[first_date].eligible_symbols == (
        "ABC-W",
        "BECOMES_READY",
        "DATE_GAP",
        "NULL_BAR",
        "READY",
    )
    assert result.by_date[first_date].universe_policy == "point_in_time"
    assert (
        result.by_date[first_date].eligibility_signature
        == result.eligibility_signatures_by_date[first_date]
    )
    assert result.unsupported_symbols == ("ABC-W",)
    assert result.unsupported_count == 1
    assert result.insufficient_history_symbols == ()
    assert result.insufficient_history_count == 0
    assert result.exact_date_gap_symbols == ()
    assert result.exact_date_gap_count == 0
    assert price_queries == []


def test_exclusion_samples_are_bounded_sorted_and_zero_counts_are_distinct(
    universe_session,
):
    calculation_date = date(2026, 4, 10)
    unsupported = tuple(f"SYM-{index:02d}-W" for index in range(25, -1, -1))
    resolver = _UniverseResolver(
        {calculation_date: unsupported},
        {calculation_date: "current_active_fallback_v1"},
    )

    result = classify_static_breadth_eligibility(
        universe_session,
        market="US",
        calculation_dates=(calculation_date,),
        universe_resolver=resolver,
        exclusion_sample_limit=20,
    )

    assert result.candidate_counts_by_date[calculation_date] == 26
    assert result.eligible_counts_by_date[calculation_date] == 26
    assert len(result.unsupported_symbols) == 20
    assert result.unsupported_count == 26
    assert result.unsupported_symbols == tuple(sorted(unsupported)[:20])


def test_zero_candidate_and_zero_eligible_are_distinguishable(universe_session):
    empty_date = date(2026, 4, 9)
    no_history_date = date(2026, 4, 10)
    resolver = _UniverseResolver(
        {empty_date: (), no_history_date: ("NO_HISTORY",)},
        {empty_date: "point_in_time", no_history_date: "point_in_time"},
    )

    result = classify_static_breadth_eligibility(
        universe_session,
        market="US",
        calculation_dates=(empty_date, no_history_date),
        universe_resolver=resolver,
    )

    assert result.candidate_counts_by_date == {empty_date: 0, no_history_date: 1}
    assert result.eligible_counts_by_date == {empty_date: 0, no_history_date: 1}
