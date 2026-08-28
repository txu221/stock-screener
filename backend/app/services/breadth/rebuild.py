"""Shadow rebuild, validation, and atomic cutover for breadth revision 2."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from datetime import date
from types import MappingProxyType
from typing import Any

import pandas as pd
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.models.market_breadth import MarketBreadth
from app.services.breadth_backfill import (
    BreadthBackfillExecutor,
    BreadthBackfillPlan,
    BreadthEligibleUniverse,
)
from app.services.breadth_calculator_service import BreadthCalculatorService
from app.services.derived_data_execution_policy import (
    DerivedDataExecutionMode,
    DerivedDataExecutionPolicy,
    DerivedDataTargetKind,
)
from app.services.point_in_time_universe_service import PointInTimeUniverseService

from .types import CURRENT_BREADTH_CALCULATION_REVISION, BreadthDailyResult

TARGET_TABLE = "market_breadth"
STAGING_TABLE = "market_breadth_rebuild"
MANIFEST_TABLE = "market_breadth_rebuild_manifest"
_EXCLUDED_COPY_COLUMNS = {"id", "created_at"}


def _copy_columns() -> tuple[str, ...]:
    return tuple(
        column.name
        for column in MarketBreadth.__table__.columns
        if column.name not in _EXCLUDED_COPY_COLUMNS
    )


class StagingBreadthPersistence:
    """Breadth persistence adapter that writes only to the shadow table."""

    def __init__(self, rebuild: "BreadthRebuildService") -> None:
        self._rebuild = rebuild

    def upsert_many(
        self,
        results: Iterable[BreadthDailyResult],
        *,
        duration_seconds_by_date: Mapping[date, float] | None = None,
    ) -> tuple[()]:
        self._rebuild.stage_results(
            results,
            duration_seconds_by_date=duration_seconds_by_date,
        )
        return ()


class BreadthRebuildService:
    def __init__(
        self,
        db: Session,
        *,
        price_cache=None,
        universe_service: PointInTimeUniverseService | None = None,
        calendar_service=None,
        required_markets: Iterable[str] | None = None,
    ) -> None:
        if required_markets is None:
            from app.domain.markets.catalog import get_market_catalog

            required_markets = get_market_catalog().market_codes_with_capability(
                "breadth"
            )
        self.db = db
        self._price_cache = price_cache
        self._universe_service = universe_service or PointInTimeUniverseService()
        self._calendar_service = calendar_service
        self._required_markets = frozenset(
            str(market).upper() for market in required_markets
        )

    @property
    def dialect_name(self) -> str:
        return self.db.get_bind().dialect.name

    def _has_table(self, table_name: str) -> bool:
        """Inspect through the session connection to avoid cross-connection locks."""
        return inspect(self.db.connection()).has_table(table_name)

    def recreate_staging(self) -> None:
        self.db.execute(text(f"DROP TABLE IF EXISTS {STAGING_TABLE}"))
        self.db.execute(text(f"DROP TABLE IF EXISTS {MANIFEST_TABLE}"))
        if self.dialect_name == "postgresql":
            self.db.execute(
                text(
                    f"CREATE TABLE {STAGING_TABLE} "
                    f"(LIKE {TARGET_TABLE} INCLUDING DEFAULTS "
                    "INCLUDING GENERATED INCLUDING IDENTITY)"
                )
            )
        else:
            self.db.execute(
                text(
                    f"CREATE TABLE {STAGING_TABLE} AS "
                    f"SELECT * FROM {TARGET_TABLE} WHERE 1 = 0"
                )
            )
        self.db.execute(
            text(
                f"CREATE UNIQUE INDEX uix_breadth_rebuild_date_market "
                f"ON {STAGING_TABLE} (date, market)"
            )
        )
        self.db.execute(
            text(
                f"CREATE TABLE {MANIFEST_TABLE} ("
                "market VARCHAR(8) PRIMARY KEY, "
                "expected_dates_json TEXT NOT NULL, "
                "full_market_set BOOLEAN NOT NULL)"
            )
        )
        self.db.commit()

    def record_build_manifest(
        self,
        expected_dates_by_market: Mapping[str, Iterable[date]],
        *,
        full_market_set: bool,
    ) -> None:
        if not self._has_table(MANIFEST_TABLE):
            raise RuntimeError("Breadth rebuild manifest table does not exist")
        self.db.execute(text(f"DELETE FROM {MANIFEST_TABLE}"))
        for raw_market, raw_dates in sorted(expected_dates_by_market.items()):
            market = raw_market.upper()
            dates = tuple(sorted(set(raw_dates)))
            self.db.execute(
                text(
                    f"INSERT INTO {MANIFEST_TABLE} "
                    "(market, expected_dates_json, full_market_set) "
                    "VALUES (:market, :expected_dates_json, :full_market_set)"
                ),
                {
                    "market": market,
                    "expected_dates_json": json.dumps(
                        [value.isoformat() for value in dates]
                    ),
                    "full_market_set": bool(full_market_set),
                },
            )
        self.db.commit()

    def stage_results(
        self,
        results: Iterable[BreadthDailyResult],
        *,
        duration_seconds_by_date: Mapping[date, float] | None = None,
    ) -> int:
        if not self._has_table(STAGING_TABLE):
            raise RuntimeError("Breadth rebuild staging table does not exist")
        columns = _copy_columns()
        placeholders = ", ".join(f":{column}" for column in columns)
        column_sql = ", ".join(columns)
        inserted = 0
        for result in results:
            if result.calculation_revision != CURRENT_BREADTH_CALCULATION_REVISION:
                raise ValueError("Staging accepts only canonical revision-2 results")
            values = result.to_record_mapping()
            values["calculation_duration_seconds"] = (
                duration_seconds_by_date.get(result.calculation_date)
                if duration_seconds_by_date is not None
                else None
            )
            self.db.execute(
                text(
                    f"DELETE FROM {STAGING_TABLE} "
                    "WHERE market = :market AND date = :date"
                ),
                {"market": result.market, "date": result.calculation_date},
            )
            self.db.execute(
                text(
                    f"INSERT INTO {STAGING_TABLE} ({column_sql}) "
                    f"VALUES ({placeholders})"
                ),
                {column: values.get(column) for column in columns},
            )
            inserted += 1
        self.db.commit()
        return inserted

    def build(
        self,
        *,
        markets: tuple[str, ...],
        start_date: date,
        end_date: date | None = None,
    ) -> dict[str, Any]:
        from app.wiring.bootstrap import get_market_calendar_service, get_price_cache

        calendar = self._calendar_service or get_market_calendar_service()
        price_cache = self._price_cache or get_price_cache()
        normalized_markets = tuple(
            dict.fromkeys(raw_market.upper() for raw_market in markets)
        )
        dates_by_market: dict[str, tuple[date, ...]] = {}
        for market in normalized_markets:
            market_end = end_date or calendar.market_now(market).date()
            dates_by_market[market] = tuple(
                value
                for value in pd.date_range(start=start_date, end=market_end).date
                if calendar.is_trading_day(market, value)
            )
        self.recreate_staging()
        self.record_build_manifest(
            dates_by_market,
            full_market_set=set(normalized_markets) == self._required_markets,
        )
        reports: dict[str, Any] = {}
        for market in normalized_markets:
            dates = dates_by_market[market]
            universes: dict[date, BreadthEligibleUniverse] = {}
            for calculation_date in dates:
                snapshot = self._universe_service.resolve(
                    self.db,
                    market=market,
                    as_of_date=calculation_date,
                )
                universes[calculation_date] = BreadthEligibleUniverse(
                    calculation_date=calculation_date,
                    symbols=snapshot.symbols,
                    eligibility_signature=snapshot.universe_hash,
                )
            calculator = BreadthCalculatorService(
                self.db,
                price_cache,
                market=market,
            )
            calculator.persistence = StagingBreadthPersistence(self)
            plan = BreadthBackfillPlan(
                dates=dates,
                universes=MappingProxyType(universes),
            )
            reports[market] = BreadthBackfillExecutor(calculator).execute(
                plan,
                policy=DerivedDataExecutionPolicy(
                    mode=DerivedDataExecutionMode.STRICT_CACHE_ONLY,
                    target_kind=DerivedDataTargetKind.HISTORICAL,
                ),
                exclude_unsupported_price_symbols=True,
                required_as_of_date=dates[-1],
                require_complete_cache_coverage=True,
            ).to_legacy_dict()
        return {
            "markets": reports,
            "processed": sum(value["processed"] for value in reports.values()),
        }

    def validate(
        self,
        *,
        expected_dates_by_market: Mapping[str, Iterable[date]] | None = None,
    ) -> dict[str, Any]:
        if not self._has_table(STAGING_TABLE):
            return {
                "valid": False,
                "errors": ["staging_table_missing"],
                "row_count": 0,
            }
        if not self._has_table(MANIFEST_TABLE):
            return {
                "valid": False,
                "errors": ["staging_manifest_missing"],
                "row_count": 0,
            }
        rows = self.db.execute(
            text(f"SELECT * FROM {STAGING_TABLE} ORDER BY market, date")
        ).mappings().all()
        errors: list[str] = []
        manifest_rows = self.db.execute(
            text(f"SELECT * FROM {MANIFEST_TABLE} ORDER BY market")
        ).mappings().all()
        manifest_dates_by_market: dict[str, set[date]] = {}
        full_market_flags: set[bool] = set()
        for manifest_row in manifest_rows:
            market = str(manifest_row["market"]).upper()
            try:
                raw_dates = json.loads(manifest_row["expected_dates_json"])
                manifest_dates_by_market[market] = {
                    date.fromisoformat(value) for value in raw_dates
                }
            except (TypeError, ValueError, json.JSONDecodeError):
                errors.append(f"invalid_manifest_dates:{market}")
            full_market_flags.add(bool(manifest_row["full_market_set"]))
        if not manifest_rows:
            errors.append("staging_manifest_empty")
        if full_market_flags != {True}:
            errors.append("partial_market_set")
        manifest_markets = set(manifest_dates_by_market)
        errors.extend(
            f"missing_manifest_market:{market}"
            for market in sorted(self._required_markets - manifest_markets)
        )
        errors.extend(
            f"unexpected_manifest_market:{market}"
            for market in sorted(manifest_markets - self._required_markets)
        )
        errors.extend(
            f"empty_manifest_market:{market}"
            for market in sorted(self._required_markets & manifest_markets)
            if not manifest_dates_by_market[market]
        )
        seen: set[tuple[str, date]] = set()
        dates_by_market: dict[str, set[date]] = {}
        for row in rows:
            row_date = (
                date.fromisoformat(row["date"])
                if isinstance(row["date"], str)
                else row["date"]
            )
            key = (str(row["market"]), row_date)
            if key in seen:
                errors.append(f"duplicate:{key[0]}:{key[1]}")
            seen.add(key)
            dates_by_market.setdefault(key[0], set()).add(row_date)
            if row["calculation_revision"] != CURRENT_BREADTH_CALCULATION_REVISION:
                errors.append(f"wrong_revision:{key[0]}:{key[1]}")
            if (
                not row["eligibility_signature"]
                or not row["stockbee_eligibility_signature"]
            ):
                errors.append(f"missing_signature:{key[0]}:{key[1]}")
            broad = row["broad_universe_count"]
            if broad is None or broad < 0:
                errors.append(f"invalid_broad_universe:{key[0]}:{key[1]}")
            eligible_names = (
                "advance_decline_eligible_count",
                "stockbee_daily_eligible_count",
                "stockbee_month_eligible_count",
                "stockbee_34day_eligible_count",
                "stockbee_quarter_eligible_count",
                "t2108_eligible_count",
                "high_low_52week_eligible_count",
                "atr_extension_eligible_count",
            )
            if broad is not None:
                for eligible_name in eligible_names:
                    eligible = row[eligible_name]
                    if eligible is None or not 0 <= eligible <= broad:
                        errors.append(
                            f"invalid_eligibility:{eligible_name}:{key[0]}:{key[1]}"
                        )
            ad_eligible = row["advance_decline_eligible_count"]
            ad_total = sum(
                int(row[name] or 0)
                for name in ("advancing_count", "declining_count", "unchanged_count")
            )
            if ad_eligible is None or ad_total != ad_eligible:
                errors.append(f"advance_decline_mismatch:{key[0]}:{key[1]}")
            pairs = (
                ("stocks_up_4pct", "stockbee_daily_eligible_count"),
                ("stocks_down_4pct", "stockbee_daily_eligible_count"),
                ("stocks_up_25pct_month", "stockbee_month_eligible_count"),
                ("stocks_down_25pct_month", "stockbee_month_eligible_count"),
                ("stocks_up_13pct_34days", "stockbee_34day_eligible_count"),
                ("stocks_down_13pct_34days", "stockbee_34day_eligible_count"),
                ("stocks_up_25pct_quarter", "stockbee_quarter_eligible_count"),
                ("stocks_down_25pct_quarter", "stockbee_quarter_eligible_count"),
                ("t2108_count", "t2108_eligible_count"),
            )
            for count_name, eligible_name in pairs:
                count = row[count_name]
                eligible = row[eligible_name]
                if count is None or eligible is None or not 0 <= count <= eligible:
                    errors.append(
                        f"count_exceeds_eligibility:{count_name}:{key[0]}:{key[1]}"
                    )
            for ratio_name in ("ratio_5day", "ratio_10day", "t2108_pct"):
                value = row[ratio_name]
                if value is not None and not math.isfinite(float(value)):
                    errors.append(f"non_finite:{ratio_name}:{key[0]}:{key[1]}")
            t2108_pct = row["t2108_pct"]
            if t2108_pct is not None and not 0 <= float(t2108_pct) <= 100:
                errors.append(f"invalid_t2108_pct:{key[0]}:{key[1]}")
            t2108_eligible = row["t2108_eligible_count"]
            expected_t2108_pct = (
                round(float(row["t2108_count"]) / t2108_eligible * 100.0, 2)
                if t2108_eligible
                else None
            )
            if t2108_pct != expected_t2108_pct:
                errors.append(f"t2108_reconciliation:{key[0]}:{key[1]}")
            mutually_exclusive_pairs = (
                (
                    "stocks_up_4pct",
                    "stocks_down_4pct",
                    "stockbee_daily_eligible_count",
                ),
                (
                    "stocks_up_25pct_month",
                    "stocks_down_25pct_month",
                    "stockbee_month_eligible_count",
                ),
            )
            for up_name, down_name, eligible_name in mutually_exclusive_pairs:
                if int(row[up_name] or 0) + int(row[down_name] or 0) > int(
                    row[eligible_name] or 0
                ):
                    errors.append(
                        f"pair_exceeds_eligibility:{up_name}:{key[0]}:{key[1]}"
                    )
            for count_name, eligible_name in (
                ("new_high_52week_count", "high_low_52week_eligible_count"),
                ("new_low_52week_count", "high_low_52week_eligible_count"),
                ("atr_10x_extension_count", "atr_extension_eligible_count"),
            ):
                if int(row[count_name] or 0) > int(row[eligible_name] or 0):
                    errors.append(
                        f"context_exceeds_eligibility:{count_name}:{key[0]}:{key[1]}"
                    )

        effective_expected = dict(manifest_dates_by_market)
        for market, expected_dates in (expected_dates_by_market or {}).items():
            normalized_market = market.upper()
            supplied = set(expected_dates)
            if effective_expected.get(normalized_market) != supplied:
                errors.append(f"manifest_mismatch:{normalized_market}")
            effective_expected[normalized_market] = supplied
        for market, expected_dates in effective_expected.items():
            actual_dates = dates_by_market.get(market, set())
            missing = set(expected_dates) - actual_dates
            errors.extend(
                f"missing_date:{market}:{value.isoformat()}"
                for value in sorted(missing)
            )
            unexpected = actual_dates - set(expected_dates)
            errors.extend(
                f"unexpected_date:{market}:{value.isoformat()}"
                for value in sorted(unexpected)
            )
        for market in sorted(set(dates_by_market) - set(effective_expected)):
            errors.append(f"unexpected_market:{market}")

        return {
            "valid": not errors and bool(rows),
            "errors": errors,
            "row_count": len(rows),
            "markets": {
                market: {
                    "row_count": len(values),
                    "start_date": min(values).isoformat() if values else None,
                    "end_date": max(values).isoformat() if values else None,
                }
                for market, values in dates_by_market.items()
            },
            "calculation_revision": CURRENT_BREADTH_CALCULATION_REVISION,
            "formula_contract": {
                "signals": "adjusted_ohlc",
                "liquidity": "raw_close_usd_x_volume_adtv20",
                "fx": "exact_or_prior_7_calendar_days_never_future",
                "ratios": "today_inclusive",
            },
        }

    def activate(self) -> dict[str, Any]:
        columns = _copy_columns()
        column_sql = ", ".join(columns)
        self.db.rollback()
        with self.db.begin():
            if self.dialect_name == "postgresql":
                self.db.execute(
                    text(f"LOCK TABLE {TARGET_TABLE} IN ACCESS EXCLUSIVE MODE")
                )
                self.db.execute(
                    text(
                        f"LOCK TABLE {STAGING_TABLE}, {MANIFEST_TABLE} "
                        "IN ACCESS EXCLUSIVE MODE"
                    )
                )
            report = self.validate()
            if not report["valid"]:
                raise RuntimeError(
                    "Cannot activate invalid breadth rebuild staging data"
                )
            self.db.execute(text(f"DELETE FROM {TARGET_TABLE}"))
            self.db.execute(
                text(
                    f"INSERT INTO {TARGET_TABLE} ({column_sql}) "
                    f"SELECT {column_sql} FROM {STAGING_TABLE}"
                )
            )
            inserted = int(
                self.db.execute(text(f"SELECT COUNT(*) FROM {TARGET_TABLE}")).scalar()
                or 0
            )
            wrong_revision = int(
                self.db.execute(
                    text(
                        f"SELECT COUNT(*) FROM {TARGET_TABLE} "
                        "WHERE calculation_revision != :revision "
                        "OR calculation_revision IS NULL"
                    ),
                    {"revision": CURRENT_BREADTH_CALCULATION_REVISION},
                ).scalar()
                or 0
            )
            if inserted != report["row_count"] or wrong_revision:
                raise RuntimeError("Breadth activation verification failed")
        return {"activated": inserted, "calculation_revision": 2}

    def cleanup(self) -> None:
        self.db.execute(text(f"DROP TABLE IF EXISTS {STAGING_TABLE}"))
        self.db.execute(text(f"DROP TABLE IF EXISTS {MANIFEST_TABLE}"))
        self.db.commit()
