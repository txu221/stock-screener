"""Canonical range engine for all market-breadth calculation paths."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date

import pandas as pd

from app.services.point_in_time_universe_service import (
    hash_point_in_time_universe_symbols,
)

from .formulas import prepare_feature_frame, signal_flags_at, validate_price_frame
from .ratios import calculate_inclusive_ratios
from .types import (
    BreadthDailyCount,
    BreadthDailyResult,
    BreadthEligibilityCounts,
    BreadthFormulaPolicy,
    BreadthIndicatorValues,
    BreadthUniverseSnapshot,
    SymbolBreadthSignals,
)
from .universe import breadth_eligibility_signature

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BreadthEngineRequest:
    market: str
    dates: tuple[date, ...]
    universes_by_date: Mapping[date, BreadthUniverseSnapshot]
    prices_by_symbol: Mapping[str, pd.DataFrame]
    fx_by_currency: Mapping[str, pd.Series]
    seed_counts: tuple[BreadthDailyCount, ...] = ()
    policy: BreadthFormulaPolicy = field(default_factory=BreadthFormulaPolicy)


class BreadthEngine:
    def calculate(
        self, request: BreadthEngineRequest
    ) -> Mapping[date, BreadthDailyResult]:
        dates = tuple(request.dates)
        if dates != tuple(sorted(set(dates))):
            raise ValueError("Breadth calculation dates must be ordered and unique")

        currencies_by_symbol: dict[str, str] = {}
        for calculation_date in dates:
            snapshot = request.universes_by_date.get(calculation_date)
            if snapshot is None:
                raise ValueError(
                    f"Missing breadth universe for {calculation_date.isoformat()}"
                )
            if snapshot.calculation_date != calculation_date:
                raise ValueError("Breadth universe date does not match request date")
            for member in snapshot.members:
                prior_currency = currencies_by_symbol.setdefault(
                    member.symbol,
                    member.currency.upper(),
                )
                if prior_currency != member.currency.upper():
                    raise ValueError(
                        f"Currency changed within breadth range for {member.symbol}"
                    )

        features_by_symbol: dict[str, pd.DataFrame] = {}
        for symbol, currency in currencies_by_symbol.items():
            prices = request.prices_by_symbol.get(symbol)
            if prices is None or prices.empty:
                continue
            try:
                validate_price_frame(prices)
            except ValueError as exc:
                logger.warning(
                    "Skipping malformed breadth prices for %s: %s", symbol, exc
                )
                continue
            fx = request.fx_by_currency.get(currency)
            if fx is None:
                if currency == "USD":
                    fx = pd.Series(1.0, index=prices.index)
                else:
                    raise ValueError(f"Missing historical FX series for {currency}")
            rates_by_date = {
                pd.Timestamp(value).date(): float(rate)
                for value, rate in fx.items()
            }
            aligned_fx = pd.Series(
                (
                    rates_by_date.get(pd.Timestamp(value).date())
                    for value in prices.index
                ),
                index=prices.index,
                dtype=float,
            )
            if aligned_fx.isna().any():
                missing_date = pd.Timestamp(
                    aligned_fx[aligned_fx.isna()].index[0]
                ).date()
                raise ValueError(
                    f"Missing historical {currency}->USD FX for "
                    f"{missing_date.isoformat()}"
                )
            features_by_symbol[symbol] = prepare_feature_frame(
                prices,
                aligned_fx,
                atr_period=request.policy.atr_period,
            )

        partial_results: dict[date, BreadthDailyResult] = {}
        daily_counts: list[BreadthDailyCount] = []
        for calculation_date in dates:
            snapshot = request.universes_by_date[calculation_date]
            signals_by_symbol: dict[str, SymbolBreadthSignals] = {}
            for member in snapshot.members:
                features = features_by_symbol.get(member.symbol)
                if features is None or not member.is_common_stock:
                    continue
                signals_by_symbol[member.symbol] = signal_flags_at(
                    features,
                    calculation_date,
                    request.policy,
                )

            signals = tuple(signals_by_symbol.values())
            eligibility = BreadthEligibilityCounts(
                advance_decline_eligible_count=sum(
                    item.eligibility.advance_decline for item in signals
                ),
                stockbee_daily_eligible_count=sum(
                    item.eligibility.stockbee_daily for item in signals
                ),
                stockbee_month_eligible_count=sum(
                    item.eligibility.stockbee_month for item in signals
                ),
                stockbee_34day_eligible_count=sum(
                    item.eligibility.stockbee_34day for item in signals
                ),
                stockbee_quarter_eligible_count=sum(
                    item.eligibility.stockbee_quarter for item in signals
                ),
                t2108_eligible_count=sum(item.eligibility.t2108 for item in signals),
                high_low_52week_eligible_count=sum(
                    item.eligibility.high_low_52week for item in signals
                ),
                atr_extension_eligible_count=sum(
                    item.eligibility.atr_extension for item in signals
                ),
            )
            t2108_count = sum(item.t2108_above for item in signals)
            values = BreadthIndicatorValues(
                stocks_up_4pct=sum(item.up_4pct for item in signals),
                stocks_down_4pct=sum(item.down_4pct for item in signals),
                stocks_up_25pct_quarter=sum(item.up_25pct_quarter for item in signals),
                stocks_down_25pct_quarter=sum(
                    item.down_25pct_quarter for item in signals
                ),
                stocks_up_25pct_month=sum(item.up_25pct_month for item in signals),
                stocks_down_25pct_month=sum(item.down_25pct_month for item in signals),
                stocks_up_50pct_month=sum(item.up_50pct_month for item in signals),
                stocks_down_50pct_month=sum(item.down_50pct_month for item in signals),
                stocks_up_13pct_34days=sum(item.up_13pct_34days for item in signals),
                stocks_down_13pct_34days=sum(
                    item.down_13pct_34days for item in signals
                ),
                advancing_count=sum(item.advancing for item in signals),
                declining_count=sum(item.declining for item in signals),
                unchanged_count=sum(item.unchanged for item in signals),
                new_high_52week_count=sum(item.new_high_52week for item in signals),
                new_low_52week_count=sum(item.new_low_52week for item in signals),
                t2108_count=t2108_count,
                t2108_pct=(
                    round(t2108_count / eligibility.t2108_eligible_count * 100.0, 2)
                    if eligibility.t2108_eligible_count
                    else None
                ),
                atr_10x_extension_count=sum(item.atr_10x_extension for item in signals),
            )

            if (
                values.advancing_count + values.declining_count + values.unchanged_count
                != eligibility.advance_decline_eligible_count
            ):
                raise AssertionError("Advance/decline counts do not reconcile")
            if not 0 <= values.t2108_count <= eligibility.t2108_eligible_count:
                raise AssertionError("T2108 count exceeds its eligible denominator")
            if request.policy.calculation_revision != 2:
                raise AssertionError("Canonical breadth engine must produce revision 2")

            stockbee_symbols = tuple(
                sorted(
                    symbol
                    for symbol, item in signals_by_symbol.items()
                    if item.eligibility.stockbee_liquidity
                )
            )
            result = BreadthDailyResult(
                market=request.market.upper(),
                calculation_date=calculation_date,
                values=values,
                eligibility=eligibility,
                broad_universe_count=len(snapshot.members),
                eligibility_signature=breadth_eligibility_signature(
                    member.symbol for member in snapshot.members
                ),
                stockbee_eligibility_signature=hash_point_in_time_universe_symbols(
                    stockbee_symbols
                ),
                calculation_revision=request.policy.calculation_revision,
            )
            partial_results[calculation_date] = result
            daily_counts.append(
                BreadthDailyCount(
                    date=calculation_date,
                    stocks_up_4pct=values.stocks_up_4pct,
                    stocks_down_4pct=values.stocks_down_4pct,
                    market=result.market,
                    calculation_revision=result.calculation_revision,
                )
            )

        ratios_by_date = calculate_inclusive_ratios(
            daily_counts,
            request.seed_counts,
            market=request.market.upper(),
            calculation_revision=request.policy.calculation_revision,
        )
        return {
            calculation_date: replace(
                result,
                values=replace(
                    result.values,
                    ratio_5day=ratios_by_date[calculation_date].ratio_5day,
                    ratio_10day=ratios_by_date[calculation_date].ratio_10day,
                ),
            )
            for calculation_date, result in partial_results.items()
        }
