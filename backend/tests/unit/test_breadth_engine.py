from datetime import date

import pandas as pd
import pytest
from app.services.breadth.engine import BreadthEngine, BreadthEngineRequest
from app.services.breadth.types import (
    BreadthUniverseMember,
    BreadthUniverseSnapshot,
)
from app.services.point_in_time_universe_service import (
    hash_point_in_time_universe_symbols,
)
from app.services.static_breadth_eligibility import (
    static_breadth_eligibility_signature,
)


def _prices(index, closes, *, final_volume=110_000):
    close = pd.Series(closes, index=index, dtype=float)
    volume = pd.Series(100_000, index=index, dtype=float)
    volume.iloc[-1] = final_volume
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Adj Close": close,
            "Volume": volume,
        },
        index=index,
    )


def test_engine_tracks_metric_specific_eligibility_for_mixed_history():
    veteran_index = pd.bdate_range(end="2026-08-21", periods=252)
    ipo_index = veteran_index[-2:]
    calculation_date = date(2026, 8, 21)
    members = (
        BreadthUniverseMember("IPO", "USD"),
        BreadthUniverseMember("VETERAN", "USD"),
    )
    snapshot = BreadthUniverseSnapshot(
        calculation_date=calculation_date,
        members=members,
        broad_signature=hash_point_in_time_universe_symbols(("IPO", "VETERAN")),
    )

    result = BreadthEngine().calculate(
        BreadthEngineRequest(
            market="US",
            dates=(calculation_date,),
            universes_by_date={calculation_date: snapshot},
            prices_by_symbol={
                "VETERAN": _prices(veteran_index, [100.0] * 251 + [104.0]),
                "IPO": _prices(ipo_index, [10.0, 11.0]),
            },
            fx_by_currency={
                "USD": pd.Series(1.0, index=veteran_index),
            },
        )
    )[calculation_date]

    assert result.broad_universe_count == 2
    assert result.eligibility.advance_decline_eligible_count == 2
    assert result.values.advancing_count == 2
    assert result.values.declining_count == 0
    assert result.values.unchanged_count == 0
    assert result.eligibility.stockbee_daily_eligible_count == 1
    assert result.values.stocks_up_4pct == 1
    assert result.eligibility.stockbee_quarter_eligible_count == 1
    assert result.eligibility.high_low_52week_eligible_count == 1
    assert result.values.new_high_52week_count == 1
    assert result.eligibility.t2108_eligible_count == 1
    assert result.values.t2108_count == 1
    assert result.values.t2108_pct == pytest.approx(100.0)
    assert result.stockbee_eligibility_signature == (
        hash_point_in_time_universe_symbols(("VETERAN",))
    )
    assert result.calculation_revision == 2

    record = result.to_record_mapping()
    assert record["total_stocks_scanned"] == record["broad_universe_count"] == 2


def test_engine_persists_the_canonical_broad_eligibility_signature():
    index = pd.bdate_range(end="2026-08-21", periods=2)
    calculation_date = date(2026, 8, 21)
    snapshot = BreadthUniverseSnapshot(
        calculation_date=calculation_date,
        members=(BreadthUniverseMember("AAA", "USD"),),
        broad_signature=hash_point_in_time_universe_symbols(("AAA",)),
    )

    result = BreadthEngine().calculate(
        BreadthEngineRequest(
            market="US",
            dates=(calculation_date,),
            universes_by_date={calculation_date: snapshot},
            prices_by_symbol={"AAA": _prices(index, [100.0, 101.0])},
            fx_by_currency={"USD": pd.Series(1.0, index=index)},
        )
    )[calculation_date]

    assert result.eligibility_signature == static_breadth_eligibility_signature(
        ("AAA",)
    )


def test_stockbee_signature_tracks_liquidity_when_daily_history_is_ineligible():
    index = pd.bdate_range(end="2026-08-21", periods=21)
    calculation_date = date(2026, 8, 21)
    prices = _prices(index, [10.0] * len(index))
    prices.loc[index[-2], "Adj Close"] = float("nan")
    snapshot = BreadthUniverseSnapshot(
        calculation_date=calculation_date,
        members=(BreadthUniverseMember("LIQUID", "USD"),),
        broad_signature=hash_point_in_time_universe_symbols(("LIQUID",)),
    )

    result = BreadthEngine().calculate(
        BreadthEngineRequest(
            market="US",
            dates=(calculation_date,),
            universes_by_date={calculation_date: snapshot},
            prices_by_symbol={"LIQUID": prices},
            fx_by_currency={"USD": pd.Series(1.0, index=index)},
        )
    )[calculation_date]

    assert result.eligibility.stockbee_daily_eligible_count == 0
    assert result.stockbee_eligibility_signature == (
        hash_point_in_time_universe_symbols(("LIQUID",))
    )


def test_engine_isolates_a_malformed_symbol_frame():
    index = pd.bdate_range(end="2026-08-21", periods=252)
    calculation_date = date(2026, 8, 21)
    malformed_index = index.append(
        pd.DatetimeIndex([index[-1] + pd.Timedelta(hours=1)])
    )
    members = (
        BreadthUniverseMember("BAD", "USD"),
        BreadthUniverseMember("GOOD", "USD"),
    )
    snapshot = BreadthUniverseSnapshot(
        calculation_date=calculation_date,
        members=members,
        broad_signature=hash_point_in_time_universe_symbols(("BAD", "GOOD")),
    )

    result = BreadthEngine().calculate(
        BreadthEngineRequest(
            market="US",
            dates=(calculation_date,),
            universes_by_date={calculation_date: snapshot},
            prices_by_symbol={
                "BAD": _prices(malformed_index, [100.0] * len(malformed_index)),
                "GOOD": _prices(index, [100.0] * 251 + [104.0]),
            },
            fx_by_currency={"USD": pd.Series(1.0, index=malformed_index)},
        )
    )[calculation_date]

    assert result.broad_universe_count == 2
    assert result.eligibility.advance_decline_eligible_count == 1
    assert result.values.advancing_count == 1
