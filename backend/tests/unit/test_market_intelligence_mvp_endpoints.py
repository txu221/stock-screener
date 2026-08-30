from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.market_intelligence import router
from app.database import Base, get_db
from app.infra.db.models.feature_store import (
    FeatureRun,
    FeatureRunPointer,
    StockFeatureDaily,
)
from app.models.stock import StockPrice
from app.models.stock_universe import StockUniverse


AS_OF = date(2026, 8, 26)
PUBLISHED_AT = datetime(2026, 8, 26, 22, 5, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def mvp_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            FeatureRun.__table__,
            StockFeatureDaily.__table__,
            FeatureRunPointer.__table__,
            StockUniverse.__table__,
            StockPrice.__table__,
        ],
    )
    factory = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/market-intelligence")

    def override_get_db():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, factory
    app.dependency_overrides.clear()
    engine.dispose()


def _prices(symbol: str, closes: list[float], *, current_volume: int = 2_000_000):
    start = AS_OF - timedelta(days=len(closes) - 1)
    for index, close in enumerate(closes):
        yield StockPrice(
            symbol=symbol,
            date=start + timedelta(days=index),
            open=close,
            high=close,
            low=close,
            close=close,
            adj_close=close,
            volume=current_volume if index == len(closes) - 1 else 1_000_000,
        )


def _seed_pulse_and_etfs(factory):
    with factory() as session:
        run = FeatureRun(
            as_of_date=AS_OF,
            run_type="daily_snapshot",
            status="published",
            published_at=PUBLISHED_AT,
        )
        session.add(run)
        session.flush()
        session.add(
            FeatureRunPointer(
                key="latest_published_market:US",
                run_id=run.id,
            )
        )
        spy = [80.0] * 61
        spy[-2:] = [100.0, 105.0]
        qqq = [70.0] * 61
        qqq[-2:] = [100.0, 110.0]
        session.add_all(_prices("SPY", spy))
        session.add_all(_prices("QQQ", qqq, current_volume=3_000_000))
        session.commit()


def _seed_mover(factory):
    with factory() as session:
        run = FeatureRun(
            as_of_date=AS_OF,
            run_type="daily_snapshot",
            status="published",
            published_at=PUBLISHED_AT,
        )
        session.add(run)
        session.flush()
        session.add_all(
            [
                FeatureRunPointer(
                    key="latest_published_market:US",
                    run_id=run.id,
                ),
                StockFeatureDaily(
                    run_id=run.id,
                    symbol="AAPL",
                    as_of_date=AS_OF,
                    details_json={"avg_dollar_volume": 200_000_000},
                ),
                StockUniverse(
                    symbol="AAPL",
                    name="Apple Inc.",
                    market="US",
                    sector="Technology",
                    industry="Consumer Electronics",
                    market_cap=3_000_000_000_000,
                    is_sp500=True,
                    is_active=True,
                    status="active",
                ),
            ]
        )
        session.add_all(
            _prices("AAPL", [100.0] * 20 + [110.0], current_volume=4_000_000)
        )
        session.commit()
        return run.id


