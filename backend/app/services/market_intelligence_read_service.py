"""Read-only assembly service for the user-facing Market Intelligence MVP."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import math
from typing import Iterable

from sqlalchemy.orm import Session

from app.domain.market_intelligence.mvp import (
    ETF_CATEGORIES,
    ETF_STRENGTH_VERSION,
    ETF_UNIVERSE,
    MVP_METRIC_VERSION,
    MVP_PRICE_BASIS,
    MVP_PRICE_HISTORY_QUALITY,
    PULSE_SYMBOLS,
    EtfRadar,
    EtfStrengthItem,
    MarketOverview,
    MarketPulseItem,
    MoverItem,
    MoverSectorSummary,
    MoverSummary,
    calculate_price_metrics,
    categories_for_symbol,
    score_and_rank_etfs,
)
from app.domain.market_intelligence.freshness import (
    classify_completed_session_freshness,
)
from app.domain.scanning.default_filters import resolve_default_scan_filters
from app.infra.db.models.feature_store import (
    FeatureRun,
    FeatureRunPointer,
    StockFeatureDaily,
)
from app.models.stock import StockPrice
from app.models.stock_universe import StockUniverse
from app.services.market_calendar_service import MarketCalendarService


PRICE_SOURCE = "existing_stock_prices"
US_PUBLISHED_POINTER = "latest_published_market:US"
DEFAULT_MIN_PRICE = 5.0
DEFAULT_MIN_AVERAGE_DOLLAR_VOLUME = float(
    resolve_default_scan_filters("US")["minVolume"] or 0
)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _finite_number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class MarketIntelligenceReadService:
    """Build deterministic read models exclusively from existing local tables."""

    def __init__(
        self,
        session: Session,
        *,
        completed_session: date | None = None,
        completed_sessions: Iterable[date] | None = None,
    ):
        self._session = session
        self._completed_session = completed_session
        self._completed_session_dates = (
            None
            if completed_sessions is None
            else tuple(sorted(set(completed_sessions)))
        )

    def _price_rows(
        self,
        symbols: Iterable[str],
        *,
        as_of: date,
        calendar_days: int,
    ) -> dict[str, list[StockPrice]]:
        normalized = tuple(dict.fromkeys(symbols))
        if not normalized:
            return {}
        rows = (
            self._session.query(StockPrice)
            .filter(
                StockPrice.symbol.in_(normalized),
                StockPrice.date >= as_of - timedelta(days=calendar_days),
                StockPrice.date <= as_of,
            )
            .order_by(StockPrice.symbol.asc(), StockPrice.date.asc())
            .all()
        )
        grouped: dict[str, list[StockPrice]] = defaultdict(list)
        for row in rows:
            grouped[row.symbol].append(row)
        return dict(grouped)

    def _expected_session(self) -> date:
        if self._completed_session_dates:
            return self._completed_session_dates[-1]
        if self._completed_session is not None:
            return self._completed_session
        return MarketCalendarService().last_completed_trading_day("US")

    def _freshness_status(self, *, as_of: date | None, expected_session: date) -> str:
        if self._completed_session_dates is not None:
            sessions = self._completed_session_dates
        elif self._completed_session is not None:
            calendar = MarketCalendarService()
            sessions = tuple(
                calendar.trading_days(
                    "US",
                    self._completed_session - timedelta(days=14),
                    self._completed_session,
                )
            )
        else:
            calendar = MarketCalendarService()
            sessions = tuple(
                calendar.trading_days(
                    "US",
                    expected_session - timedelta(days=14),
                    expected_session,
                )
            )
        return classify_completed_session_freshness(as_of, sessions)

    def get_overview(self) -> MarketOverview:
        expected_session = self._expected_session()
        run = self._published_us_run()
        if run is None:
            return MarketOverview(
                as_of=None,
                last_updated=None,
                provider=PRICE_SOURCE,
                metric_version=MVP_METRIC_VERSION,
                price_basis=MVP_PRICE_BASIS,
                price_history_quality=MVP_PRICE_HISTORY_QUALITY,
                expected_session=expected_session,
                freshness_status="UNAVAILABLE",
                market_status=None,
                pulse=tuple(
                    MarketPulseItem(symbol=symbol, available=False)
                    for symbol in PULSE_SYMBOLS
                ),
                missing_symbols=PULSE_SYMBOLS,
                unavailable_reason="no_published_us_feature_run",
            )
        as_of = run.as_of_date

        grouped = self._price_rows(
            PULSE_SYMBOLS,
            as_of=as_of,
            calendar_days=120,
        )
        pulse: list[MarketPulseItem] = []
        missing: list[str] = []
        for symbol in PULSE_SYMBOLS:
            metrics = calculate_price_metrics(grouped.get(symbol, ()), as_of=as_of)
            if not metrics.available:
                missing.append(symbol)
            pulse.append(
                MarketPulseItem(
                    symbol=symbol,
                    available=metrics.available,
                    price=metrics.price,
                    return_1d=metrics.return_1d,
                    return_5d=metrics.return_5d,
                    return_20d=metrics.return_20d,
                    return_60d=metrics.return_60d,
                )
            )
        return MarketOverview(
            as_of=as_of,
            last_updated=_as_utc(run.published_at),
            provider=PRICE_SOURCE,
            metric_version=MVP_METRIC_VERSION,
            price_basis=MVP_PRICE_BASIS,
            price_history_quality=MVP_PRICE_HISTORY_QUALITY,
            expected_session=expected_session,
            freshness_status=self._freshness_status(
                as_of=as_of,
                expected_session=expected_session,
            ),
            market_status=None,
            pulse=tuple(pulse),
            missing_symbols=tuple(missing),
        )

    def _published_us_run(self) -> FeatureRun | None:
        pointer = self._session.get(FeatureRunPointer, US_PUBLISHED_POINTER)
        if pointer is None:
            return None
        run = self._session.get(FeatureRun, pointer.run_id)
        if run is None or run.status != "published" or run.published_at is None:
            return None
        return run

    def get_movers(
        self,
        *,
        limit: int = 20,
        sector: str | None = None,
        direction: str | None = None,
        min_price: float = DEFAULT_MIN_PRICE,
        min_rvol: float | None = None,
        search: str | None = None,
        market_cap_group: str | None = None,
    ) -> MoverSummary:
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        if min_price < 0:
            raise ValueError("min_price cannot be negative")
        if min_rvol is not None and min_rvol < 0:
            raise ValueError("min_rvol cannot be negative")
        normalized_direction = (direction or "all").strip().lower()
        if normalized_direction not in {"all", "gainers", "losers"}:
            raise ValueError("direction must be all, gainers, or losers")
        if market_cap_group not in {None, "mega", "large", "mid", "small"}:
            raise ValueError("unsupported market_cap_group")

        expected_session = self._expected_session()
        run = self._published_us_run()
        if run is None:
            return MoverSummary(
                as_of=None,
                published_at=None,
                provider=PRICE_SOURCE,
                metric_version=MVP_METRIC_VERSION,
                price_basis=MVP_PRICE_BASIS,
                price_history_quality=MVP_PRICE_HISTORY_QUALITY,
                expected_session=expected_session,
                freshness_status="UNAVAILABLE",
                eligible_count=0,
                gainers=(),
                losers=(),
                unusual_volume=(),
                sectors=(),
                unavailable_reason="no_published_us_feature_run",
            )

        feature_rows = (
            self._session.query(StockFeatureDaily)
            .filter(StockFeatureDaily.run_id == run.id)
            .all()
        )
        features = {row.symbol: row for row in feature_rows}
        if not features:
            return MoverSummary(
                as_of=run.as_of_date,
                published_at=_as_utc(run.published_at),
                provider=PRICE_SOURCE,
                metric_version=MVP_METRIC_VERSION,
                price_basis=MVP_PRICE_BASIS,
                price_history_quality=MVP_PRICE_HISTORY_QUALITY,
                expected_session=expected_session,
                freshness_status=self._freshness_status(
                    as_of=run.as_of_date,
                    expected_session=expected_session,
                ),
                eligible_count=0,
                gainers=(),
                losers=(),
                unusual_volume=(),
                sectors=(),
                unavailable_reason="published_run_has_no_features",
            )

        universe_rows = (
            self._session.query(StockUniverse)
            .filter(
                StockUniverse.symbol.in_(tuple(features)),
                StockUniverse.market == "US",
                StockUniverse.is_sp500.is_(True),
                StockUniverse.active_filter(),
            )
            .all()
        )
        eligible_metadata: dict[str, tuple[StockUniverse, float]] = {}
        for row in universe_rows:
            details = features[row.symbol].details_json
            details = details if isinstance(details, dict) else {}
            average_dollar_volume = _finite_number(details.get("avg_dollar_volume"))
            if (
                average_dollar_volume is None
                or average_dollar_volume < DEFAULT_MIN_AVERAGE_DOLLAR_VOLUME
            ):
                continue
            eligible_metadata[row.symbol] = (row, average_dollar_volume)

        grouped = self._price_rows(
            eligible_metadata,
            as_of=run.as_of_date,
            calendar_days=60,
        )
        items: list[MoverItem] = []
        normalized_search = (search or "").strip().upper()
        normalized_sector = (sector or "").strip().casefold()
        for symbol, (metadata, average_dollar_volume) in eligible_metadata.items():
            metrics = calculate_price_metrics(grouped.get(symbol, ()), as_of=run.as_of_date)
            if (
                not metrics.available
                or metrics.price is None
                or metrics.price <= min_price
                or metrics.return_1d is None
            ):
                continue
            if min_rvol is not None and (
                metrics.rvol20 is None or metrics.rvol20 < min_rvol
            ):
                continue
            if normalized_sector and (metadata.sector or "").casefold() != normalized_sector:
                continue
            if normalized_search and normalized_search not in symbol.upper() and normalized_search not in (metadata.name or "").upper():
                continue
            if normalized_direction == "gainers" and metrics.return_1d <= 0:
                continue
            if normalized_direction == "losers" and metrics.return_1d >= 0:
                continue
            market_cap = _finite_number(metadata.market_cap)
            if not self._market_cap_matches(market_cap, market_cap_group):
                continue
            items.append(
                MoverItem(
                    symbol=symbol,
                    company_name=metadata.name,
                    price=metrics.price,
                    change_1d=metrics.return_1d,
                    volume=metrics.volume,
                    rvol20=metrics.rvol20,
                    average_dollar_volume=average_dollar_volume,
                    sector=metadata.sector,
                    industry=metadata.industry,
                    market_cap=market_cap,
                )
            )

        gainers = tuple(
            sorted(
                (item for item in items if item.change_1d > 0),
                key=lambda item: (-item.change_1d, item.symbol),
            )[:limit]
        )
        losers = tuple(
            sorted(
                (item for item in items if item.change_1d < 0),
                key=lambda item: (item.change_1d, item.symbol),
            )[:limit]
        )
        unusual = tuple(
            sorted(
                (item for item in items if item.rvol20 is not None),
                key=lambda item: (-float(item.rvol20), item.symbol),
            )[:limit]
        )
        sectors = self._sector_summaries(items)
        return MoverSummary(
            as_of=run.as_of_date,
            published_at=_as_utc(run.published_at),
            provider=PRICE_SOURCE,
            metric_version=MVP_METRIC_VERSION,
            price_basis=MVP_PRICE_BASIS,
            price_history_quality=MVP_PRICE_HISTORY_QUALITY,
            expected_session=expected_session,
            freshness_status=self._freshness_status(
                as_of=run.as_of_date,
                expected_session=expected_session,
            ),
            eligible_count=len(items),
            gainers=gainers,
            losers=losers,
            unusual_volume=unusual,
            sectors=sectors,
        )

    @staticmethod
    def _market_cap_matches(value: float | None, group: str | None) -> bool:
        if group is None:
            return True
        market_cap = _finite_number(value)
        if market_cap is None:
            return False
        if group == "mega":
            return market_cap >= 200_000_000_000
        if group == "large":
            return 10_000_000_000 <= market_cap < 200_000_000_000
        if group == "mid":
            return 2_000_000_000 <= market_cap < 10_000_000_000
        return market_cap < 2_000_000_000

    @staticmethod
    def _sector_summaries(items: Iterable[MoverItem]) -> tuple[MoverSectorSummary, ...]:
        counts: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
        for item in items:
            sector = item.sector or "Unknown"
            if item.change_1d > 0:
                counts[sector][0] += 1
            elif item.change_1d < 0:
                counts[sector][1] += 1
            else:
                counts[sector][2] += 1
        return tuple(
            MoverSectorSummary(
                sector=sector,
                advancers=values[0],
                decliners=values[1],
                unchanged=values[2],
                total=sum(values),
            )
            for sector, values in sorted(
                counts.items(),
                key=lambda pair: (-sum(pair[1]), pair[0]),
            )
        )

    def get_etf_radar(self, *, category: str = "all") -> EtfRadar:
        normalized_category = category.strip().lower()
        if normalized_category != "all" and normalized_category not in ETF_CATEGORIES:
            raise ValueError("unsupported ETF category")

        selected_symbols = (
            ETF_UNIVERSE
            if normalized_category == "all"
            else ETF_CATEGORIES[normalized_category]
        )
        expected_session = self._expected_session()
        run = self._published_us_run()
        if run is None:
            return EtfRadar(
                as_of=None,
                last_updated=None,
                provider=PRICE_SOURCE,
                metric_version=MVP_METRIC_VERSION,
                price_basis=MVP_PRICE_BASIS,
                price_history_quality=MVP_PRICE_HISTORY_QUALITY,
                expected_session=expected_session,
                freshness_status="UNAVAILABLE",
                score_version=ETF_STRENGTH_VERSION,
                category=normalized_category,
                items=tuple(
                    EtfStrengthItem(
                        symbol=symbol,
                        categories=categories_for_symbol(symbol),
                        available=False,
                    )
                    for symbol in selected_symbols
                ),
                missing_symbols=tuple(selected_symbols),
                unavailable_reason="no_published_us_feature_run",
            )
        as_of = run.as_of_date

        grouped = self._price_rows(
            ETF_UNIVERSE,
            as_of=as_of,
            calendar_days=120,
        )
        price_metrics = {
            symbol: calculate_price_metrics(grouped.get(symbol, ()), as_of=as_of)
            for symbol in ETF_UNIVERSE
        }
        spy = price_metrics["SPY"]

        def relative(value: float | None, benchmark: float | None) -> float | None:
            if value is None or benchmark is None:
                return None
            return value - benchmark

        all_items = tuple(
            EtfStrengthItem(
                symbol=symbol,
                categories=categories_for_symbol(symbol),
                available=metrics.available,
                price=metrics.price,
                return_1d=metrics.return_1d,
                return_5d=metrics.return_5d,
                return_20d=metrics.return_20d,
                return_60d=metrics.return_60d,
                relative_strength_1d=relative(metrics.return_1d, spy.return_1d),
                relative_strength_5d=relative(metrics.return_5d, spy.return_5d),
                relative_strength_20d=relative(metrics.return_20d, spy.return_20d),
                relative_strength_60d=relative(metrics.return_60d, spy.return_60d),
                rvol20=metrics.rvol20,
                drawdown_60d=metrics.drawdown_60d,
            )
            for symbol in ETF_UNIVERSE
            for metrics in (price_metrics[symbol],)
        )
        scored = {item.symbol: item for item in score_and_rank_etfs(all_items)}
        items = tuple(scored[symbol] for symbol in selected_symbols)
        return EtfRadar(
            as_of=as_of,
            last_updated=_as_utc(run.published_at),
            provider=PRICE_SOURCE,
            metric_version=MVP_METRIC_VERSION,
            price_basis=MVP_PRICE_BASIS,
            price_history_quality=MVP_PRICE_HISTORY_QUALITY,
            expected_session=expected_session,
            freshness_status=self._freshness_status(
                as_of=as_of,
                expected_session=expected_session,
            ),
            score_version=ETF_STRENGTH_VERSION,
            category=normalized_category,
            items=items,
            missing_symbols=tuple(
                symbol for symbol in selected_symbols if not scored[symbol].available
            ),
        )
