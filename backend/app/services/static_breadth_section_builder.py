"""Static breadth payload assembly extracted from the main exporter."""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.services.bounded_history_universe import (
    CurrentActiveFallbackUniverseResolver,
)
from app.services.breadth.engine import BreadthEngine, BreadthEngineRequest
from app.services.breadth.formulas import prices_for_feature_window
from app.services.breadth.types import (
    CURRENT_BREADTH_CALCULATION_REVISION,
    BreadthUniverseMember,
    BreadthUniverseSnapshot,
)
from app.services.breadth.universe import build_breadth_universe_snapshots
from app.services.breadth_attribution_service import BreadthAttributionService
from app.services.fx_service import default_currency_for_market, get_fx_service
from app.services.point_in_time_universe_service import (
    hash_point_in_time_universe_symbols,
)
from app.services.static_market_artifact_contract import STATIC_SITE_SCHEMA_VERSION
from app.services.static_site_errors import StaticSiteSectionUnavailableError

STATIC_BREADTH_HISTORY_LOOKBACK_DAYS = 90
STATIC_BREADTH_ATTRIBUTION_LOOKBACK_DAYS = 10
STATIC_BREADTH_ATTRIBUTION_MARKETS = ("US",)
STATIC_DEFAULT_MARKET = "US"
STATIC_CHART_LOOKUP_BATCH_SIZE = 250


@dataclass(frozen=True, slots=True)
class StaticBreadthEngineInputs:
    request: BreadthEngineRequest
    currencies_by_symbol: Mapping[str, str]


class StaticBreadthEngineInputFactory:
    """Build cache-only canonical inputs for static breadth generation."""

    def __init__(self, *, fx_service=None) -> None:
        self._fx_service = fx_service

    def build(
        self,
        *,
        market: str,
        canonical_dates: list[date],
        price_data: Mapping[str, pd.DataFrame | None],
        currencies_by_symbol: Mapping[str, str] | None = None,
        universes_by_date: Mapping[date, BreadthUniverseSnapshot] | None = None,
    ) -> StaticBreadthEngineInputs:
        normalized_market = market.upper()
        default_currency = default_currency_for_market(normalized_market)
        symbols = tuple(sorted(str(symbol).upper() for symbol in price_data))
        currency_map = {
            symbol: str(
                (currencies_by_symbol or {}).get(symbol) or default_currency
            ).upper()
            for symbol in symbols
        }
        if universes_by_date is None:
            members = tuple(
                BreadthUniverseMember(symbol=symbol, currency=currency_map[symbol])
                for symbol in symbols
            )
            signature = hash_point_in_time_universe_symbols(symbols)
            universes = {
                calculation_date: BreadthUniverseSnapshot(
                    calculation_date=calculation_date,
                    members=members,
                    broad_signature=signature,
                )
                for calculation_date in canonical_dates
            }
        else:
            missing_dates = set(canonical_dates) - set(universes_by_date)
            if missing_dates:
                missing = ", ".join(
                    value.isoformat() for value in sorted(missing_dates)
                )
                raise ValueError(f"Static breadth universes missing for: {missing}")
            universes = {
                calculation_date: universes_by_date[calculation_date]
                for calculation_date in canonical_dates
            }
        usable_prices = prices_for_feature_window(
            {
                symbol: history
                for symbol, history in price_data.items()
                if history is not None and not history.empty
            },
            tuple(canonical_dates),
        )
        dates_by_currency: dict[str, set[date]] = {}
        for symbol, history in usable_prices.items():
            currency = currency_map[str(symbol).upper()]
            dates_by_currency.setdefault(currency, set()).update(
                pd.Timestamp(value).date() for value in history.index
            )
        fx_by_currency: dict[str, pd.Series] = {}
        for currency, required_dates in dates_by_currency.items():
            if currency == "USD":
                fx_by_currency[currency] = pd.Series(
                    1.0,
                    index=pd.DatetimeIndex(sorted(required_dates)),
                )
                continue
            fx_service = self._fx_service or get_fx_service()
            fx_by_currency.update(
                fx_service.get_historical_usd_rates(
                    (currency,),
                    required_dates,
                )
            )
        return StaticBreadthEngineInputs(
            request=BreadthEngineRequest(
                market=normalized_market,
                dates=tuple(canonical_dates),
                universes_by_date=universes,
                prices_by_symbol=usable_prices,
                fx_by_currency=fx_by_currency,
            ),
            currencies_by_symbol=currency_map,
        )


