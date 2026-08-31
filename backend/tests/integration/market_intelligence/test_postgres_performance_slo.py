"""PostgreSQL Market Intelligence read baseline and opt-in SLO evidence."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from time import perf_counter_ns
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
import httpx
import pytest
from fastapi import APIRouter, FastAPI
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import sessionmaker

import app.api.v1.market_intelligence as market_intelligence_api
from app.api.v1.market_intelligence import router as market_intelligence_router
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
from app.services.server_auth import require_server_session
from tests.integration.market_intelligence.scenario import weekday_sessions


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
DEFAULT_SAMPLE_COUNT = 20
SLO_DATABASE_NAME = "market_intelligence_slo"
BACKEND_ROOT = Path(__file__).resolve().parents[3]


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
    samples: dict[str, list[float]], *, threshold_ms: float
) -> list[str]:
    violations = []
    for family in API_FAMILIES:
        raw_p95_ms = _linear_percentile(samples[family], 0.95)
        if raw_p95_ms > threshold_ms:
            violations.append(
                f"{family} p95={raw_p95_ms:.6f}ms > {threshold_ms:.6f}ms"
            )
    return violations


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


def _require_exact_database_name(database_name: str | None) -> str:
    normalized = (database_name or "").strip()
    if normalized != SLO_DATABASE_NAME:
        raise ValueError(
            "Market Intelligence SLO evidence requires the exact database "
            f"name {SLO_DATABASE_NAME!r}; received {normalized!r}"
        )
    return normalized


def _synthetic_market_cap(index: int, stock_count: int) -> float:
    if stock_count < 2:
        raise ValueError("stock_count must be at least two")
    if index < 0 or index >= stock_count:
        raise ValueError("index must identify a stock in the synthetic universe")
    lower = 5_000_000_000.0
    upper = 3_000_000_000_000.0
    rank_fraction = (stock_count - 1 - index) / (stock_count - 1)
    return lower + (upper - lower) * rank_fraction**6


def _migration_state(connection: Connection) -> dict[str, list[str]]:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    expected_heads = sorted(ScriptDirectory.from_config(config).get_heads())
    database_heads = sorted(
        connection.execute(text("SELECT version_num FROM alembic_version")).scalars()
    )
    if database_heads != expected_heads:
        raise AssertionError(
            "SLO database is not migrated to the repository Alembic heads: "
            f"database={database_heads}, expected={expected_heads}"
        )
    return {
        "database_heads": database_heads,
        "expected_heads": expected_heads,
    }


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
    request_index: int
    elapsed_ms: float
    statement: str
    parameters: Any


@dataclass(frozen=True)
class _RequestObservation:
    api_family: str
    sample_index: int
    api_elapsed_ms: float


class _QueryRecorder:
    """Capture individual SELECT timings while one API family is active."""

    def __init__(self, engine: Engine):
        self.engine = engine
        self.api_family: str | None = None
        self.request_index: int | None = None
        self.observations: list[_QueryObservation] = []
        self.capture_bookkeeping_ns = 0

    def start(self) -> None:
        event.listen(self.engine, "before_cursor_execute", self._before)
        event.listen(self.engine, "after_cursor_execute", self._after)

    def stop(self) -> None:
        event.remove(self.engine, "before_cursor_execute", self._before)
        event.remove(self.engine, "after_cursor_execute", self._after)

    def clear(self) -> None:
        self.observations.clear()
        self.capture_bookkeeping_ns = 0

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
        bookkeeping_started_ns = perf_counter_ns()
        elapsed_ms = (
            bookkeeping_started_ns - context._market_intelligence_slo_started_ns
        ) / 1_000_000
        captured_parameters = (
            dict(parameters) if isinstance(parameters, dict) else tuple(parameters)
        )
        assert self.request_index is not None
        self.observations.append(
            _QueryObservation(
                api_family=self.api_family,
                request_index=self.request_index,
                elapsed_ms=elapsed_ms,
                statement=statement,
                parameters=captured_parameters,
            )
        )
        self.capture_bookkeeping_ns += perf_counter_ns() - bookkeeping_started_ns


def _statement_fingerprint(statement: str) -> tuple[str, str]:
    normalized = " ".join(statement.split())
    fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return fingerprint, normalized


def _build_query_evidence(
    requests: list[_RequestObservation],
    observations: list[_QueryObservation],
    *,
    capture_bookkeeping_ns: int,
) -> dict[str, object]:
    observations_by_request: dict[tuple[str, int], list[_QueryObservation]] = (
        defaultdict(list)
    )
    for observation in observations:
        observations_by_request[
            (observation.api_family, observation.request_index)
        ].append(observation)

    requests_by_family: dict[str, list[dict[str, int | float]]] = defaultdict(list)
    for request in requests:
        request_queries = observations_by_request[
            (request.api_family, request.sample_index)
        ]
        aggregate_sql_ms = sum(item.elapsed_ms for item in request_queries)
        requests_by_family[request.api_family].append(
            {
                "sample_index": request.sample_index,
                "query_count": len(request_queries),
                "api_elapsed_ms": round(request.api_elapsed_ms, 3),
                "aggregate_sql_ms": round(aggregate_sql_ms, 3),
                "api_minus_sql_ms": round(
                    request.api_elapsed_ms - aggregate_sql_ms,
                    3,
                ),
            }
        )

    family_evidence: dict[str, object] = {}
    for family in API_FAMILIES:
        family_requests = requests_by_family[family]
        family_observations = [
            item for item in observations if item.api_family == family
        ]
        statement_groups: dict[str, dict[str, object]] = {}
        for observation in family_observations:
            fingerprint, normalized = _statement_fingerprint(observation.statement)
            group = statement_groups.setdefault(
                fingerprint,
                {
                    "fingerprint": fingerprint,
                    "statement": normalized,
                    "frequency": 0,
                    "aggregate_sql_ms": 0.0,
                },
            )
            group["frequency"] += 1
            group["aggregate_sql_ms"] += observation.elapsed_ms
        statements = []
        for fingerprint in sorted(statement_groups):
            group = dict(statement_groups[fingerprint])
            group["aggregate_sql_ms"] = round(group["aggregate_sql_ms"], 3)
            statements.append(group)
        aggregate_api_ms = sum(
            item.api_elapsed_ms
            for item in requests
            if item.api_family == family
        )
        aggregate_sql_ms = sum(item.elapsed_ms for item in family_observations)
        family_evidence[family] = {
            "request_count": len(family_requests),
            "total_query_count": len(family_observations),
            "aggregate_api_ms": round(aggregate_api_ms, 3),
            "aggregate_sql_ms": round(aggregate_sql_ms, 3),
            "aggregate_api_minus_sql_ms": round(
                aggregate_api_ms - aggregate_sql_ms,
                3,
            ),
            "requests": family_requests,
            "statement_fingerprints": statements,
        }

    capture_total_ms = capture_bookkeeping_ns / 1_000_000
    return {
        "capture_overhead": {
            "scope": "measured after-cursor bookkeeping only",
            "bookkeeping_total_ms": round(capture_total_ms, 6),
            "bookkeeping_per_select_ms": (
                capture_total_ms / len(observations) if observations else 0.0
            ),
            "included_in_api_elapsed": True,
            "excluded_from_sql_elapsed": True,
            "unisolated_components": [
                "SQLAlchemy event dispatch",
                "before-cursor timer and context assignment",
                "after-cursor timer read",
            ],
        },
        "api_families": family_evidence,
    }


def _published_at(as_of: date, sequence: int) -> datetime:
    return datetime(
        as_of.year,
        as_of.month,
        as_of.day,
        22,
        sequence % 60,
        tzinfo=timezone.utc,
    )


def _seed_representative_dataset(engine: Engine) -> dict[str, int]:
    """Seed a fixed read-heavy dataset without network or Redis dependencies."""

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
                    "market_cap": _synthetic_market_cap(
                        index,
                        DATASET_STOCK_COUNT,
                    ),
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


class _EvidenceArtifacts:
    """Persist complete or partial evidence without replacing a test failure."""

    def __init__(
        self,
        baseline_path: Path,
        plan_path: Path,
        report: dict[str, object],
        plan: dict[str, object],
    ) -> None:
        self.baseline_path = baseline_path
        self.plan_path = plan_path
        self.report = report
        self.plan = plan

    def __enter__(self) -> _EvidenceArtifacts:
        return self

    def __exit__(self, exc_type, exc, _traceback) -> bool:
        if exc is None:
            self.report["status"] = "completed"
        else:
            self.report["status"] = "failed"
            stages = self.report.get("stages", {})
            for name, status in tuple(stages.items()):
                if status == "running":
                    stages[name] = "failed"
            self.report["failure"] = {
                "type": type(exc).__name__,
                "message": str(exc),
            }
            if self.plan.get("status") != "completed":
                self.plan["status"] = "not_completed"
                self.plan["failure"] = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                }

        write_failures = []
        for path, payload in (
            (self.baseline_path, self.report),
            (self.plan_path, self.plan),
        ):
            try:
                _write_json(path, payload)
            except Exception as write_error:  # pragma: no cover - filesystem failure
                write_failures.append(f"{path}: {write_error}")
        if write_failures:
            message = "failed to persist SLO evidence: " + "; ".join(write_failures)
            if exc is None:
                raise RuntimeError(message)
            print(message, file=sys.stderr)
        return False


def _build_benchmark_app(session_factory, api_router: APIRouter | None = None) -> FastAPI:
    if api_router is None:
        from app.api.v1.router import router as api_router

    api_app = FastAPI()
    api_app.include_router(api_router, prefix="/api/v1")

    def override_get_db():
        with session_factory() as session:
            yield session

    api_app.dependency_overrides[get_db] = override_get_db
    api_app.dependency_overrides[require_server_session] = lambda: True
    return api_app


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
    samples = {family: [10.0, 10.0] for family in API_FAMILIES}
    samples["sectors_history"] = [20.0004, 20.0004]

    assert _slo_violations(samples, threshold_ms=20.0) == [
        "sectors_history p95=20.000400ms > 20.000000ms"
    ]


def test_sample_count_defaults_to_twenty(monkeypatch) -> None:
    monkeypatch.delenv("MARKET_INTELLIGENCE_SLO_SAMPLE_COUNT", raising=False)

    assert _sample_count() == 20


def test_exact_slo_database_name_rejects_similar_names() -> None:
    assert _require_exact_database_name("market_intelligence_slo") == (
        "market_intelligence_slo"
    )

    with pytest.raises(ValueError, match="exact database"):
        _require_exact_database_name("market_intelligence_slo_copy")


def test_synthetic_market_caps_are_bounded_and_ranked() -> None:
    values = [
        _synthetic_market_cap(index, DATASET_STOCK_COUNT)
        for index in range(DATASET_STOCK_COUNT)
    ]

    assert values[0] == 3_000_000_000_000.0
    assert values[-1] == 5_000_000_000.0
    assert all(left > right for left, right in zip(values, values[1:]))


def test_query_evidence_has_per_request_frequencies_and_non_sql_time() -> None:
    requests = [
        _RequestObservation("overview", 0, 8.0),
        _RequestObservation("overview", 1, 7.0),
    ]
    observations = [
        _QueryObservation("overview", 0, 1.0, "SELECT  1", ()),
        _QueryObservation("overview", 0, 2.0, "SELECT 2", ()),
        _QueryObservation("overview", 1, 1.0, "SELECT 1", ()),
    ]

    evidence = _build_query_evidence(
        requests,
        observations,
        capture_bookkeeping_ns=500_000,
    )

    overview = evidence["api_families"]["overview"]
    assert overview["request_count"] == 2
    assert overview["total_query_count"] == 3
    assert overview["aggregate_api_ms"] == 15.0
    assert overview["aggregate_sql_ms"] == 4.0
    assert overview["aggregate_api_minus_sql_ms"] == 11.0
    assert [item["query_count"] for item in overview["requests"]] == [2, 1]
    frequencies = sorted(
        item["frequency"] for item in overview["statement_fingerprints"]
    )
    assert frequencies == [1, 2]
    assert evidence["capture_overhead"]["bookkeeping_total_ms"] == 0.5
    assert evidence["capture_overhead"]["bookkeeping_per_select_ms"] == pytest.approx(
        1 / 6
    )


def test_evidence_artifacts_write_partial_state_without_hiding_failure(tmp_path) -> None:
    baseline_path = tmp_path / "baseline.json"
    plan_path = tmp_path / "plan.json"
    report = {"status": "running", "stages": {"seed": "running"}}
    plan = {"status": "pending"}

    with pytest.raises(RuntimeError, match="seed exploded"):
        with _EvidenceArtifacts(baseline_path, plan_path, report, plan):
            raise RuntimeError("seed exploded")

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    plan_payload = json.loads(plan_path.read_text(encoding="utf-8"))
    assert baseline["status"] == "failed"
    assert baseline["stages"]["seed"] == "failed"
    assert baseline["failure"] == {
        "type": "RuntimeError",
        "message": "seed exploded",
    }
    assert plan_payload["status"] == "not_completed"


def test_benchmark_app_uses_production_route_and_explicit_overrides() -> None:
    api_router = APIRouter()
    api_router.include_router(
        market_intelligence_router,
        prefix="/market-intelligence",
    )
    app = _build_benchmark_app(lambda: None, api_router)

    assert "/api/v1/market-intelligence/overview" in {
        route.path for route in app.routes
    }
    assert get_db in app.dependency_overrides
    assert require_server_session in app.dependency_overrides


def test_workflow_has_dedicated_migrated_postgresql_slo_job() -> None:
    workflow = (
        Path(__file__).parents[4]
        / ".github"
        / "workflows"
        / "market-intelligence-integration.yml"
    ).read_text(encoding="utf-8")

    assert "market-intelligence-slo:" in workflow
    ordinary_jobs, remainder = workflow.split("  market-intelligence-slo:", 1)
    slo_job = remainder.split("  live-yahoo-and-celery:", 1)[0]
    assert (
        "-k postgresql_16_uncached_read_baseline_and_opt_in_slo"
        not in ordinary_jobs
    )
    assert "image: postgres:16-alpine" in slo_job
    assert "POSTGRES_DB: market_intelligence_slo" in slo_job
    assert "python -m alembic upgrade head" in slo_job
    assert 'MARKET_INTELLIGENCE_SLO_SAMPLE_COUNT: "20"' in slo_job
    assert 'MARKET_INTELLIGENCE_ENFORCE_SLO: "1"' in slo_job
    assert 'MARKET_INTELLIGENCE_SLO_P95_MS: "1000"' in slo_job
    assert "if: always()" in slo_job
    assert "actions/upload-artifact@v4" in slo_job


@pytest.mark.integration
@pytest.mark.postgresql_integration
@pytest.mark.performance
@pytest.mark.asyncio
async def test_postgresql_16_uncached_read_baseline_and_opt_in_slo(
    phase2_postgresql_url,
    monkeypatch,
) -> None:
    artifact_dir = Path(
        os.environ.get(
            "MARKET_INTELLIGENCE_SLO_ARTIFACT_DIR",
            ".artifacts/market-intelligence-slo",
        )
    ).resolve()
    baseline_path = artifact_dir / "market-intelligence-slo-baseline.json"
    explain_path = artifact_dir / "market-intelligence-slowest-query-plan.json"
    print(f"MARKET_INTELLIGENCE_SLO_BASELINE={baseline_path}")
    print(f"MARKET_INTELLIGENCE_SLO_EXPLAIN={explain_path}")

    stage_names = (
        "database_validation",
        "seed",
        "warmup",
        "measured_requests",
        "query_capture",
        "explain",
        "slo_evaluation",
    )
    report: dict[str, object] = {
        "schema_version": 2,
        "status": "running",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "stages": {name: "pending" for name in stage_names},
        "benchmark_scope": {
            "included": [
                "FastAPI production v1 router matching",
                "explicit dependency-override resolution",
                "handler, ORM, PostgreSQL, and response serialization",
            ],
            "excluded": [
                "ASGI server and worker scheduling",
                "socket, network, reverse-proxy, and TLS overhead",
                "production lifespan and main-app middleware",
                "real server-session authentication logic",
            ],
            "transport": "httpx ASGITransport in-process",
        },
    }
    plan_payload: dict[str, object] = {
        "schema_version": 2,
        "status": "pending",
        "explain_options": ["ANALYZE", "BUFFERS", "FORMAT JSON"],
    }
    samples = {family: [] for family in API_FAMILIES}
    requests: list[_RequestObservation] = []
    engine: Engine | None = None
    recorder: _QueryRecorder | None = None
    recorder_started = False
    api_app: FastAPI | None = None

    with _EvidenceArtifacts(
        baseline_path,
        explain_path,
        report,
        plan_payload,
    ):
        try:
            engine = create_engine(phase2_postgresql_url, pool_pre_ping=True)
            environment: dict[str, object] = {
                "database": "PostgreSQL",
                "configured_database_name": engine.url.database,
                "schema_source": "alembic_upgrade_head",
                "cache_mode": "bypassed_uncached_db_baseline",
                "live_provider_used": False,
                "redis_used": False,
            }
            report["environment"] = environment
            report["stages"]["database_validation"] = "running"
            configured_database = _require_exact_database_name(engine.url.database)
            with engine.connect() as connection:
                actual_database = _require_exact_database_name(
                    connection.execute(text("SELECT current_database()")).scalar_one()
                )
                postgresql_version = connection.execute(
                    text("SELECT version()")
                ).scalar_one()
                server_version_info = engine.dialect.server_version_info
                environment.update(
                    {
                        "database_name": actual_database,
                        "configured_database_name": configured_database,
                        "postgresql_version": postgresql_version,
                        "server_version_info": (
                            list(server_version_info)
                            if server_version_info is not None
                            else None
                        ),
                    }
                )
                if not server_version_info or server_version_info[0] != 16:
                    raise AssertionError(
                        "Market Intelligence SLO evidence requires PostgreSQL 16; "
                        f"received {server_version_info!r}"
                    )
                migration = _migration_state(connection)
            environment["migration"] = migration
            report["stages"]["database_validation"] = "completed"

            report["stages"]["seed"] = "running"
            dataset = _seed_representative_dataset(engine)
            report["dataset"] = dataset
            report["stages"]["seed"] = "completed"

            report["stages"]["warmup"] = "running"
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            api_app = _build_benchmark_app(factory)

            def bypass_read_cache(_key_parts, compute, **_kwargs):
                return compute()

            monkeypatch.setattr(
                market_intelligence_api,
                "cached_market_intelligence_payload",
                bypass_read_cache,
            )

            transport = httpx.ASGITransport(app=api_app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://market-intelligence-slo.test",
            ) as client:
                for family in API_FAMILIES:
                    path, params = ENDPOINTS[family]
                    response = await client.get(path, params=params)
                    assert response.status_code == 200, response.text
                report["stages"]["warmup"] = "completed"

                recorder = _QueryRecorder(engine)
                recorder.start()
                recorder_started = True
                report["stages"]["measured_requests"] = "running"
                report["stages"]["query_capture"] = "running"
                for sample_index in range(_sample_count()):
                    for family in API_FAMILIES:
                        path, params = ENDPOINTS[family]
                        observation_start = len(recorder.observations)
                        recorder.api_family = family
                        recorder.request_index = sample_index
                        started_ns = perf_counter_ns()
                        try:
                            response = await client.get(path, params=params)
                        finally:
                            elapsed_ms = (
                                perf_counter_ns() - started_ns
                            ) / 1_000_000
                            recorder.api_family = None
                            recorder.request_index = None
                        assert response.status_code == 200, response.text
                        samples[family].append(elapsed_ms)
                        requests.append(
                            _RequestObservation(family, sample_index, elapsed_ms)
                        )
                        request_queries = recorder.observations[observation_start:]
                        assert request_queries, (
                            f"{family} sample {sample_index} captured no relevant "
                            "PostgreSQL SELECT"
                        )
                report["stages"]["measured_requests"] = "completed"
                report["stages"]["query_capture"] = "completed"

            recorder.stop()
            recorder_started = False
            assert recorder.observations
            slowest = max(recorder.observations, key=lambda item: item.elapsed_ms)
            fingerprint, normalized_statement = _statement_fingerprint(
                slowest.statement
            )
            report.update(_build_report(samples))
            report["schema_version"] = 2
            report["sampling"] = {
                "warmup_count_per_family": 1,
                "sample_count_per_family": _sample_count(),
                "percentile_method": (
                    "linear interpolation at (n - 1) * percentile"
                ),
                "enforcement_precision": "unrounded p95",
                "serialized_precision": "milliseconds rounded to 3 decimals",
            }
            report["slowest_sql"] = {
                "api_family": slowest.api_family,
                "request_index": slowest.request_index,
                "fingerprint": fingerprint,
                "observed_elapsed_ms": round(slowest.elapsed_ms, 3),
                "statement": normalized_statement,
                "parameters": _jsonable(slowest.parameters),
                "explain_artifact": "market-intelligence-slowest-query-plan.json",
            }

            report["stages"]["explain"] = "running"
            plan_payload.update(
                {
                    "status": "running",
                    "api_family": slowest.api_family,
                    "request_index": slowest.request_index,
                    "fingerprint": fingerprint,
                    "observed_elapsed_ms": round(slowest.elapsed_ms, 3),
                    "statement": normalized_statement,
                    "parameters": _jsonable(slowest.parameters),
                }
            )
            plan_payload["plan"] = _explain_slowest(engine, slowest)
            plan_payload["status"] = "completed"
            report["stages"]["explain"] = "completed"

            report["stages"]["slo_evaluation"] = "running"
            threshold_ms = _configured_slo_ms()
            report["slo"] = {
                "enforced": threshold_ms is not None,
                "p95_threshold_ms": threshold_ms,
                "threshold_source": (
                    None
                    if threshold_ms is None
                    else "MARKET_INTELLIGENCE_SLO_P95_MS"
                ),
                "comparison_precision": "unrounded",
            }
            if threshold_ms is not None:
                violations = _slo_violations(samples, threshold_ms=threshold_ms)
                assert not violations, "; ".join(violations)
            report["stages"]["slo_evaluation"] = "completed"
        finally:
            if recorder is not None:
                report["samples_collected"] = {
                    family: len(samples[family]) for family in API_FAMILIES
                }
                report["api_families"] = {
                    family: _latency_summary(samples[family])
                    for family in API_FAMILIES
                    if samples[family]
                }
                report["query_evidence"] = _build_query_evidence(
                    requests,
                    recorder.observations,
                    capture_bookkeeping_ns=recorder.capture_bookkeeping_ns,
                )
                recorder.api_family = None
                recorder.request_index = None
                if recorder_started:
                    recorder.stop()
            if api_app is not None:
                api_app.dependency_overrides.clear()
            if engine is not None:
                engine.dispose()