@pytest.mark.asyncio
async def test_overview_contract_exposes_fixed_pulse_and_freshness(mvp_client):
    client, factory = mvp_client
    _seed_pulse_and_etfs(factory)

    response = await client.get("/api/v1/market-intelligence/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["as_of"] == AS_OF.isoformat()
    assert body["last_updated"] is not None
    assert body["provider"] == "existing_stock_prices"
    assert body["metric_version"] == "market_intelligence_mvp_v1"
    assert body["price_basis"] == "cached_adjusted_close"
    assert body["price_history_quality"] == "not_corporate_action_reconciled"
    assert body["freshness_status"] in {"FRESH", "STALE"}
    assert body["expected_session"] is not None
    assert body["market_status"] is None
    assert [item["symbol"] for item in body["pulse"]] == ["SPY", "QQQ", "DIA", "IWM"]
    assert body["pulse"][0]["return_1d"] == pytest.approx(0.05)
    assert body["missing_symbols"] == ["DIA", "IWM"]


@pytest.mark.asyncio
async def test_movers_contract_exposes_backend_ordering_filters_and_sector_counts(mvp_client):
    client, factory = mvp_client
    _seed_mover(factory)

    response = await client.get(
        "/api/v1/market-intelligence/movers",
        params={"direction": "gainers", "sector": "Technology", "min_rvol": 2},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["as_of"] == AS_OF.isoformat()
    assert body["published_at"] is not None
    assert body["price_basis"] == "cached_adjusted_close"
    assert body["price_history_quality"] == "not_corporate_action_reconciled"
    assert body["freshness_status"] in {"FRESH", "STALE"}
    assert body["eligible_count"] == 1
    assert [item["symbol"] for item in body["gainers"]] == ["AAPL"]
    assert body["gainers"][0]["change_1d"] == pytest.approx(0.10)
    assert body["gainers"][0]["rvol20"] == pytest.approx(4.0)
    assert body["losers"] == []
    assert body["sectors"] == [
        {
            "sector": "Technology",
            "advancers": 1,
            "decliners": 0,
            "unchanged": 0,
            "total": 1,
        }
    ]


@pytest.mark.asyncio
async def test_etf_contract_exposes_category_ranks_and_score_explanation(mvp_client):
    client, factory = mvp_client
    _seed_pulse_and_etfs(factory)

    response = await client.get(
        "/api/v1/market-intelligence/etfs",
        params={"category": "broad_market"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["category"] == "broad_market"
    assert body["price_basis"] == "cached_adjusted_close"
    assert body["price_history_quality"] == "not_corporate_action_reconciled"
    assert body["freshness_status"] in {"FRESH", "STALE"}
    assert [item["symbol"] for item in body["items"]] == ["SPY", "QQQ", "IWM", "DIA"]
    assert body["score_definition"]["version"] == "etf_strength_v1"
    assert body["score_definition"]["weights"] == {
        "relative_strength_20d": 0.3,
        "relative_strength_60d": 0.25,
        "return_20d": 0.2,
        "volume_confirmation": 0.15,
        "drawdown_60d": 0.1,
    }
    assert body["score_definition"]["language"] == "descriptive_not_predictive"
    qqq = next(item for item in body["items"] if item["symbol"] == "QQQ")
    assert qqq["strength_score"] is not None
    assert qqq["category_ranks"]["broad_market"] == 1
    assert body["missing_symbols"] == ["IWM", "DIA"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/v1/market-intelligence/movers", {"limit": 0}),
        ("/api/v1/market-intelligence/movers", {"min_price": -1}),
        ("/api/v1/market-intelligence/movers", {"min_rvol": -1}),
        ("/api/v1/market-intelligence/movers", {"direction": "sideways"}),
        ("/api/v1/market-intelligence/etfs", {"category": "leveraged"}),
    ],
)
async def test_mvp_query_validation_is_explicit(mvp_client, path, params):
    client, _ = mvp_client

    response = await client.get(path, params=params)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_movers_cache_uses_stable_pointer_and_normalized_parameters(
    mvp_client,
    monkeypatch,
):
    from app.api.v1 import market_intelligence as module
    from app.services.market_intelligence_read_cache import (
        build_market_intelligence_cache_key,
    )

    client, factory = mvp_client
    run_id = _seed_mover(factory)
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

    first = await client.get(
        "/api/v1/market-intelligence/movers",
        params={"direction": "gainers", "sector": " Technology ", "limit": 20},
    )
    second = await client.get(
        "/api/v1/market-intelligence/movers",
        params={"limit": 20, "sector": "technology", "direction": "gainers"},
    )

    assert first.status_code == second.status_code == 200
    assert len(observed) == 2
    assert observed[0].stable_run_id == run_id
    assert observed[0].stable_trading_date == AS_OF
    assert observed[0].metric_version == "market_intelligence_mvp_v1"
    assert build_market_intelligence_cache_key(observed[0]) == (
        build_market_intelligence_cache_key(observed[1])
    )


@pytest.mark.asyncio
async def test_unpublished_mvp_read_bypasses_cache_storage(mvp_client, monkeypatch):
    from app.api.v1 import market_intelligence as module

    client, _ = mvp_client
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

    response = await client.get("/api/v1/market-intelligence/overview")

    assert response.status_code == 200
    assert response.json()["unavailable_reason"] == "no_published_us_feature_run"
    assert observed == [None]


@pytest.mark.asyncio
async def test_cached_mvp_read_refreshes_completed_session_freshness(
    mvp_client,
    monkeypatch,
):
    from app.api.v1 import market_intelligence as module

    client, factory = mvp_client
    _seed_pulse_and_etfs(factory)
    cached_payload = None
    completed_sessions = [AS_OF]

    def in_process_cache(_key_parts, compute, **_kwargs):
        nonlocal cached_payload
        if cached_payload is None:
            cached_payload = compute()
        return cached_payload

    monkeypatch.setattr(module, "cached_market_intelligence_payload", in_process_cache)
    monkeypatch.setattr(
        module,
        "_completed_us_sessions",
        lambda: tuple(completed_sessions),
    )

    first = await client.get("/api/v1/market-intelligence/overview")
    completed_sessions.append(AS_OF + timedelta(days=1))
    second = await client.get("/api/v1/market-intelligence/overview")

    assert first.json()["expected_session"] == AS_OF.isoformat()
    assert first.json()["freshness_status"] == "FRESH"
    assert second.json()["expected_session"] == (AS_OF + timedelta(days=1)).isoformat()
    assert second.json()["freshness_status"] == "AGING"
