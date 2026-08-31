"""PostgreSQL Market Intelligence read baseline and opt-in SLO evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
from time import perf_counter_ns
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

import app.api.v1.market_intelligence as market_intelligence_api
from app.api.v1.market_intelligence import router
from app.database import get_db
from app.domain.market_intelligence.constants import (
    LATEST_POINTER_KEY,
    MARKET_INTELLIGENCE_UNIVERSE,
    METRIC_VERSION,
    NORMALIZATION_VERSION,
    PRICE_BASIS,
)
from app.domain.market_intelligence.mvp import ETF_UNIVERSE
from app.infra.db.models.feature_store import (
    FeatureRun,
    FeatureRunPointer,
    StockFeatureDaily,
)
from app.infra.db.models.market_intelligence import (
    MarketIntelligenceRunAudit,
    MarketIntelligenceSectorSnapshot,
)
from app.models.stock import StockPrice
from app.models.stock_universe import StockUniverse
from tests.integration.market_intelligence.scenario import weekday_sessions
from tests.integration.market_intelligence.test_postgres_publication import (
    _create_tables,
)


API_FAMILIES = (
    "overview",
    "movers",
    "etfs",
    "sectors_latest",
    "sectors_history",
    "sectors_health",
)

ENDPOINTS = {
    "overview": ("/api/v1/market-intelligence/overview", None),
    "movers": ("/api/v1/market-intelligence/movers", {"limit": 20}),
    "etfs": ("/api/v1/market-intelligence/etfs", {"category": "all"}),
    "sectors_latest": ("/api/v1/market-intelligence/sectors/latest", None),
    "sectors_history": (
        "/api/v1/market-intelligence/sectors/history",
        {"limit": 60},
    ),
    "sectors_health": ("/api/v1/market-intelligence/sectors/health", None),
}

DATASET_STOCK_COUNT = 500
DATASET_PRICE_SESSION_COUNT = 90
DATASET_SECTOR_SESSION_COUNT = 60
DEFAULT_SAMPLE_COUNT = 10


def _linear_percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("at least one timing sample is required")
    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def _latency_summary(samples_ms: list[float]) -> dict[str, int | float]:
    return {
        "sample_count": len(samples_ms),
        "p50_ms": round(_linear_percentile(samples_ms, 0.50), 3),
        "p95_ms": round(_linear_percentile(samples_ms, 0.95), 3),
        "worst_ms": round(max(samples_ms), 3),
    }


def _build_report(
    samples: dict[str, list[float]],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "api_families": {
            family: _latency_summary(samples[family]) for family in API_FAMILIES
        },
    }


def _configured_slo_ms() -> float | None:
    enabled = os.environ.get("MARKET_INTELLIGENCE_ENFORCE_SLO", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return None
    raw_threshold = os.environ.get("MARKET_INTELLIGENCE_SLO_P95_MS", "").strip()
    if not raw_threshold:
        raise ValueError(
            "SLO enforcement requires a measured threshold in "
            "MARKET_INTELLIGENCE_SLO_P95_MS"
        )
    threshold = float(raw_threshold)
    if not math.isfinite(threshold) or threshold <= 0:
        raise ValueError("measured threshold must be a finite positive number")
    return threshold


def _slo_violations(
    report: dict[str, object], *, threshold_ms: float
) -> list[str]:
    families = report["api_families"]
    return [
        f"{family} p95={families[family]['p95_ms']:.3f}ms > {threshold_ms:.3f}ms"
        for family in API_FAMILIES
        if families[family]["p95_ms"] > threshold_ms
    ]


def _sample_count() -> int:
    value = int(
        os.environ.get(
            "MARKET_INTELLIGENCE_SLO_SAMPLE_COUNT",
            DEFAULT_SAMPLE_COUNT,
        )
    )
    if value < 5:
        raise ValueError("MARKET_INTELLIGENCE_SLO_SAMPLE_COUNT must be at least 5")
    return value


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)


@dataclass(frozen=True)
class _QueryObservation:
    api_family: str
    elapsed_ms: float
    statement: str
    parameters: Any


class _QueryRecorder:
    """Capture individual SELECT timings while one API family is active."""

    def __init__(self, engine: Engine):
        self.engine = engine
        self.api_family: str | None = None
        self.observations: list[_QueryObservation] = []

    def start(self) -> None:
        event.listen(self.engine, "before_cursor_execute", self._before)
        event.listen(self.engine, "after_cursor_execute", self._after)

    def stop(self) -> None:
        event.remove(self.engine, "before_cursor_execute", self._before)
        event.remove(self.engine, "after_cursor_execute", self._after)

    def clear(self) -> None:
        self.observations.clear()

    def _before(
        self,
        _connection,
        _cursor,
        _statement,
        _parameters,
        context,
        _executemany,
    ) -> None:
        context._market_intelligence_slo_started_ns = perf_counter_ns()

    def _after(
        self,
        _connection,
        _cursor,
        statement,
        parameters,
        context,
        _executemany,
    ) -> None:
        if self.api_family is None:
            return
        normalized = statement.lstrip().upper()
        if not normalized.startswith(("SELECT", "WITH")):
            return
        elapsed_ms = (
            perf_counter_ns() - context._market_intelligence_slo_started_ns
        ) / 1_000_000
        captured_parameters = (
            dict(parameters) if isinstance(parameters, dict) else tuple(parameters)
        )
        self.observations.append(
            _QueryObservation(
                api_family=self.api_family,
                elapsed_ms=elapsed_ms,
                statement=statement,
                parameters=captured_parameters,
            )
        )


def _published_at(as_of: date, sequence: int) -> datetime:
    return datetime(
        as_of.year,
        as_of.month,
        as_of.day,
        22,
        sequence % 60,
        tzinfo=timezone.utc,
    )


def _create_required_tables(engine: Engine) -> None:
    _create_tables(engine)
    StockPrice.metadata.create_all(
        engine,
        tables=[
            StockFeatureDaily.__table__,
            StockUniverse.__table__,
            StockPrice.__table__,
        ],
    )


def _seed_representative_dataset(engine: Engine) -> dict[str, int]:
    """Seed a fixed read-heavy dataset without network or Redis dependencies."""

    _create_required_tables(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    sector_sessions = weekday_sessions(date(2026, 8, 26), DATASET_SECTOR_SESSION_COUNT)
    price_sessions = weekday_sessions(date(2026, 8, 26), DATASET_PRICE_SESSION_COUNT)
    published_runs: list[FeatureRun] = []

    with factory() as session:
        for sequence, as_of in enumerate(sector_sessions):
            published = _published_at(as_of, sequence)
            run = FeatureRun(
                as_of_date=as_of,
                run_type="daily_snapshot",
                status="published",
                created_at=published - timedelta(minutes=10),
                updated_at=published,
                completed_at=published - timedelta(minutes=1),
                published_at=published,
                code_version="slo-harness-v1",
                universe_hash="slo-representative-universe",
                input_hash=f"{sequence:064x}",
                config_json={"pipeline": "market_intelligence_slo_harness"},
                stats_json={"expected_symbols": 12, "usable_symbols": 12},
                warnings_json=[],
            )
            session.add(run)
            published_runs.append(run)
        session.flush()

        audit_rows = []
        snapshot_rows = []
        for sequence, (as_of, run) in enumerate(zip(sector_sessions, published_runs)):
            timestamp = _published_at(as_of, sequence)
            audit_rows.append(
                {
                    "run_id": run.id,
                    "idempotency_key": f"slo-{sequence:060d}",
                    "input_hash": f"{sequence:064x}",
                    "ingestion_status": "SUCCEEDED",
                    "provider": "deterministic_slo_seed",
                    "provider_status": "AVAILABLE",
                    "request_failure_json": None,
                    "metric_version": METRIC_VERSION,
                    "normalization_version": NORMALIZATION_VERSION,
                    "price_basis": PRICE_BASIS,
                    "target_session": as_of,
                    "counters_json": {"expected_symbols": 12, "usable_symbols": 12},
                    "missing_symbols_json": [],
                    "provider_failures_json": [],
                    "provider_response_at": timestamp,
                    "source_freshness_json": {"status": "FRESH", "as_of": as_of.isoformat()},
                    "calculation_timestamp": timestamp,
                    "ingestion_timestamp": timestamp,
                    "pipeline_version": "slo-harness-v1",
                    "failure_category": None,
                    "stage_timings_json": {"seed": 0.0},
                    "publication_status": "PUBLISHED",
                    "retry_status": "NOT_REQUIRED",
                    "reuse_status": "NOT_REUSED",
                    "created_at": timestamp,
                }
            )
            for symbol_index, symbol in enumerate(MARKET_INTELLIGENCE_UNIVERSE):
                relative_return = (symbol_index - 5) * 0.001 + sequence * 0.00001
                rank = symbol_index + 1
                snapshot_rows.append(
                    {
                        "run_id": run.id,
                        "symbol": symbol,
                        "trading_date": as_of,
                        "asset_type": "benchmark_etf" if symbol == "SPY" else "sector_etf",
                        "sector_name": None if symbol == "SPY" else f"Sector {symbol}",
                        "return_1d": relative_return + 0.001,
                        "return_5d": relative_return + 0.005,
                        "return_20d": relative_return + 0.02,
                        "return_60d": relative_return + 0.06,
                        "relative_return_vs_spy_1d": relative_return,
                        "relative_return_vs_spy_5d": relative_return,
                        "relative_return_vs_spy_20d": relative_return,
                        "relative_return_vs_spy_60d": relative_return,
                        "rvol20": 1.0 + symbol_index * 0.05,
                        "flow_pressure_1d_proxy": relative_return * 10,
                        "cmf_5d_proxy": relative_return,
                        "cmf_20d_proxy": relative_return,
                        "cmf_60d_proxy": relative_return,
                        "current_ranks_json": {"relative_return_vs_spy_20d": rank},
                        "previous_ranks_json": {"relative_return_vs_spy_20d": rank},
                        "rank_changes_json": {"relative_return_vs_spy_20d": 0},
                        "rank_directions_json": {"relative_return_vs_spy_20d": "UNCHANGED"},
                        "provider": "deterministic_slo_seed",
                        "source_freshness_json": {"status": "FRESH", "as_of": as_of.isoformat()},
                        "price_basis": PRICE_BASIS,
                        "metric_version": METRIC_VERSION,
                        "calculation_timestamp": timestamp,
                        "data_quality_status": "COMPLETE",
                    }
                )
        session.bulk_insert_mappings(MarketIntelligenceRunAudit, audit_rows)
        session.bulk_insert_mappings(MarketIntelligenceSectorSnapshot, snapshot_rows)

        latest_run = published_runs[-1]
        session.add_all(
            [
                FeatureRunPointer(key=LATEST_POINTER_KEY, run_id=latest_run.id),
                FeatureRunPointer(
                    key="latest_published_market:US",
                    run_id=latest_run.id,
                ),
            ]
        )

        stock_symbols = [f"MI{index:04d}" for index in range(DATASET_STOCK_COUNT)]
        session.bulk_insert_mappings(
            StockFeatureDaily,
            [
                {
                    "run_id": latest_run.id,
                    "symbol": symbol,
                    "as_of_date": sector_sessions[-1],
                    "composite_score": float(100 - index % 100),
                    "overall_rating": 99 - index % 99,
                    "passes_count": index % 8,
                    "details_json": {"avg_dollar_volume": 250_000_000 + index * 1000},
                }
                for index, symbol in enumerate(stock_symbols)
            ],
        )
        sectors = ("Technology", "Healthcare", "Financials", "Industrials", "Energy")
        session.bulk_insert_mappings(
            StockUniverse,
            [
                {
                    "symbol": symbol,
                    "name": f"Market Intelligence Company {index:04d}",
                    "market": "US",
                    "exchange": "NASDAQ" if index % 2 else "NYSE",
                    "currency": "USD",
                    "timezone": "America/New_York",
                    "sector": sectors[index % len(sectors)],
                    "industry": f"Industry {index % 25:02d}",
                    "market_cap": float(1_000_000_000 + index * 7_500_000_000),
                    "is_active": True,
                    "status": "active",
                    "is_common_stock": True,
                    "is_sp500": True,
                    "source": "slo_harness",
                }
                for index, symbol in enumerate(stock_symbols)
            ],
        )

        all_price_symbols = stock_symbols + [
            symbol for symbol in ETF_UNIVERSE if symbol not in stock_symbols
        ]
        price_rows = []
        for symbol_index, symbol in enumerate(all_price_symbols):
            base_price = 20.0 + symbol_index % 120
            daily_slope = ((symbol_index % 9) - 4) * 0.0007
            for day_index, trading_date in enumerate(price_sessions):
                close = base_price * (1.0 + daily_slope * day_index)
                normal_volume = 1_000_000 + symbol_index * 100 + day_index * 50
                volume = normal_volume * (2 + symbol_index % 3) if day_index == len(price_sessions) - 1 else normal_volume
                price_rows.append(
                    {
                        "symbol": symbol,
                        "date": trading_date,
                        "open": close,
                        "high": close * 1.005,
                        "low": close * 0.995,
                        "close": close,
                        "adj_close": close,
                        "volume": volume,
                    }
                )
        session.bulk_insert_mappings(StockPrice, price_rows)
        session.commit()

    with engine.begin() as connection:
        connection.execute(text("ANALYZE"))

    return {
        "published_sector_sessions": len(sector_sessions),
        "sector_symbols_per_session": len(MARKET_INTELLIGENCE_UNIVERSE),
        "sp500_symbols": DATASET_STOCK_COUNT,
        "price_symbols": DATASET_STOCK_COUNT + len(ETF_UNIVERSE),
        "price_sessions_per_symbol": len(price_sessions),
        "stock_price_rows": (DATASET_STOCK_COUNT + len(ETF_UNIVERSE)) * len(price_sessions),
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, default=_jsonable) + "\n",
        encoding="utf-8",
    )


def _explain_slowest(engine: Engine, observation: _QueryObservation) -> Any:
    statement = observation.statement.strip().rstrip(";")
    with engine.connect() as connection:
        return connection.exec_driver_sql(
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement,
            observation.parameters,
        ).scalar_one()


def test_latency_summary_uses_stable_linear_percentiles() -> None:
    assert _latency_summary([4.0, 1.0, 3.0, 2.0]) == {
        "sample_count": 4,
        "p50_ms": 2.5,
        "p95_ms": 3.85,
        "worst_ms": 4.0,
    }


def test_build_report_keeps_all_six_families_in_contract_order() -> None:
    samples = {
        family: [float(index), float(index + 1)]
        for index, family in enumerate(API_FAMILIES, start=1)
    }

    report = _build_report(samples)

    assert list(report["api_families"]) == list(API_FAMILIES)
    assert all(
        report["api_families"][family]["sample_count"] == 2
        for family in API_FAMILIES
    )


def test_slo_enforcement_requires_explicit_measured_threshold(monkeypatch) -> None:
    monkeypatch.setenv("MARKET_INTELLIGENCE_ENFORCE_SLO", "1")
    monkeypatch.delenv("MARKET_INTELLIGENCE_SLO_P95_MS", raising=False)

    with pytest.raises(ValueError, match="measured threshold"):
        _configured_slo_ms()


def test_slo_is_disabled_by_default_even_when_threshold_is_present(monkeypatch) -> None:
    monkeypatch.delenv("MARKET_INTELLIGENCE_ENFORCE_SLO", raising=False)
    monkeypatch.setenv("MARKET_INTELLIGENCE_SLO_P95_MS", "250")

    assert _configured_slo_ms() is None


def test_slo_violations_report_only_families_above_measured_threshold() -> None:
    report = _build_report({family: [10.0, 10.0] for family in API_FAMILIES})
    report["api_families"]["sectors_history"]["p95_ms"] = 20.001

    assert _slo_violations(report, threshold_ms=20.0) == [
        "sectors_history p95=20.001ms > 20.000ms"
    ]


@pytest.mark.integration
@pytest.mark.postgresql_integration
@pytest.mark.performance
@pytest.mark.asyncio
async def test_postgresql_16_uncached_read_baseline_and_opt_in_slo(
    phase2_postgresql_engine,
    monkeypatch,
) -> None:
    engine = phase2_postgresql_engine
    assert engine.dialect.server_version_info[0] == 16
    dataset = _seed_representative_dataset(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with engine.connect() as connection:
        postgresql_version = connection.execute(
            text("SELECT version()")
        ).scalar_one()

    api_app = FastAPI()
    api_app.include_router(router, prefix="/api/v1/market-intelligence")

    def override_get_db():
        with factory() as session:
            yield session

    def bypass_read_cache(_key_parts, compute, **_kwargs):
        return compute()

    api_app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(
        market_intelligence_api,
        "cached_market_intelligence_payload",
        bypass_read_cache,
    )

    samples = {family: [] for family in API_FAMILIES}
    recorder = _QueryRecorder(engine)
    recorder.start()
    try:
        transport = httpx.ASGITransport(app=api_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://market-intelligence-slo.test",
        ) as client:
            for family in API_FAMILIES:
                path, params = ENDPOINTS[family]
                recorder.api_family = family
                response = await client.get(path, params=params)
                recorder.api_family = None
                assert response.status_code == 200, response.text

            recorder.clear()
            for _sample_index in range(_sample_count()):
                for family in API_FAMILIES:
                    path, params = ENDPOINTS[family]
                    recorder.api_family = family
                    started_ns = perf_counter_ns()
                    response = await client.get(path, params=params)
                    elapsed_ms = (perf_counter_ns() - started_ns) / 1_000_000
                    recorder.api_family = None
                    assert response.status_code == 200, response.text
                    samples[family].append(elapsed_ms)
    finally:
        recorder.api_family = None
        recorder.stop()
        api_app.dependency_overrides.clear()

    assert recorder.observations, "no relevant PostgreSQL SELECT was captured"
    slowest = max(recorder.observations, key=lambda item: item.elapsed_ms)
    explain_plan = _explain_slowest(engine, slowest)
    threshold_ms = _configured_slo_ms()
    report = _build_report(samples)
    query_counts = {
        family: sum(
            observation.api_family == family
            for observation in recorder.observations
        )
        for family in API_FAMILIES
    }
    report.update(
        {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "environment": {
                "database": "PostgreSQL",
                "postgresql_version": postgresql_version,
                "cache_mode": "bypassed_uncached_db_baseline",
                "live_provider_used": False,
                "redis_used": False,
            },
            "dataset": dataset,
            "sampling": {
                "warmup_count_per_family": 1,
                "sample_count_per_family": _sample_count(),
                "percentile_method": "linear interpolation at (n - 1) * percentile",
            },
            "query_counts": query_counts,
            "slowest_sql": {
                "api_family": slowest.api_family,
                "observed_elapsed_ms": round(slowest.elapsed_ms, 3),
                "statement": slowest.statement,
                "parameters": _jsonable(slowest.parameters),
                "explain_artifact": "market-intelligence-slowest-query-plan.json",
            },
            "slo": {
                "enforced": threshold_ms is not None,
                "p95_threshold_ms": threshold_ms,
                "threshold_source": (
                    None
                    if threshold_ms is None
                    else "MARKET_INTELLIGENCE_SLO_P95_MS"
                ),
            },
        }
    )

    artifact_dir = Path(
        os.environ.get(
            "MARKET_INTELLIGENCE_SLO_ARTIFACT_DIR",
            ".artifacts/market-intelligence-slo",
        )
    ).resolve()
    baseline_path = artifact_dir / "market-intelligence-slo-baseline.json"
    explain_path = artifact_dir / "market-intelligence-slowest-query-plan.json"
    _write_json(baseline_path, report)
    _write_json(
        explain_path,
        {
            "schema_version": 1,
            "api_family": slowest.api_family,
            "observed_elapsed_ms": round(slowest.elapsed_ms, 3),
            "statement": slowest.statement,
            "parameters": _jsonable(slowest.parameters),
            "explain_options": ["ANALYZE", "BUFFERS", "FORMAT JSON"],
            "plan": explain_plan,
        },
    )
    print(f"MARKET_INTELLIGENCE_SLO_BASELINE={baseline_path}")
    print(f"MARKET_INTELLIGENCE_SLO_EXPLAIN={explain_path}")

    if threshold_ms is not None:
        violations = _slo_violations(report, threshold_ms=threshold_ms)
        assert not violations, "; ".join(violations)
