"""Machine-readable API contract for Phase 1 sector intelligence."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone

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
from app.domain.market_intelligence.observability import (
    PIPELINE_VERSION,
    MarketIntelligenceErrorCategory,
    complete_stage_timings,
)
from app.domain.market_intelligence.ranking import RANKING_METRICS
from app.infra.db.models.feature_store import FeatureRunPointer
from app.infra.db.uow import SqlUnitOfWork
from app.infra.db.repositories.market_intelligence_repo import (
    SqlMarketIntelligenceRepository,
)
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
        pipeline_version=PIPELINE_VERSION,
        failure_category=(
            None
            if complete
            else MarketIntelligenceErrorCategory.PROVIDER_FAILURE
            if request_failure is not None
            else MarketIntelligenceErrorCategory.INVALID_MARKET_DATA
        ),
        stage_timings=complete_stage_timings(
            {"provider_fetch_ms": 125.0, "total_ms": 200.0}
        ),
        publication_status=(
            "PUBLISHED"
            if complete
            else "FAILED"
            if status is IngestionStatus.FAILED
            else "QUARANTINED"
        ),
        retry_status="NOT_RETRYABLE" if complete else "RETRYABLE",
        reuse_status="NEW",
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
    keep_latest_pointer: bool = False,
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
    if keep_latest_pointer:
        uow.feature_runs.publish_atomically_if_not_older(
            run.id,
            LATEST_POINTER_KEY,
        )
    else:
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
    monkeypatch,
) -> None:
    aggregate_calls: list[str] = []
    original_aggregate = SqlMarketIntelligenceRepository.get_health_aggregate

    def tracked_aggregate(repository, pointer_key):
        aggregate_calls.append(pointer_key)
        return original_aggregate(repository, pointer_key)

    monkeypatch.setattr(
        SqlMarketIntelligenceRepository,
        "get_health_aggregate",
        tracked_aggregate,
    )

    def inconsistent_legacy_selector(*_args, **_kwargs):
        raise AssertionError("health endpoint must use the atomic aggregate selector")

    for method_name in (
        "get_latest_attempt",
        "get_latest_published",
        "get_last_successful_attempt",
        "count_consecutive_failures",
    ):
        monkeypatch.setattr(
            SqlMarketIntelligenceRepository,
            method_name,
            inconsistent_legacy_selector,
        )
    monkeypatch.setattr(
        "app.api.v1.market_intelligence._completed_us_sessions",
        lambda: (MONDAY, TUESDAY),
    )
    monkeypatch.setattr(
        "app.api.v1.market_intelligence._utc_now",
        lambda: NOW + timedelta(hours=1),
    )
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
    assert body["publication_status"] == "SERVING_PREVIOUS"
    assert body["freshness_status"] == "AGING"
    assert body["last_attempt_age_seconds"] == 3600.0
    assert body["last_success_age_seconds"] == 3600.0
    assert body["provider_latency_ms"] == 125.0
    assert body["failure_category"] == "INVALID_MARKET_DATA"
    assert body["consecutive_failures"] == 1
    assert body["last_successful_trading_date"] == MONDAY.isoformat()
    assert body["stale_threshold_completed_sessions"] == 2
    assert body["pipeline_version"] == PIPELINE_VERSION
    assert body["latest_attempt"]["provider_latency_ms"] == 125.0
    assert body["latest_attempt"]["publication_status"] == "QUARANTINED"
    assert body["latest_attempt"]["retry_status"] == "RETRYABLE"
    assert body["latest_attempt"]["reuse_status"] == "NEW"
    assert aggregate_calls == [LATEST_POINTER_KEY]


@pytest.mark.asyncio
async def test_health_counts_consecutive_failures_since_last_persisted_success(
    client,
    seeded_runs: _SeededRuns,
    monkeypatch,
) -> None:
    del seeded_runs
    with SqlUnitOfWork(SessionLocal) as uow:
        run = _start(uow, TUESDAY, "9" * 64)
        uow.market_intelligence.persist_candidate(
            run.id,
            _audit(
                "9" * 64,
                TUESDAY,
                IngestionStatus.FAILED,
                request_failure=RequestFailure("PROVIDER_TIMEOUT", "timeout"),
            ),
            (),
            (),
            (),
        )
        uow.feature_runs.mark_failed(run.id, RunStats(12, 0, 12, 1.0, 0))
        uow.commit()

    monkeypatch.setattr(
        "app.api.v1.market_intelligence._completed_us_sessions",
        lambda: (MONDAY, TUESDAY),
    )
    body = (
        await client.get("/api/v1/market-intelligence/sectors/health")
    ).json()

    assert body["consecutive_failures"] == 2
    assert body["failure_category"] == "PROVIDER_FAILURE"
    assert body["last_successful_trading_date"] == MONDAY.isoformat()


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
    assert body["publication_status"] == "UNAVAILABLE"
    assert body["freshness_status"] == "UNAVAILABLE"
    assert body["last_successful_run"] is None
    assert body["last_successful_trading_date"] is None


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


@pytest.mark.asyncio
async def test_sector_reads_use_stable_pointer_cache_but_health_never_does(
    client,
    monkeypatch,
) -> None:
    from app.api.v1 import market_intelligence as module

    with SqlUnitOfWork(SessionLocal) as uow:
        run_id = _persist_published(uow, MONDAY, "6" * 64)
        uow.commit()

    observed = []

    def capture(key_parts, compute, **_kwargs):
        observed.append(key_parts)
        return compute()

    monkeypatch.setattr(
        module,
        "cached_market_intelligence_payload",
        capture,
        raising=False,
    )

    latest = await client.get("/api/v1/market-intelligence/sectors/latest")
    history = await client.get(
        "/api/v1/market-intelligence/sectors/history",
        params={"symbol": " xlk ", "limit": 10},
    )
    cache_calls_before_health = len(observed)
    health = await client.get("/api/v1/market-intelligence/sectors/health")

    assert latest.status_code == history.status_code == health.status_code == 200
    assert cache_calls_before_health == 2
    assert len(observed) == cache_calls_before_health
    assert [parts.endpoint for parts in observed] == [
        "sectors-latest",
        "sectors-history",
    ]
    assert all(parts.stable_run_id == run_id for parts in observed)
    assert all(parts.stable_trading_date == MONDAY for parts in observed)
    assert all(parts.metric_version == METRIC_VERSION for parts in observed)
    assert all(parts.stable_pointer_revision is not None for parts in observed)
    assert observed[1].params["symbol"] == "XLK"
    assert observed[1].params["limit"] == 10


@pytest.mark.asyncio
async def test_history_normalizes_metric_version_for_query_and_parameter_key(
    client,
    monkeypatch,
) -> None:
    from app.api.v1 import market_intelligence as module

    with SqlUnitOfWork(SessionLocal) as uow:
        _persist_published(uow, MONDAY, "7" * 64)
        uow.commit()
    observed = []

    def capture(key_parts, compute, **_kwargs):
        observed.append(key_parts)
        return compute()

    monkeypatch.setattr(module, "cached_market_intelligence_payload", capture)

    response = await client.get(
        "/api/v1/market-intelligence/sectors/history",
        params={"metric_version": f" {METRIC_VERSION} "},
    )

    assert response.status_code == 200
    assert response.json()["metric_version"] == METRIC_VERSION
    assert len(response.json()["items"]) == 1
    assert observed[0].metric_version == METRIC_VERSION
    assert observed[0].stable_pointer_revision is not None
    assert observed[0].params["metric_version"] == METRIC_VERSION


@pytest.mark.asyncio
async def test_pointer_revision_recheck_rejects_aba_repoint(client, monkeypatch) -> None:
    from app.api.v1 import market_intelligence as module

    with SqlUnitOfWork(SessionLocal) as uow:
        run_id = _persist_published(uow, MONDAY, "8" * 64)
        uow.commit()
    initial = module._StablePublishedIdentity(
        run_id=run_id,
        trading_date=MONDAY,
        metric_version=METRIC_VERSION,
        pointer_revision=datetime(2026, 5, 12, 21, 0, tzinfo=timezone.utc),
    )
    repointed_back = replace(
        initial,
        pointer_revision=datetime(2026, 5, 12, 21, 1, tzinfo=timezone.utc),
    )
    identities = iter((initial, repointed_back))
    stability_results = []

    monkeypatch.setattr(
        module,
        "_sector_published_identity",
        lambda _db: next(identities),
    )

    def capture(_key_parts, compute, **kwargs):
        value = compute()
        stability_results.append(kwargs["is_still_stable"]())
        return value

    monkeypatch.setattr(module, "cached_market_intelligence_payload", capture)

    response = await client.get("/api/v1/market-intelligence/sectors/latest")

    assert response.status_code == 200
    assert stability_results == [False]


@pytest.mark.asyncio
async def test_latest_compute_stays_pinned_during_same_timestamp_aba_swap(
    client,
    monkeypatch,
) -> None:
    from app.api.v1 import market_intelligence as module
    from app.infra.db.models.feature_store import FeatureRunPointer

    with SqlUnitOfWork(SessionLocal) as uow:
        run_a_id = _persist_published(uow, MONDAY, "9" * 64)
        run_d_id = _persist_published(uow, TUESDAY, "a" * 64)
        uow.commit()
    revision_a = NOW
    revision_d = revision_a
    revision_a2 = revision_a
    with SessionLocal() as session:
        pointer = session.get(FeatureRunPointer, LATEST_POINTER_KEY)
        pointer.run_id = run_a_id
        pointer.updated_at = revision_a
        session.commit()
    stability_results = []

    def coordinated_cache(key_parts, compute, **kwargs):
        assert key_parts.stable_run_id == run_a_id
        with SessionLocal() as session:
            pointer = session.get(FeatureRunPointer, LATEST_POINTER_KEY)
            pointer.run_id = run_d_id
            pointer.updated_at = revision_d
            session.commit()
        value = compute()
        with SessionLocal() as session:
            pointer = session.get(FeatureRunPointer, LATEST_POINTER_KEY)
            pointer.run_id = run_a_id
            pointer.updated_at = revision_a2
            session.commit()
        stability_results.append(kwargs["is_still_stable"]())
        return value

    monkeypatch.setattr(
        module,
        "cached_market_intelligence_payload",
        coordinated_cache,
    )

    response = await client.get("/api/v1/market-intelligence/sectors/latest")

    assert response.status_code == 200
    assert response.json()["run_id"] == run_a_id
    assert stability_results == [True]


def test_history_generation_advances_for_older_backfill_without_pointer_move() -> None:
    with SqlUnitOfWork(SessionLocal) as uow:
        latest_id = _persist_published(uow, TUESDAY, "b" * 64)
        uow.commit()
    with SessionLocal() as session:
        repository = SqlMarketIntelligenceRepository(session)
        before = repository.get_published_history_generation(METRIC_VERSION)

    with SqlUnitOfWork(SessionLocal) as uow:
        backfill_id = _persist_published(
            uow,
            MONDAY,
            "c" * 64,
            keep_latest_pointer=True,
        )
        uow.commit()

    with SessionLocal() as session:
        pointer = session.get(FeatureRunPointer, LATEST_POINTER_KEY)
        repository = SqlMarketIntelligenceRepository(session)
        after = repository.get_published_history_generation(METRIC_VERSION)

    assert pointer.run_id == latest_id
    assert before is not None and before.run_id == latest_id
    assert after is not None and after.run_id == backfill_id
    assert after != before


@pytest.mark.asyncio
async def test_history_compute_is_bounded_to_generation_during_older_backfill(
    client,
    monkeypatch,
) -> None:
    from app.api.v1 import market_intelligence as module

    with SqlUnitOfWork(SessionLocal) as uow:
        latest_id = _persist_published(uow, TUESDAY, "d" * 64)
        uow.commit()
    stability_results = []
    captured_generations = []

    def coordinated_cache(key_parts, compute, **kwargs):
        captured_generations.append(key_parts.published_generation)
        with SqlUnitOfWork(SessionLocal) as uow:
            _persist_published(
                uow,
                MONDAY,
                "e" * 64,
                keep_latest_pointer=True,
            )
            uow.commit()
        value = compute()
        stability_results.append(kwargs["is_still_stable"]())
        return value

    monkeypatch.setattr(
        module,
        "cached_market_intelligence_payload",
        coordinated_cache,
    )

    response = await client.get("/api/v1/market-intelligence/sectors/history")

    assert response.status_code == 200
    assert [item["run_id"] for item in response.json()["items"]] == [latest_id]
    assert len(captured_generations) == 1
    assert captured_generations[0].endswith(f"#{latest_id}")
    assert stability_results == [False]
    with SessionLocal() as session:
        pointer = session.get(FeatureRunPointer, LATEST_POINTER_KEY)
        generation = SqlMarketIntelligenceRepository(
            session
        ).get_published_history_generation(METRIC_VERSION)
    assert pointer.run_id == latest_id
    assert generation is not None and generation.run_id > latest_id


@pytest.mark.asyncio
async def test_empty_history_generation_stays_empty_during_first_publication(
    client,
    monkeypatch,
) -> None:
    from app.api.v1 import market_intelligence as module

    future_metric_version = "market_intelligence_v2"
    with SqlUnitOfWork(SessionLocal) as uow:
        _persist_published(uow, TUESDAY, "1" * 64)
        uow.commit()
    stability_results = []

    def coordinated_cache(key_parts, compute, **kwargs):
        assert key_parts.published_generation is None
        with SqlUnitOfWork(SessionLocal) as uow:
            _persist_published(
                uow,
                MONDAY,
                "2" * 64,
                metric_version=future_metric_version,
                keep_latest_pointer=True,
            )
            uow.commit()
        value = compute()
        stability_results.append(kwargs["is_still_stable"]())
        return value

    monkeypatch.setattr(
        module,
        "cached_market_intelligence_payload",
        coordinated_cache,
    )

    response = await client.get(
        "/api/v1/market-intelligence/sectors/history",
        params={"metric_version": future_metric_version},
    )

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert stability_results == [False]


@pytest.mark.asyncio
async def test_history_generation_is_stable_during_same_timestamp_aba_swap(
    client,
    monkeypatch,
) -> None:
    from app.api.v1 import market_intelligence as module

    with SqlUnitOfWork(SessionLocal) as uow:
        run_a_id = _persist_published(uow, MONDAY, "f" * 64)
        run_d_id = _persist_published(uow, TUESDAY, "0" * 64)
        uow.commit()
    revision_a = NOW
    revision_d = revision_a
    revision_a2 = revision_a
    with SessionLocal() as session:
        pointer = session.get(FeatureRunPointer, LATEST_POINTER_KEY)
        pointer.run_id = run_a_id
        pointer.updated_at = revision_a
        session.commit()
    stability_results = []

    def coordinated_cache(key_parts, compute, **kwargs):
        assert key_parts.stable_run_id == run_a_id
        assert key_parts.stable_pointer_revision.replace(tzinfo=None) == revision_a.replace(
            tzinfo=None
        )
        assert key_parts.published_generation.endswith(f"#{run_d_id}")
        with SessionLocal() as session:
            pointer = session.get(FeatureRunPointer, LATEST_POINTER_KEY)
            pointer.run_id = run_d_id
            pointer.updated_at = revision_d
            session.commit()
        value = compute()
        with SessionLocal() as session:
            pointer = session.get(FeatureRunPointer, LATEST_POINTER_KEY)
            pointer.run_id = run_a_id
            pointer.updated_at = revision_a2
            session.commit()
        stability_results.append(kwargs["is_still_stable"]())
        return value

    monkeypatch.setattr(
        module,
        "cached_market_intelligence_payload",
        coordinated_cache,
    )

    response = await client.get("/api/v1/market-intelligence/sectors/history")

    assert response.status_code == 200
    assert [item["run_id"] for item in response.json()["items"]] == [
        run_d_id,
        run_a_id,
    ]
    assert stability_results == [True]
