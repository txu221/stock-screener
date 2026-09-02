"""Opt-in live Yahoo validation for the fixed Phase 2 sector universe.

The command prints only summarized/derived evidence. It does not persist or
write provider payloads. Set the application's normal environment first, then
run from ``backend`` with ``RUN_MARKET_INTELLIGENCE_LIVE=1``.
"""

from __future__ import annotations

import json
import math
import os
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.domain.market_intelligence.constants import (  # noqa: E402
    BENCHMARK_SYMBOL,
    MARKET_INTELLIGENCE_UNIVERSE,
    METRIC_VERSION,
    SECTOR_SYMBOLS,
)
from app.domain.market_intelligence.metrics import (  # noqa: E402
    calculate_symbol_metrics,
    with_relative_returns,
)
from app.domain.market_intelligence.snapshot import (  # noqa: E402
    build_candidate_snapshot,
)
from app.domain.market_intelligence.validation import (  # noqa: E402
    validate_provider_rows,
)
from app.infra.providers.market_intelligence_yahoo import (  # noqa: E402
    YahooMarketIntelligenceProvider,
)
from app.services.bulk_data_fetcher import BulkDataFetcher  # noqa: E402
from app.services.market_calendar_service import MarketCalendarService  # noqa: E402


MANUAL_SYMBOLS = ("SPY", "XLK", "XLE", "XLU")


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _close(value: float | None, expected: float | None) -> bool:
    if value is None or expected is None:
        return value is expected
    return math.isclose(value, expected, rel_tol=1e-12, abs_tol=1e-12)


def _manual_metrics(symbol_bars, spy_bars, sessions: tuple[date, ...]) -> dict[str, Any]:
    bars = {bar.trading_date: bar for bar in symbol_bars}
    spy = {bar.trading_date: bar for bar in spy_bars}
    current = bars.get(sessions[-1]) if sessions else None
    spy_current = spy.get(sessions[-1]) if sessions else None

    returns: dict[int, float | None] = {}
    spy_returns: dict[int, float | None] = {}
    for offset in (1, 5, 20, 60):
        anchor = sessions[-1 - offset] if len(sessions) > offset else None
        symbol_anchor = bars.get(anchor) if anchor is not None else None
        spy_anchor = spy.get(anchor) if anchor is not None else None
        returns[offset] = (
            current.adjusted_close / symbol_anchor.adjusted_close - 1.0
            if current is not None
            and symbol_anchor is not None
            and symbol_anchor.adjusted_close > 0
            else None
        )
        spy_returns[offset] = (
            spy_current.adjusted_close / spy_anchor.adjusted_close - 1.0
            if spy_current is not None
            and spy_anchor is not None
            and spy_anchor.adjusted_close > 0
            else None
        )

    rvol_window = (
        tuple(bars.get(session) for session in sessions[-21:])
        if len(sessions) >= 21
        else ()
    )
    if len(rvol_window) != 21 or any(bar is None for bar in rvol_window):
        rvol20 = None
    else:
        previous_average = sum(
            bar.provider_volume for bar in rvol_window[:-1] if bar is not None
        ) / 20.0
        rvol20 = (
            rvol_window[-1].provider_volume / previous_average
            if previous_average != 0 and rvol_window[-1] is not None
            else None
        )

    def pressure(bar) -> float | None:
        if bar is None:
            return None
        spread = bar.adjusted_high - bar.adjusted_low
        if spread == 0:
            return 0.0
        return (
            2.0 * bar.adjusted_close - bar.adjusted_high - bar.adjusted_low
        ) / spread

    def cmf(count: int) -> float | None:
        if len(sessions) < count:
            return None
        window = tuple(bars.get(session) for session in sessions[-count:])
        if any(bar is None for bar in window):
            return None
        denominator = sum(
            bar.provider_volume for bar in window if bar is not None
        )
        if denominator == 0:
            return None
        return sum(
            pressure(bar) * bar.provider_volume
            for bar in window
            if bar is not None
        ) / denominator

    def relative(value: float | None, benchmark: float | None) -> float | None:
        if value is None or benchmark is None:
            return None
        result = value - benchmark
        return result if math.isfinite(result) else None

    return {
        "return_1d": returns[1],
        "return_5d": returns[5],
        "return_20d": returns[20],
        "return_60d": returns[60],
        "relative_return_vs_spy_1d": relative(returns[1], spy_returns[1]),
        "relative_return_vs_spy_5d": relative(returns[5], spy_returns[5]),
        "relative_return_vs_spy_20d": relative(returns[20], spy_returns[20]),
        "relative_return_vs_spy_60d": relative(returns[60], spy_returns[60]),
        "rvol20": rvol20,
        "flow_pressure_1d_proxy": pressure(current),
        "cmf_5d_proxy": cmf(5),
        "cmf_20d_proxy": cmf(20),
        "cmf_60d_proxy": cmf(60),
    }


