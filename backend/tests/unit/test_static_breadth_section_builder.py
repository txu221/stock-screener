from datetime import date

import pandas as pd
import pytest
from app.services.breadth.types import BreadthUniverseMember, BreadthUniverseSnapshot
from app.services.point_in_time_universe_service import (
    PointInTimeUniverse,
    PointInTimeUniverseMember,
    hash_point_in_time_universe_symbols,
)
from app.services.static_breadth_section_builder import (
    StaticBreadthEngineInputFactory,
    StaticBreadthSectionBuilder,
)
from app.services.static_site_errors import StaticSiteSectionUnavailableError


def _price_frame(index: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": 100.0,
            "High": 101.0,
            "Low": 99.0,
            "Close": 100.0,
            "Adj Close": 100.0,
            "Volume": 1_000_000,
        },
        index=index,
    )


class _HistoricalFx:
    def get_historical_usd_rates(self, currencies, required_dates):
        return {
            currency: pd.Series(
                0.13,
                index=pd.DatetimeIndex(sorted(required_dates)),
            )
            for currency in currencies
        }


def test_static_inputs_retain_only_the_feature_window_for_prices_and_fx():
    recent_index = pd.bdate_range(end="2026-08-21", periods=400)
    full_index = pd.DatetimeIndex([pd.Timestamp("2020-01-02"), *recent_index])
    canonical_dates = list(recent_index[-120:].date)

    inputs = StaticBreadthEngineInputFactory(
        fx_service=_HistoricalFx()
    ).build(
        market="HK",
        canonical_dates=canonical_dates,
        price_data={"0700.HK": _price_frame(full_index)},
        currencies_by_symbol={"0700.HK": "HKD"},
    )

    retained_index = inputs.request.prices_by_symbol["0700.HK"].index
    assert len(retained_index) == 371
    assert retained_index[0] == recent_index[29]
    assert pd.Timestamp("2020-01-02") not in retained_index
    assert pd.Timestamp("2020-01-02") not in inputs.request.fx_by_currency["HKD"].index


def test_static_inputs_keep_each_dates_point_in_time_universe():
    first_date = date(2026, 8, 20)
    second_date = date(2026, 8, 21)
    prices = {
        "OLD": _price_frame(pd.bdate_range(end=second_date, periods=30)),
        "NEW": _price_frame(pd.bdate_range(end=second_date, periods=30)),
    }
    universes = {
        first_date: BreadthUniverseSnapshot(
            calculation_date=first_date,
            members=(BreadthUniverseMember("OLD", "USD"),),
            broad_signature="first",
        ),
        second_date: BreadthUniverseSnapshot(
            calculation_date=second_date,
            members=(
                BreadthUniverseMember("OLD", "USD"),
                BreadthUniverseMember("NEW", "USD"),
            ),
            broad_signature="second",
        ),
    }

    inputs = StaticBreadthEngineInputFactory().build(
        market="US",
        canonical_dates=[first_date, second_date],
        price_data=prices,
        universes_by_date=universes,
    )

    assert tuple(
        member.symbol
        for member in inputs.request.universes_by_date[first_date].members
    ) == ("OLD",)
    assert tuple(
        member.symbol
        for member in inputs.request.universes_by_date[second_date].members
    ) == ("OLD", "NEW")


def test_static_builder_resolves_and_passes_a_universe_for_each_history_date():
    first_date = date(2026, 8, 20)
    second_date = date(2026, 8, 21)
    price_frame = _price_frame(
        pd.DatetimeIndex([pd.Timestamp(first_date), pd.Timestamp(second_date)])
    )

    class _Resolver:
        def resolve(self, _db, *, market, as_of_date):
            symbols = (
                ("OLD",)
                if as_of_date == first_date
                else ("OLD", "NEW")
            )
            return PointInTimeUniverse(
                market=market,
                as_of_date=as_of_date,
                symbols=symbols,
                universe_hash=hash_point_in_time_universe_symbols(symbols),
                members=tuple(
                    PointInTimeUniverseMember(symbol=symbol, currency="USD")
                    for symbol in symbols
                ),
            )

    class _PriceCache:
        def get_cached_only(self, symbol, *, period):
            assert (symbol, period) == ("SPY", "1y")
            return price_frame

        def get_many_cached_only(self, symbols, *, period):
            assert period == "2y"
            return {symbol: price_frame for symbol in symbols}

    class _BenchmarkCache:
        def get_benchmark_candidates(self, market):
            assert market == "US"
            return ("SPY",)

        def get_benchmark_symbol(self, market):
            return "SPY"

    captured = {}

    class _InputFactory:
        def build(self, **kwargs):
            captured.update(kwargs)
            raise RuntimeError("captured inputs")

    builder = StaticBreadthSectionBuilder(
        ui_snapshot_service=object(),
        price_cache=_PriceCache(),
        benchmark_cache=_BenchmarkCache(),
        engine_input_factory=_InputFactory(),
        universe_resolver=_Resolver(),
    )

    with pytest.raises(RuntimeError, match="captured inputs"):
        builder.build(
            generated_at="2026-08-21T22:00:00Z",
            expected_as_of_date=second_date,
            market="US",
            serialized_rows=[{"symbol": "OLD"}, {"symbol": "NEW"}],
            db=object(),
        )

    universes = captured["universes_by_date"]
    assert tuple(member.symbol for member in universes[first_date].members) == (
        "OLD",
    )
    assert tuple(member.symbol for member in universes[second_date].members) == (
        "NEW",
        "OLD",
    )


class _EmptyUniverseResolver:
    def resolve(self, _db, *, market, as_of_date):
        return PointInTimeUniverse(
            market=market,
            as_of_date=as_of_date,
            symbols=(),
            universe_hash="empty",
        )


def test_static_builder_does_not_fall_back_when_database_universe_is_empty():
    builder = StaticBreadthSectionBuilder(
        ui_snapshot_service=object(),
        price_cache=object(),
        benchmark_cache=object(),
        universe_resolver=_EmptyUniverseResolver(),
    )

    with pytest.raises(
        StaticSiteSectionUnavailableError,
        match="No common-stock universe is available",
    ):
        builder.build(
            generated_at="2026-08-21T22:00:00Z",
            expected_as_of_date=date(2026, 8, 21),
            market="US",
            serialized_rows=[{"symbol": "ETF"}],
            db=object(),
        )
