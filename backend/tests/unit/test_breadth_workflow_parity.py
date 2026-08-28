from datetime import date
from unittest.mock import Mock

import pandas as pd

from app.services.breadth.engine import BreadthEngine, BreadthEngineRequest
from app.services.breadth.types import BreadthUniverseMember, BreadthUniverseSnapshot
from app.services.breadth_attribution_service import BreadthAttributionService
from app.services.point_in_time_universe_service import (
    hash_point_in_time_universe_symbols,
)
from app.services.static_breadth_section_builder import StaticBreadthSectionBuilder


def _prices(final_close: float) -> pd.DataFrame:
    index = pd.bdate_range(end="2026-08-21", periods=70)
    close = pd.Series([100.0] * 69 + [final_close], index=index)
    volume = pd.Series([100_000.0] * 69 + [200_000.0], index=index)
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


def test_static_and_attribution_daily_counts_match_canonical_engine():
    calculation_date = date(2026, 8, 21)
    prices = {"UP": _prices(105.0), "DOWN": _prices(95.0)}
    members = (
        BreadthUniverseMember("DOWN", "USD"),
        BreadthUniverseMember("UP", "USD"),
    )
    snapshot = BreadthUniverseSnapshot(
        calculation_date=calculation_date,
        members=members,
        broad_signature=hash_point_in_time_universe_symbols(("DOWN", "UP")),
    )
    canonical = BreadthEngine().calculate(
        BreadthEngineRequest(
            market="US",
            dates=(calculation_date,),
            universes_by_date={calculation_date: snapshot},
            prices_by_symbol=prices,
            fx_by_currency={"USD": pd.Series(1.0, index=prices["UP"].index)},
        )
    )[calculation_date]

    builder = StaticBreadthSectionBuilder(
        ui_snapshot_service=Mock(),
        price_cache=Mock(),
        benchmark_cache=Mock(),
    )
    static = builder._compute_breadth_metrics_by_date(
        [calculation_date],
        prices,
        market="US",
    )[calculation_date]
    attribution = BreadthAttributionService().compute(
        symbols_meta=[
            {"symbol": "UP", "ibd_industry_group": "Group A"},
            {"symbol": "DOWN"},
        ],
        price_data=prices,
        target_dates=[calculation_date],
    )[0]

    expected = (
        canonical.values.stocks_up_4pct,
        canonical.values.stocks_down_4pct,
    )
    assert (static["stocks_up_4pct"], static["stocks_down_4pct"]) == expected
    assert (
        attribution["stocks_up_4pct"],
        attribution["stocks_down_4pct"],
    ) == expected
    assert sum(group["up_count"] for group in attribution["groups"]) == expected[0]
    assert sum(group["down_count"] for group in attribution["groups"]) == expected[1]