def _source_freshness(canonical_bars, target: date) -> dict[str, Any]:
    latest_by_symbol: dict[str, date] = {}
    for bar in canonical_bars:
        current = latest_by_symbol.get(bar.symbol)
        if current is None or bar.trading_date > current:
            latest_by_symbol[bar.symbol] = bar.trading_date
    complete = sorted(
        symbol
        for symbol in MARKET_INTELLIGENCE_UNIVERSE
        if latest_by_symbol.get(symbol) == target
    )
    missing_target = sorted(set(MARKET_INTELLIGENCE_UNIVERSE) - set(complete))
    return {
        "status": "FRESH" if not missing_target else "STALE",
        "target_session": target.isoformat(),
        "complete_through_target": not missing_target,
        "target_complete_count": len(complete),
        "missing_target_symbols": missing_target,
        "per_symbol_latest": {
            symbol: (
                latest_by_symbol[symbol].isoformat()
                if symbol in latest_by_symbol
                else None
            )
            for symbol in MARKET_INTELLIGENCE_UNIVERSE
        },
    }


def _safe_failure(failure) -> dict[str, Any]:
    """Keep failure topology without emitting provider-controlled messages."""

    safe = {"code": str(failure.code)}
    symbol = getattr(failure, "symbol", None)
    if symbol is not None:
        safe["symbol"] = str(symbol)
    return safe


def _calculate_candidate(
    canonical_bars,
    sessions,
    *,
    as_of,
    previous,
    source_freshness,
):
    bars_by_symbol = defaultdict(list)
    for bar in canonical_bars:
        bars_by_symbol[bar.symbol].append(bar)
    metrics = {
        symbol: calculate_symbol_metrics(tuple(bars_by_symbol[symbol]), sessions)
        for symbol in MARKET_INTELLIGENCE_UNIVERSE
        if bars_by_symbol[symbol]
    }
    spy_metrics = metrics.get(BENCHMARK_SYMBOL)
    if spy_metrics is not None:
        metrics = {
            symbol: (
                value
                if symbol == BENCHMARK_SYMBOL
                else with_relative_returns(value, spy_metrics)
            )
            for symbol, value in metrics.items()
        }
    counts = {
        symbol: len({bar.trading_date for bar in bars})
        for symbol, bars in bars_by_symbol.items()
    }
    candidate = build_candidate_snapshot(
        request_succeeded=True,
        as_of=as_of,
        metrics_by_symbol=metrics,
        history_session_counts=counts,
        received_symbols=tuple(bars_by_symbol),
        rejection_count=0,
        provider_failures=(),
        provider="yahoo",
        source_freshness=source_freshness,
        calculation_timestamp=datetime.now(timezone.utc),
        previous_published=previous,
    )
    return candidate, metrics, bars_by_symbol