def _coerce_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class StaticBreadthSectionBuilder:
    def __init__(
        self,
        *,
        ui_snapshot_service,
        price_cache,
        benchmark_cache,
        engine: BreadthEngine | None = None,
        engine_input_factory: StaticBreadthEngineInputFactory | None = None,
        universe_resolver=None,
    ) -> None:
        self._ui_snapshot_service = ui_snapshot_service
        self._price_cache = price_cache
        self._benchmark_cache = benchmark_cache
        self._engine = engine or BreadthEngine()
        self._engine_input_factory = (
            engine_input_factory or StaticBreadthEngineInputFactory()
        )
        self._universe_resolver = (
            universe_resolver or CurrentActiveFallbackUniverseResolver()
        )

    def build(self, **kwargs):
        return self._build_breadth_payload(**kwargs)

    def _build_breadth_payload(
        self,
        *,
        generated_at: str,
        expected_as_of_date: date,
        market: str = STATIC_DEFAULT_MARKET,
        serialized_rows: list[dict[str, Any]] | None = None,
        db: Session | None = None,
    ) -> dict[str, Any]:
        if serialized_rows is None:
            snapshot = self._ui_snapshot_service.publish_breadth_bootstrap(market=market).to_dict()
            payload = snapshot.get("payload", {})
            current_date = ((payload.get("current") or {}).get("date"))
            if current_date != expected_as_of_date.isoformat():
                raise StaticSiteSectionUnavailableError(
                    section="breadth",
                    reason=(
                        "No breadth snapshot is available for static-site export date "
                        f"{expected_as_of_date.isoformat()} (latest snapshot date: {current_date or 'none'})."
                    ),
                )
            return {
                "schema_version": STATIC_SITE_SCHEMA_VERSION,
                "generated_at": generated_at,
                "available": True,
                "published_at": _coerce_datetime(snapshot.get("published_at")),
                "source_revision": snapshot.get("source_revision"),
                "payload": payload,
            }

        scan_symbols = [
            str(row["symbol"]).upper()
            for row in serialized_rows
            if row.get("symbol")
        ]
        currencies_by_symbol: dict[str, str] = {}
        universes_by_date: dict[date, BreadthUniverseSnapshot] | None = None
        symbols = scan_symbols
        if db is not None:
            universe = self._universe_resolver.resolve(
                db,
                market=market,
                as_of_date=expected_as_of_date,
            )
            symbols = list(universe.symbols)
            currencies_by_symbol = {
                member.symbol: member.currency
                for member in universe.members
            }
        if not symbols:
            raise StaticSiteSectionUnavailableError(
                section="breadth",
                reason=(
                    f"No common-stock universe is available for market {market} "
                    f"on {expected_as_of_date.isoformat()}."
                ),
            )

        benchmark_symbol, benchmark = self._get_market_benchmark_history(market, period="1y")
        if benchmark.empty:
            raise StaticSiteSectionUnavailableError(
                section="breadth",
                reason=f"No cached benchmark price history is available for market {market}.",
            )

        canonical_dates = [
            ts.date()
            for ts in pd.to_datetime(benchmark.index)
            if ts.date() <= expected_as_of_date
        ]
        if expected_as_of_date not in canonical_dates:
            raise StaticSiteSectionUnavailableError(
                section="breadth",
                reason=(
                    f"No benchmark trading session is available for market {market} "
                    f"on {expected_as_of_date.isoformat()}."
                ),
            )

        canonical_dates = canonical_dates[-max(STATIC_BREADTH_HISTORY_LOOKBACK_DAYS + 15, 120):]
        if db is not None:
            universes_by_date = dict(
                build_breadth_universe_snapshots(
                    db,
                    market,
                    canonical_dates,
                    universe_service=self._universe_resolver,
                )
            )
            symbols = sorted(
                {
                    member.symbol
                    for universe in universes_by_date.values()
                    for member in universe.members
                }
            )
            currencies_by_symbol = {
                member.symbol: member.currency
                for universe in universes_by_date.values()
                for member in universe.members
            }
        price_data = self._get_cached_price_histories(symbols, period="2y")
        engine_inputs = self._engine_input_factory.build(
            market=market,
            canonical_dates=canonical_dates,
            price_data=price_data,
            currencies_by_symbol=currencies_by_symbol,
            universes_by_date=universes_by_date,
        )
        metrics_by_date = self._compute_breadth_metrics_by_date(
            canonical_dates,
            price_data,
            market=market,
            engine_inputs=engine_inputs,
        )
        current = metrics_by_date.get(expected_as_of_date)
        if current is None:
            raise StaticSiteSectionUnavailableError(
                section="breadth",
                reason=f"No breadth snapshot could be derived for market {market} on {expected_as_of_date.isoformat()}.",
            )

        ordered_dates = sorted(metrics_by_date.keys())
        ordered_history = [
            {**metrics_by_date[item_date], "market": market}
            for item_date in ordered_dates
        ]
        chart_data = ordered_history[-31:]
        current = {**current, "market": market}
        benchmark_overlay = self._serialize_history_bars(
            benchmark,
            period_days=31,
            end_date=expected_as_of_date,
        )
        group_attribution = self._build_group_attribution(
            market=market,
            serialized_rows=serialized_rows,
            ordered_dates=ordered_dates,
            engine_inputs=engine_inputs,
        )
        return {
            "schema_version": STATIC_SITE_SCHEMA_VERSION,
            "generated_at": generated_at,
            "available": True,
            "published_at": _coerce_datetime(datetime.utcnow()),
            "source_revision": (
                f"feature-run:{market}:{expected_as_of_date.isoformat()}"
                f"|breadth-r{CURRENT_BREADTH_CALCULATION_REVISION}"
            ),
            "market": market,
            "payload": {
                "current": current,
                "summary": {
                    "market": market,
                    "latest_date": expected_as_of_date.isoformat(),
                    "total_records": len(ordered_history),
                    "date_range_start": ordered_dates[0].isoformat() if ordered_dates else None,
                    "date_range_end": ordered_dates[-1].isoformat() if ordered_dates else None,
                },
                "history_90d": list(reversed(ordered_history[-STATIC_BREADTH_HISTORY_LOOKBACK_DAYS:])),
                "chart_range": "1M",
                "chart_data": list(reversed(chart_data)),
                "benchmark_symbol": benchmark_symbol,
                "benchmark_overlay": benchmark_overlay,
                "spy_overlay": benchmark_overlay,
                "group_attribution": group_attribution,
            },
        }

    def _build_group_attribution(
        self,
        *,
        market: str,
        serialized_rows: list[dict[str, Any]],
        ordered_dates: list[date],
        engine_inputs: StaticBreadthEngineInputs,
    ) -> dict[str, Any]:
        """Attribute ±4% movers to IBD industry groups for the most recent sessions.

        Only enabled for markets in ``STATIC_BREADTH_ATTRIBUTION_MARKETS`` — non-US
        taxonomies aren't wired in for the first cut. Returns an
        ``{available: False, reason}`` payload when skipped so the static client
        can hide the feature cleanly.
        """
        if market not in STATIC_BREADTH_ATTRIBUTION_MARKETS:
            return {
                "available": False,
                "reason": f"Group attribution is not yet supported for market {market}.",
            }
        if not ordered_dates:
            return {
                "available": False,
                "reason": "No trading dates were available to attribute.",
            }

        attribution_dates = ordered_dates[-STATIC_BREADTH_ATTRIBUTION_LOOKBACK_DAYS:]
        metadata_by_symbol = {
            str(row["symbol"]).upper(): {
                "symbol": str(row["symbol"]).upper(),
                "company_name": row.get("company_name"),
                "ibd_industry_group": row.get("ibd_industry_group"),
            }
            for row in serialized_rows
            if row.get("symbol")
        }
        price_data = engine_inputs.request.prices_by_symbol
        symbols_meta = [
            metadata_by_symbol.get(symbol, {"symbol": symbol})
            for symbol in price_data
        ]
        service = BreadthAttributionService()
        history = service.compute(
            symbols_meta=symbols_meta,
            price_data=price_data,
            target_dates=attribution_dates,
            currencies_by_symbol=engine_inputs.currencies_by_symbol,
            fx_by_currency=engine_inputs.request.fx_by_currency,
            symbols_by_date={
                calculation_date: frozenset(
                    member.symbol
                    for member in universe.members
                )
                for calculation_date, universe in (
                    engine_inputs.request.universes_by_date.items()
                )
            },
        )
        has_any_mover = any(
            (day.get("stocks_up_4pct", 0) + day.get("stocks_down_4pct", 0)) > 0
            for day in history
        )
        if not history or not has_any_mover:
            return {
                "available": False,
                "reason": "No 4%+ movers were attributable for the lookback window.",
            }

        latest = history[-1]
        return {
            "available": True,
            "market": market,
            "threshold_pct": 4.0,
            "lookback_days": STATIC_BREADTH_ATTRIBUTION_LOOKBACK_DAYS,
            "latest_date": latest["date"] if latest else None,
            "history": list(reversed(history)),
        }

    def _get_cached_price_histories(
        self,
        symbols: list[str],
        *,
        period: str,
    ) -> dict[str, pd.DataFrame | None]:
        results: dict[str, pd.DataFrame | None] = {
            str(symbol).upper(): None for symbol in symbols
        }
        for start in range(0, len(symbols), STATIC_CHART_LOOKUP_BATCH_SIZE):
            batch = symbols[start:start + STATIC_CHART_LOOKUP_BATCH_SIZE]
            results.update(self._price_cache.get_many_cached_only(batch, period=period))
        return results

    def _get_market_benchmark_history(self, market: str, *, period: str) -> tuple[str, pd.DataFrame]:
        for candidate in self._benchmark_cache.get_benchmark_candidates(market):
            history = self._get_symbol_price_history(candidate, period=period)
            if history is not None and not history.empty:
                return candidate, history
        return self._benchmark_cache.get_benchmark_symbol(market), pd.DataFrame()

    def _get_symbol_price_history(self, symbol: str, *, period: str) -> pd.DataFrame | None:
        data = self._price_cache.get_cached_only(symbol.upper(), period=period)
        if data is None or data.empty:
            return None
        return data

    def _compute_breadth_metrics_by_date(
        self,
        canonical_dates: list[date],
        price_data: dict[str, pd.DataFrame | None],
        *,
        market: str = STATIC_DEFAULT_MARKET,
        engine_inputs: StaticBreadthEngineInputs | None = None,
    ) -> dict[date, dict[str, Any]]:
        if not canonical_dates:
            return {}
        inputs = engine_inputs or self._engine_input_factory.build(
            market=market,
            canonical_dates=canonical_dates,
            price_data=price_data,
        )
        calculated = self._engine.calculate(inputs.request)
        return {
            item_date: {
                **result.to_record_mapping(),
                "date": item_date.isoformat(),
            }
            for item_date, result in calculated.items()
        }

    @staticmethod
    def _serialize_close_history(data: pd.DataFrame | None, *, days: int) -> list[dict[str, Any]]:
        if data is None or data.empty or "Close" not in data.columns:
            return []
        frame = data.tail(days).reset_index()
        date_col = frame.columns[0]
        frame = frame.rename(columns={date_col: "Date"})
        frame["Date"] = pd.to_datetime(frame["Date"]).dt.strftime("%Y-%m-%d")
        return [
            {
                "date": row["Date"],
                "close": round(float(row["Close"]), 2),
            }
            for _, row in frame.iterrows()
            if row["Close"] is not None and not math.isnan(float(row["Close"]))
        ]

    @staticmethod
    def _serialize_history_bars(
        data: pd.DataFrame | None,
        *,
        period_days: int,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        if data is None or data.empty:
            return []
        end_timestamp = pd.Timestamp(end_date or datetime.utcnow())
        cutoff_date = end_timestamp - timedelta(days=period_days)
        if data.index.tz is not None:
            cutoff_date = cutoff_date.tz_localize(data.index.tz)
            end_timestamp = end_timestamp.tz_localize(data.index.tz)
        filtered = data[(data.index >= cutoff_date) & (data.index <= end_timestamp)]
        if filtered.empty:
            return []
        frame = filtered.reset_index()
        date_col = frame.columns[0]
        frame = frame.rename(columns={date_col: "Date"})
        frame["Date"] = pd.to_datetime(frame["Date"]).dt.strftime("%Y-%m-%d")
        return [
            {
                "date": row["Date"],
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(row["Close"]), 2),
                "volume": int(row["Volume"]),
            }
            for _, row in frame.iterrows()
            if all(
                value is not None and not math.isnan(float(value))
                for value in (row["Open"], row["High"], row["Low"], row["Close"], row["Volume"])
            )
        ]
