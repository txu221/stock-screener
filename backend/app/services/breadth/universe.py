"""Point-in-time universe and historical-FX adapters for breadth calculations."""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Iterable, Mapping
from datetime import date

import numpy as np
import pandas as pd

from app.models.stock_universe import StockUniverse
from app.services.point_in_time_universe_service import PointInTimeUniverseService

from .formulas import signal_flags_at
from .types import (
    BreadthFormulaPolicy,
    BreadthUniverseMember,
    BreadthUniverseSnapshot,
    SymbolMetricEligibility,
)

BREADTH_ELIGIBILITY_SIGNATURE_VERSION = "point-in-time-common-stock-v2"


def breadth_eligibility_signature(symbols: Iterable[str]) -> str:
    """Hash canonical broad-universe membership under the current policy."""
    canonical_symbols = tuple(sorted(set(symbols)))
    payload = "".join(
        (
            f"{BREADTH_ELIGIBILITY_SIGNATURE_VERSION}\n",
            *(f"{symbol}\n" for symbol in canonical_symbols),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class MissingHistoricalFXError(RuntimeError):
    def __init__(self, currency: str, calculation_date: date) -> None:
        self.currency = currency
        self.calculation_date = calculation_date
        super().__init__(
            f"Missing historical {currency}->USD FX rate for "
            f"{calculation_date.isoformat()}"
        )


def resolve_historical_fx_series(
    currency: str,
    calculation_dates: Collection[date],
    observations: Mapping[date, float],
    *,
    max_age_days: int,
) -> pd.Series:
    normalized_currency = currency.strip().upper()
    requested = tuple(calculation_dates)
    index = pd.DatetimeIndex(requested)
    if normalized_currency == "USD":
        return pd.Series(1.0, index=index, dtype=float)

    valid_observations = sorted(
        (
            observation_date,
            float(rate),
        )
        for observation_date, rate in observations.items()
        if rate is not None and np.isfinite(float(rate)) and float(rate) > 0
    )

    resolved: list[float] = []
    for requested_date in requested:
        prior = [item for item in valid_observations if item[0] <= requested_date]
        if not prior:
            raise MissingHistoricalFXError(normalized_currency, requested_date)
        observation_date, rate = prior[-1]
        if (requested_date - observation_date).days > max_age_days:
            raise MissingHistoricalFXError(normalized_currency, requested_date)
        resolved.append(rate)

    return pd.Series(resolved, index=index, dtype=float)


def _snapshot_members(db, point_in_time) -> tuple[BreadthUniverseMember, ...]:
    source_members = point_in_time.members
    if not source_members and point_in_time.symbols:
        rows = (
            db.query(StockUniverse)
            .filter(StockUniverse.symbol.in_(point_in_time.symbols))
            .order_by(StockUniverse.symbol.asc())
            .all()
        )
        source_members = tuple(
            BreadthUniverseMember(
                symbol=row.symbol,
                currency=row.currency,
                is_common_stock=row.is_common_stock,
            )
            for row in rows
        )

    return tuple(
        BreadthUniverseMember(
            symbol=member.symbol,
            currency=member.currency,
            is_common_stock=member.is_common_stock,
        )
        for member in source_members
        if member.is_common_stock
    )


def build_breadth_universe_snapshots(
    db,
    market: str,
    dates: Collection[date],
    *,
    universe_service: PointInTimeUniverseService | None = None,
) -> Mapping[date, BreadthUniverseSnapshot]:
    from app.services.point_in_time_universe_service import (
        hash_point_in_time_universe_symbols,
    )

    resolver = universe_service or PointInTimeUniverseService()
    snapshots: dict[date, BreadthUniverseSnapshot] = {}
    for calculation_date in dates:
        point_in_time = resolver.resolve(
            db,
            market=market,
            as_of_date=calculation_date,
        )
        members = tuple(
            sorted(
                _snapshot_members(db, point_in_time),
                key=lambda item: item.symbol,
            )
        )
        symbols = tuple(member.symbol for member in members)
        snapshots[calculation_date] = BreadthUniverseSnapshot(
            calculation_date=calculation_date,
            members=members,
            broad_signature=hash_point_in_time_universe_symbols(symbols),
        )
    return snapshots


def classify_metric_eligibility(
    member: BreadthUniverseMember,
    features: pd.DataFrame,
    policy: BreadthFormulaPolicy,
    *,
    calculation_date: date | None = None,
) -> SymbolMetricEligibility:
    if not member.is_common_stock or features.empty:
        return SymbolMetricEligibility()
    target_date = calculation_date or pd.Timestamp(features.index[-1]).date()
    return signal_flags_at(features, target_date, policy).eligibility


def stockbee_eligible_symbols(
    snapshot: BreadthUniverseSnapshot,
    features_by_symbol: Mapping[str, pd.DataFrame],
    policy: BreadthFormulaPolicy,
) -> tuple[str, ...]:
    eligible: list[str] = []
    for member in snapshot.members:
        features = features_by_symbol.get(member.symbol)
        if features is None:
            continue
        metric_eligibility = classify_metric_eligibility(
            member,
            features,
            policy,
            calculation_date=snapshot.calculation_date,
        )
        if metric_eligibility.stockbee_daily:
            eligible.append(member.symbol)
    return tuple(sorted(eligible))


def stockbee_eligibility_signature(
    snapshot: BreadthUniverseSnapshot,
    features_by_symbol: Mapping[str, pd.DataFrame],
    policy: BreadthFormulaPolicy,
) -> str:
    from app.services.point_in_time_universe_service import (
        hash_point_in_time_universe_symbols,
    )

    return hash_point_in_time_universe_symbols(
        stockbee_eligible_symbols(snapshot, features_by_symbol, policy)
    )