def run_live_validation(*, as_of: date | None = None) -> dict[str, Any]:
    if not _enabled(os.environ.get("RUN_MARKET_INTELLIGENCE_LIVE")):
        raise RuntimeError("set RUN_MARKET_INTELLIGENCE_LIVE=1 to call Yahoo")

    started = perf_counter()
    calendar = MarketCalendarService()
    last_completed = calendar.last_completed_trading_day("US")
    target = as_of or last_completed
    if target > last_completed:
        raise RuntimeError("requested session is not yet completed")
    sessions = tuple(
        calendar.trading_days("US", target - timedelta(days=210), target)
    )
    if len(sessions) < 90 or sessions[-1] != target:
        raise RuntimeError("calendar did not provide 90 completed sessions through target")

    provider = YahooMarketIntelligenceProvider(
        BulkDataFetcher(), clock=lambda: datetime.now(timezone.utc)
    )
    fetch_started = perf_counter()
    result = provider.fetch(MARKET_INTELLIGENCE_UNIVERSE, target)
    fetch_duration = perf_counter() - fetch_started

    base: dict[str, Any] = {
        "provider": result.provider,
        "requested": list(MARKET_INTELLIGENCE_UNIVERSE),
        "requested_count": len(MARKET_INTELLIGENCE_UNIVERSE),
        "as_of": target.isoformat(),
        "fetch_duration_seconds": fetch_duration,
        "response_timestamp": result.response_timestamp.isoformat(),
        "request_failure": (
            None if result.request_failure is None else _safe_failure(result.request_failure)
        ),
        "symbol_failures": [_safe_failure(failure) for failure in result.symbol_failures],
    }
    if result.request_failure is not None:
        return {**base, "total_duration_seconds": perf_counter() - started}

    validation_started = perf_counter()
    validation = validate_provider_rows(
        result.rows,
        sessions,
        result.response_timestamp,
    )
    validation_duration = perf_counter() - validation_started
    returned = sorted({row.symbol for row in result.rows})
    missing = sorted(set(MARKET_INTELLIGENCE_UNIVERSE) - set(returned))
    row_counts = Counter(row.symbol for row in result.rows)
    raw_dates = [row.trading_date for row in result.rows if row.trading_date is not None]
    rejection_counts = Counter(item.code.value for item in validation.rejections)
    freshness = _source_freshness(validation.canonical_bars, target)

    calculation_started = perf_counter()
    candidate, metrics, bars_by_symbol = _calculate_candidate(
        validation.canonical_bars,
        sessions,
        as_of=target,
        previous={},
        source_freshness=freshness,
    )
    calculation_duration = perf_counter() - calculation_started

    spy_bars = tuple(bars_by_symbol[BENCHMARK_SYMBOL])
    manual_checks: dict[str, Any] = {}
    for symbol in MANUAL_SYMBOLS:
        raw = next(
            row
            for row in result.rows
            if row.symbol == symbol and row.trading_date == target
        )
        canonical = next(
            bar
            for bar in validation.canonical_bars
            if bar.symbol == symbol and bar.trading_date == target
        )
        independent = _manual_metrics(
            tuple(bars_by_symbol[symbol]), spy_bars, sessions
        )
        if symbol == BENCHMARK_SYMBOL:
            for horizon in (1, 5, 20, 60):
                independent[f"relative_return_vs_spy_{horizon}d"] = None
        production = asdict(metrics[symbol])
        raw_open = float(raw.open)
        raw_high = float(raw.high)
        raw_low = float(raw.low)
        raw_close = float(raw.close)
        adjusted_close = float(raw.adjusted_close)
        raw_volume = float(raw.volume)
        independent_factor = adjusted_close / raw_close
        independent_canonical = {
            "adjustment_factor": independent_factor,
            "adjusted_open": raw_open * independent_factor,
            "adjusted_high": raw_high * independent_factor,
            "adjusted_low": raw_low * independent_factor,
            "adjusted_close": adjusted_close,
            "volume": raw_volume,
        }
        canonical_output = {
            "adjustment_factor": canonical.adjustment_factor,
            "adjusted_open": canonical.adjusted_open,
            "adjusted_high": canonical.adjusted_high,
            "adjusted_low": canonical.adjusted_low,
            "adjusted_close": canonical.adjusted_close,
            "volume": canonical.provider_volume,
        }
        canonical_fields_match = all(
            _close(canonical_output[name], value)
            for name, value in independent_canonical.items()
        )
        manual_checks[symbol] = {
            "raw": {
                "open": raw_open,
                "high": raw_high,
                "low": raw_low,
                "close": raw_close,
                "adj_close": adjusted_close,
                "volume": raw_volume,
            },
            "independent_canonical": independent_canonical,
            "canonical": canonical_output,
            "canonical_fields_match": canonical_fields_match,
            "independent_metrics": independent,
            "production_metrics": production,
            "all_metrics_match": canonical_fields_match and all(
                _close(production[name], value)
                for name, value in independent.items()
            ),
        }

    replay: list[dict[str, Any]] = []
    previous = {}
    for replay_date in sessions[-5:]:
        replay_sessions = tuple(session for session in sessions if session <= replay_date)
        replay_rows = tuple(
            row
            for row in result.rows
            if row.trading_date is not None and row.trading_date <= replay_date
        )
        replay_validation = validate_provider_rows(
            replay_rows, replay_sessions, result.response_timestamp
        )
        replay_freshness = _source_freshness(
            replay_validation.canonical_bars, replay_date
        )
        replay_candidate, _, _ = _calculate_candidate(
            replay_validation.canonical_bars,
            replay_sessions,
            as_of=replay_date,
            previous=previous,
            source_freshness=replay_freshness,
        )
        max_input = max(
            bar.trading_date for bar in replay_validation.canonical_bars
        )
        rank_records = [
            rank
            for snapshot in replay_candidate.snapshots
            if snapshot.symbol in SECTOR_SYMBOLS
            for rank in snapshot.ranks.values()
        ]
        replay.append(
            {
                "as_of": replay_date.isoformat(),
                "max_input_date": max_input.isoformat(),
                "status": replay_candidate.ingestion_status.value,
                "snapshot_count": len(replay_candidate.snapshots),
                "previous_rank_count": sum(
                    rank.previous_rank is not None for rank in rank_records
                ),
                "metric_version": METRIC_VERSION,
            }
        )
        if replay_candidate.publishable:
            previous = {
                snapshot.symbol: snapshot
                for snapshot in replay_candidate.snapshots
            }

    return {
        **base,
        "returned": returned,
        "returned_count": len(returned),
        "missing": missing,
        "earliest_date": min(raw_dates).isoformat() if raw_dates else None,
        "latest_completed_session": max(raw_dates).isoformat() if raw_dates else None,
        "bars_per_symbol": dict(sorted(row_counts.items())),
        "canonical_bars": len(validation.canonical_bars),
        "rejected_rows": len(validation.rejections),
        "rejection_codes": dict(sorted(rejection_counts.items())),
        "freshness": freshness,
        "validation_duration_seconds": validation_duration,
        "calculation_duration_seconds": calculation_duration,
        "candidate_status": candidate.ingestion_status.value,
        "snapshot_count": len(candidate.snapshots),
        "metric_version": METRIC_VERSION,
        "manual_checks": manual_checks,
        "historical_replay_using_real_provider_data": replay,
        "total_duration_seconds": perf_counter() - started,
    }


def main() -> int:
    requested_as_of = os.environ.get("PHASE2_COMPLETED_SESSION")
    as_of = date.fromisoformat(requested_as_of) if requested_as_of else None
    summary = run_live_validation(as_of=as_of)
    print(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False))
    return 0 if summary.get("request_failure") is None else 2


if __name__ == "__main__":
    raise SystemExit(main())
