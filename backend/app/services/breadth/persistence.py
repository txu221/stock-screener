"""Single persistence mapping for canonical breadth results."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date

from sqlalchemy.orm import Session

from app.models.market_breadth import MarketBreadth

from .types import CURRENT_BREADTH_CALCULATION_REVISION, BreadthDailyResult


class BreadthPersistence:
    def __init__(self, db: Session) -> None:
        self._db = db

    @staticmethod
    def _assign(
        record: MarketBreadth,
        result: BreadthDailyResult,
        *,
        duration_seconds: float | None,
    ) -> None:
        values = result.to_record_mapping()
        if result.calculation_revision != CURRENT_BREADTH_CALCULATION_REVISION:
            raise ValueError("Only current revision breadth results may be persisted")
        for column in MarketBreadth.__table__.columns:
            if column.name in {"id", "created_at", "calculation_duration_seconds"}:
                continue
            if column.name in values:
                setattr(record, column.name, values[column.name])
        record.total_stocks_scanned = result.broad_universe_count
        record.calculation_revision = CURRENT_BREADTH_CALCULATION_REVISION
        record.calculation_duration_seconds = duration_seconds

    def _upsert_without_commit(
        self,
        result: BreadthDailyResult,
        *,
        duration_seconds: float | None,
    ) -> MarketBreadth:
        record = (
            self._db.query(MarketBreadth)
            .filter(
                MarketBreadth.market == result.market,
                MarketBreadth.date == result.calculation_date,
            )
            .first()
        )
        if record is None:
            record = MarketBreadth(
                market=result.market,
                date=result.calculation_date,
                stocks_up_4pct=0,
                stocks_down_4pct=0,
                stocks_up_25pct_quarter=0,
                stocks_down_25pct_quarter=0,
                stocks_up_25pct_month=0,
                stocks_down_25pct_month=0,
                stocks_up_50pct_month=0,
                stocks_down_50pct_month=0,
                stocks_up_13pct_34days=0,
                stocks_down_13pct_34days=0,
                total_stocks_scanned=0,
            )
            self._db.add(record)
        self._assign(record, result, duration_seconds=duration_seconds)
        return record

    def upsert_daily(
        self,
        result: BreadthDailyResult,
        *,
        duration_seconds: float | None,
    ) -> MarketBreadth:
        record = self._upsert_without_commit(
            result,
            duration_seconds=duration_seconds,
        )
        self._db.commit()
        return record

    def upsert_many(
        self,
        results: Iterable[BreadthDailyResult],
        *,
        duration_seconds_by_date: Mapping[date, float] | None = None,
    ) -> tuple[MarketBreadth, ...]:
        records = tuple(
            self._upsert_without_commit(
                result,
                duration_seconds=(
                    duration_seconds_by_date.get(result.calculation_date)
                    if duration_seconds_by_date is not None
                    else None
                ),
            )
            for result in results
        )
        if records:
            self._db.commit()
        return records
