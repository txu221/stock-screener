"""Machine-readable API contract for Phase 1 sector intelligence."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.database import SessionLocal
from app.domain.feature_store.models import DQSeverity, RunStats, RunType
from app.domain.feature_store.quality import DQResult
from app.domain.market_intelligence.constants import (
    LATEST_POINTER_KEY,
    MARKET_INTELLIGENCE_UNIVERSE,
    METRIC_VERSION,
    NORMALIZATION_VERSION,
    PIPELINE_NAME,
    PRICE_BASIS,
    SECTOR_NAMES,
    SECTOR_SYMBOLS,
    UNIVERSE_HASH,
)
from app.domain.market_intelligence.models import (
    IngestionStatus,
    RankDirection,
    RankRecord,
    RequestFailure,
    RunAudit,
    SectorMetrics,
    SectorSnapshot,
)
from app.domain.market_intelligence.ranking import RANKING_METRICS
from app.infra.db.uow import SqlUnitOfWork
from app.api.v1.market_intelligence import router as market_intelligence_router

api_app = FastAPI()
api_app.include_router(
    market_intelligence_router,
    prefix="/api/v1/market-intelligence",
)

MONDAY = date(2026, 5, 11)
TUESDAY = date(2026, 5, 12)
NOW = datetime(2026, 5, 12, 21, 10, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=api_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as value:
        yield value


def _metrics(value: float) -> SectorMetrics:
    return SectorMetrics(
        return_1d=value,
        return_5d=value * 2,
        return_20d=value * 3,
        return_60d=value * 4,
        relative_return_vs_spy_1d=value / 2,
        relative_return_vs_spy_5d=value,
        relative_return_vs_spy_20d=value * 1.5,
        relative_return_vs_spy_60d=value * 2,
        rvol20=1.25,
        flow_pressure_1d_proxy=0.5,
        cmf_5d_proxy=0.2,
        cmf_20d_proxy=0.15,
        cmf_60d_proxy=0.1,
    )


def _snapshot(
    symbol: str,
    trading_date: date,
    *,
    metric_version: str = METRIC_VERSION,
) -> SectorSnapshot:
    ranks = {}
    if symbol != "SPY":
        rank = SECTOR_SYMBOLS.index(symbol) + 1
        ranks = {
            name: RankRecord(
                current_rank=rank,
                previous_rank=rank + 1,
                rank_change=1,
                rank_direction=RankDirection.IMPROVED,
            )
            for name in RANKING_METRICS
        }
    metrics = _metrics((MARKET_INTELLIGENCE_UNIVERSE.index(symbol) + 1) / 100)
    if symbol == "SPY":
        metrics = replace(
            metrics,
            relative_return_vs_spy_1d=None,
            relative_return_vs_spy_5d=None,
            relative_return_vs_spy_20d=None,
            relative_return_vs_spy_60d=None,
        )
    return SectorSnapshot(
        trading_date=trading_date,
        symbol=symbol,
        asset_type="benchmark_etf" if symbol == "SPY" else "sector_etf",
        sector_name=SECTOR_NAMES.get(symbol),
        metrics=metrics,
        ranks=ranks,
        provider="yahoo",
        source_freshness={"status": "FRESH", "as_of": trading_date.isoformat()},
        price_basis=PRICE_BASIS,
        metric_version=metric_version,
        calculation_timestamp=NOW,
        data_quality_status="COMPLETE",
    )


def _audit(
    key: str,
    trading_date: date,
    status: IngestionStatus,
    *,
    metric_version: str = METRIC_VERSION,
    request_failure: RequestFailure | None = None,
) -> RunAudit:
    complete = status is IngestionStatus.SUCCEEDED
    counters = {
        "expected_symbols": 12,
        "symbols_received": 12 if complete else 11,
        "valid_bars": 1080 if complete else 990,
        "rejected_bars": 0 if complete else 1,
        "missing_symbols": 0 if complete else 1,
        "duplicate_rows": 0,
        "invalid_volume": 0 if complete else 1,
        "invalid_ohlc": 0,
        "usable_symbols": 12 if complete else 11,
        "snapshot_rows": 12 if complete else 11,
    }
    if status is IngestionStatus.FAILED:
        counters = {name: 0 for name in counters}
        counters["expected_symbols"] = 12
        counters["missing_symbols"] = 12
    return RunAudit(
        idempotency_key=key,
        input_hash=key[::-1],
        ingestion_status=status,
        provider="yahoo",
        provider_status=("AVAILABLE" if complete else "UNAVAILABLE" if request_failure else "DEGRADED"),
        request_failure=request_failure,
        metric_version=metric_version,
        normalization_version=NORMALIZATION_VERSION,
        price_basis=PRICE_BASIS,
        counters=counters,
        missing_symbols=(() if complete else tuple(MARKET_INTELLIGENCE_UNIVERSE[-1:])),
        provider_failures=(),
        target_session=trading_date,
        provider_response_at=None if request_failure else NOW,
        source_freshness={
            "status": "FRESH" if complete else "STALE_OR_MISSING",
            "as_of": trading_date.isoformat(),
        },
        calculation_timestamp=NOW,
        ingestion_timestamp=NOW,
    )


def _start(uow, trading_date: date, key: str):
    return uow.feature_runs.start_run(
        as_of_date=trading_date,
        run_type=RunType.DAILY_SNAPSHOT,
        universe_hash=UNIVERSE_HASH,
        input_hash=key[::-1],
        config_json={"pipeline": PIPELINE_NAME},
    )


def _persist_published(
    uow,
    trading_date: date,
    key: str,
    *,
    metric_version: str = METRIC_VERSION,
) -> int:
    run = _start(uow, trading_date, key)
    uow.market_intelligence.persist_candidate(
        run.id,
        _audit(key, trading_date, IngestionStatus.SUCCEEDED, metric_version=metric_version),
        (),
        (),
        tuple(
            _snapshot(symbol, trading_date, metric_version=metric_version)
            for symbol in MARKET_INTELLIGENCE_UNIVERSE
        ),
    )
    uow.feature_runs.mark_completed(run.id, RunStats(12, 12, 0, 1.0, 12))
    uow.feature_runs.publish_atomically(run.id, LATEST_POINTER_KEY)
    return run.id


@dataclass(frozen=True)
class _SeededRuns:
    published_id: int
    partial_id: int


@pytest.fixture
def seeded_runs() -> _SeededRuns:
    with SqlUnitOfWork(SessionLocal) as uow:
        published_id = _persist_published(uow, MONDAY, "1" * 64)
        partial = _start(uow, TUESDAY, "2" * 64)
        uow.market_intelligence.persist_candidate(
            partial.id,
            _audit("2" * 64, TUESDAY, IngestionStatus.PARTIAL),
            (),
            (),
            tuple(
                _snapshot(symbol, TUESDAY)
                for symbol in MARKET_INTELLIGENCE_UNIVERSE
                if symbol != "XLU"
            ),
        )
        uow.feature_runs.mark_completed(partial.id, RunStats(12, 11, 1, 1.0, 11))
        uow.feature_runs.mark_quarantined(
            partial.id,
            (
                DQResult(
                    "market_intelligence_completeness",
                    False,
                    DQSeverity.CRITICAL,
                    11.0,
                    12.0,
                    "partial",
                ),
            ),
        )
        uow.commit()
    return _SeededRuns(published_id, partial.id)


@pytest.mark.asyncio
async def test_latest_without_published_snapshot_is_404(client) -> None:
    response = await client.get("/api/v1/market-intelligence/sectors/latest")

    assert response.status_code == 404
    assert "No complete sector intelligence snapshot" in response.json()["detail"]


@pytest.mark.asyncio
async def test_latest_serves_previous_complete_with_stable_contract(
    client,
    seeded_runs: _SeededRuns,
) -> None:
    response = await client.get("/api/v1/market-intelligence/sectors/latest")

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == seeded_runs.published_id
    assert body["status"] == "SUCCEEDED"
    assert body["run_status"] == "SUCCEEDED"
    assert body["as_of"] == MONDAY.isoformat()
    assert body["published_at"] is not None
    assert body["provider"] == "yahoo"
    assert body["metric_version"] == METRIC_VERSION
    assert body["normalization_version"] == NORMALIZATION_VERSION
    assert body["price_basis"] == PRICE_BASIS
    assert body["benchmark"]["symbol"] == "SPY"
    assert [item["symbol"] for item in body["sectors"]] == list(SECTOR_SYMBOLS)
    xlk = next(item for item in body["sectors"] if item["symbol"] == "XLK")
    assert xlk["flow_pressure_proxy"]["metric_type"] == "derived_proxy"
    assert xlk["ranks"]["relative_return_vs_spy_20d"] == 10
    assert xlk["previous_ranks"]["relative_return_vs_spy_20d"] == 11
    assert xlk["rank_changes"]["relative_return_vs_spy_20d"] == 1
    assert xlk["rank_directions"]["relative_return_vs_spy_20d"] == "IMPROVED"


@pytest.mark.asyncio
async def test_health_separates_latest_attempt_from_published_and_uses_audit_counters(
    client,
    seeded_runs: _SeededRuns,
) -> None:
    response = await client.get("/api/v1/market-intelligence/sectors/health")

    assert response.status_code == 200
    body = response.json()
    assert body["latest_attempt"]["run_id"] == seeded_runs.partial_id
    assert body["latest_attempt"]["status"] == "PARTIAL"
    assert body["latest_attempt"]["counters"]["invalid_volume"] == 1
    assert body["latest_attempt"]["counters"]["valid_bars"] == 990
    assert body["latest_published"]["run_id"] == seeded_runs.published_id
    assert body["latest_published"]["status"] == "SUCCEEDED"
    assert body["publication_occurred"] is False


@pytest.mark.asyncio
async def test_health_failed_request_has_zero_row_rejections(client) -> None:
    with SqlUnitOfWork(SessionLocal) as uow:
        run = _start(uow, TUESDAY, "3" * 64)
        uow.market_intelligence.persist_candidate(
            run.id,
            _audit(
                "3" * 64,
                TUESDAY,
                IngestionStatus.FAILED,
                request_failure=RequestFailure("PROVIDER_TIMEOUT", "timeout"),
            ),
            (), (), (),
        )
        uow.feature_runs.mark_failed(run.id, RunStats(12, 0, 12, 1.0, 0))
        uow.commit()

    body = (
        await client.get("/api/v1/market-intelligence/sectors/health")
    ).json()

    assert body["latest_attempt"]["status"] == "FAILED"
    assert body["latest_attempt"]["request_failure"]["code"] == "PROVIDER_TIMEOUT"
    assert body["latest_attempt"]["counters"]["rejected_bars"] == 0
    assert body["latest_attempt"]["counters"]["valid_bars"] == 0
    assert body["latest_published"] is None


@pytest.mark.asyncio
async def test_history_filters_symbol_dates_and_metric_version(client) -> None:
    with SqlUnitOfWork(SessionLocal) as uow:
        v1_id = _persist_published(uow, MONDAY, "4" * 64)
        _persist_published(
            uow,
            TUESDAY,
            "5" * 64,
            metric_version="market_intelligence_v2",
        )
        uow.commit()

    response = await client.get(
        "/api/v1/market-intelligence/sectors/history",
        params={
            "symbol": "XLK",
            "date_from": MONDAY.isoformat(),
            "date_to": TUESDAY.isoformat(),
            "metric_version": METRIC_VERSION,
            "limit": 10,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["metric_version"] == METRIC_VERSION
    assert len(body["items"]) == 1
    assert body["items"][0]["run_id"] == v1_id
    assert [item["symbol"] for item in body["items"][0]["snapshots"]] == ["XLK"]


@pytest.mark.asyncio
async def test_history_rejects_symbol_outside_fixed_universe(client) -> None:
    response = await client.get(
        "/api/v1/market-intelligence/sectors/history",
        params={"symbol": "AAPL"},
    )

    assert response.status_code == 422
