"""Read-only Phase 1 sector intelligence and Data Health endpoints."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Literal, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.domain.market_intelligence.constants import (
    LATEST_POINTER_KEY,
    MARKET_INTELLIGENCE_UNIVERSE,
    METRIC_VERSION,
)
from app.domain.market_intelligence.freshness import (
    FRESHNESS_STALE_THRESHOLD_COMPLETED_SESSIONS,
    classify_completed_session_freshness,
    collect_completed_sessions,
)
from app.domain.market_intelligence.models import IngestionStatus
from app.domain.market_intelligence.mvp import MVP_METRIC_VERSION
from app.infra.db.models.feature_store import FeatureRun, FeatureRunPointer
from app.infra.db.models.market_intelligence import MarketIntelligenceRunAudit
from app.infra.db.repositories.market_intelligence_repo import (
    SqlMarketIntelligenceRepository,
)
from app.schemas.market_intelligence import (
    EtfRadarResponse,
    MarketIntelligenceHealthResponse,
    MarketIntelligenceHealthRunResponse,
    MarketIntelligenceOverviewResponse,
    MarketMoversResponse,
    SectorIntelligenceHistoryItemResponse,
    SectorIntelligenceHistoryResponse,
    SectorIntelligenceLatestResponse,
)
from app.services.market_intelligence_read_service import (
    DEFAULT_MIN_PRICE,
    MarketIntelligenceReadService,
    US_PUBLISHED_POINTER,
)
from app.services.market_intelligence_read_cache import (
    MarketIntelligenceCacheKeyParts,
    cached_market_intelligence_payload,
)
from app.services.market_calendar_service import MarketCalendarService

router = APIRouter()
ResponseModel = TypeVar("ResponseModel")


@dataclass(frozen=True)
class _StablePublishedIdentity:
    run_id: int
    trading_date: date
    metric_version: str
    pointer_revision: datetime | None


def _mvp_published_identity(db: Session) -> _StablePublishedIdentity | None:
    row = (
        db.query(
            FeatureRun.id,
            FeatureRun.as_of_date,
            FeatureRunPointer.updated_at,
        )
        .join(FeatureRunPointer, FeatureRunPointer.run_id == FeatureRun.id)
        .filter(
            FeatureRunPointer.key == US_PUBLISHED_POINTER,
            FeatureRun.status == "published",
            FeatureRun.published_at.isnot(None),
        )
        .one_or_none()
    )
    if row is None:
        return None
    return _StablePublishedIdentity(
        run_id=int(row.id),
        trading_date=row.as_of_date,
        metric_version=MVP_METRIC_VERSION,
        pointer_revision=row.updated_at,
    )


def _sector_published_identity(db: Session) -> _StablePublishedIdentity | None:
    row = (
        db.query(
            FeatureRun.id,
            FeatureRun.as_of_date,
            MarketIntelligenceRunAudit.metric_version,
            FeatureRunPointer.updated_at,
        )
        .join(FeatureRunPointer, FeatureRunPointer.run_id == FeatureRun.id)
        .join(
            MarketIntelligenceRunAudit,
            MarketIntelligenceRunAudit.run_id == FeatureRun.id,
        )
        .filter(
            FeatureRunPointer.key == LATEST_POINTER_KEY,
            FeatureRun.status == "published",
            FeatureRun.published_at.isnot(None),
            MarketIntelligenceRunAudit.ingestion_status
            == IngestionStatus.SUCCEEDED.value,
        )
        .one_or_none()
    )
    if row is None:
        return None
    return _StablePublishedIdentity(
        run_id=int(row.id),
        trading_date=row.as_of_date,
        metric_version=row.metric_version,
        pointer_revision=row.updated_at,
    )


def _cache_key_parts(
    identity: _StablePublishedIdentity | None,
    *,
    endpoint: str,
    params: Mapping[str, Any] | None = None,
) -> MarketIntelligenceCacheKeyParts | None:
    if identity is None:
        return None
    return MarketIntelligenceCacheKeyParts(
        endpoint=endpoint,
        stable_run_id=identity.run_id,
        stable_trading_date=identity.trading_date,
        metric_version=identity.metric_version,
        params=params or {},
    )


def _cached_response(
    response_type: type[ResponseModel],
    *,
    key_parts: MarketIntelligenceCacheKeyParts | None,
    compute: Callable[[], ResponseModel],
    is_still_stable: Callable[[], bool] | None = None,
) -> ResponseModel:
    payload = cached_market_intelligence_payload(
        key_parts,
        lambda: compute().model_dump(mode="json"),
        is_still_stable=is_still_stable,
        validate_cached=lambda value: response_type.model_validate(value).model_dump(
            mode="json"
        ),
    )
    return response_type.model_validate(payload)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _completed_us_sessions() -> tuple[date, ...]:
    calendar = MarketCalendarService()
    latest = calendar.last_completed_trading_day("US")
    return collect_completed_sessions(
        latest,
        lambda start, end: calendar.trading_days("US", start, end),
    )


def _refresh_mvp_freshness(response: ResponseModel) -> ResponseModel:
    completed_sessions = _completed_us_sessions()
    expected_session = completed_sessions[-1] if completed_sessions else None
    return response.model_copy(
        update={
            "expected_session": expected_session,
            "freshness_status": classify_completed_session_freshness(
                response.as_of,
                completed_sessions,
            ),
        }
    )


def _age_seconds(value: datetime | None, *, now: datetime) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0.0, (now - value.astimezone(timezone.utc)).total_seconds())


@router.get(
    "/overview",
    response_model=MarketIntelligenceOverviewResponse,
)
def market_intelligence_overview(
    db: Session = Depends(get_db),
) -> MarketIntelligenceOverviewResponse:
    identity = _mvp_published_identity(db)
    return _refresh_mvp_freshness(
        _cached_response(
            MarketIntelligenceOverviewResponse,
            key_parts=_cache_key_parts(identity, endpoint="overview"),
            compute=lambda: MarketIntelligenceOverviewResponse.from_domain(
                MarketIntelligenceReadService(db).get_overview()
            ),
            is_still_stable=(
                None
                if identity is None
                else lambda: _mvp_published_identity(db) == identity
            ),
        ),
    )


@router.get(
    "/movers",
    response_model=MarketMoversResponse,
)
def market_intelligence_movers(
    limit: int = Query(default=20, ge=1, le=100),
    sector: str | None = Query(default=None, min_length=1, max_length=100),
    direction: Literal["all", "gainers", "losers"] = "all",
    min_price: float = Query(default=DEFAULT_MIN_PRICE, ge=0),
    min_rvol: float | None = Query(default=None, ge=0),
    market_cap_group: Literal["mega", "large", "mid", "small"] | None = None,
    search: str | None = Query(default=None, min_length=1, max_length=64),
    db: Session = Depends(get_db),
) -> MarketMoversResponse:
    identity = _mvp_published_identity(db)
    normalized_sector = sector.strip().casefold() if sector is not None else None
    normalized_search = search.strip().casefold() if search is not None else None
    params = {
        "limit": limit,
        "sector": normalized_sector,
        "direction": direction,
        "min_price": min_price,
        "min_rvol": min_rvol,
        "market_cap_group": market_cap_group,
        "search": normalized_search,
    }
    return _refresh_mvp_freshness(
        _cached_response(
            MarketMoversResponse,
            key_parts=_cache_key_parts(identity, endpoint="movers", params=params),
            compute=lambda: MarketMoversResponse.from_domain(
                MarketIntelligenceReadService(db).get_movers(
                    limit=limit,
                    sector=normalized_sector,
                    direction=direction,
                    min_price=min_price,
                    min_rvol=min_rvol,
                    market_cap_group=market_cap_group,
                    search=normalized_search,
                )
            ),
            is_still_stable=(
                None
                if identity is None
                else lambda: _mvp_published_identity(db) == identity
            )
        ),
    )


@router.get(
    "/etfs",
    response_model=EtfRadarResponse,
)
def market_intelligence_etfs(
    category: Literal[
        "all",
        "broad_market",
        "sector",
        "semiconductor",
        "software",
        "biotech",
        "defense",
        "energy",
        "metals",
        "uranium",
    ] = "all",
    db: Session = Depends(get_db),
) -> EtfRadarResponse:
    identity = _mvp_published_identity(db)
    return _refresh_mvp_freshness(
        _cached_response(
            EtfRadarResponse,
            key_parts=_cache_key_parts(
                identity,
                endpoint="etfs",
                params={"category": category},
            ),
            compute=lambda: EtfRadarResponse.from_domain(
                MarketIntelligenceReadService(db).get_etf_radar(category=category)
            ),
            is_still_stable=(
                None
                if identity is None
                else lambda: _mvp_published_identity(db) == identity
            ),
        ),
    )


@router.get(
    "/sectors/latest",
    response_model=SectorIntelligenceLatestResponse,
)
def latest_sector_intelligence(
    db: Session = Depends(get_db),
) -> SectorIntelligenceLatestResponse:
    identity = _sector_published_identity(db)

    def compute() -> SectorIntelligenceLatestResponse:
        bundle = SqlMarketIntelligenceRepository(db).get_latest_published(
            LATEST_POINTER_KEY
        )
        if bundle is None:
            raise HTTPException(
                status_code=404,
                detail="No complete sector intelligence snapshot has been published",
            )
        return SectorIntelligenceLatestResponse.from_bundle(bundle)

    return _cached_response(
        SectorIntelligenceLatestResponse,
        key_parts=_cache_key_parts(identity, endpoint="sectors-latest"),
        compute=compute,
        is_still_stable=(
            None
            if identity is None
            else lambda: _sector_published_identity(db) == identity
        ),
    )


@router.get(
    "/sectors/history",
    response_model=SectorIntelligenceHistoryResponse,
)
def sector_intelligence_history(
    date_from: date | None = None,
    date_to: date | None = None,
    symbol: str | None = Query(default=None, min_length=1, max_length=8),
    metric_version: str = Query(
        default=METRIC_VERSION,
        min_length=1,
        max_length=64,
    ),
    limit: int = Query(default=60, ge=1, le=252),
    db: Session = Depends(get_db),
) -> SectorIntelligenceHistoryResponse:
    normalized_symbol = symbol.strip().upper() if symbol is not None else None
    normalized_metric_version = metric_version.strip()
    if (
        normalized_symbol is not None
        and normalized_symbol not in MARKET_INTELLIGENCE_UNIVERSE
    ):
        raise HTTPException(
            status_code=422,
            detail="symbol is outside the fixed Phase 1 universe",
        )
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="date_from must be on or before date_to",
        )
    if not normalized_metric_version:
        raise HTTPException(
            status_code=422,
            detail="metric_version must not be blank",
        )
    identity = _sector_published_identity(db)

    def compute() -> SectorIntelligenceHistoryResponse:
        bundles = SqlMarketIntelligenceRepository(db).list_published_history(
            metric_version=normalized_metric_version,
            symbol=normalized_symbol,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )
        return SectorIntelligenceHistoryResponse(
            metric_version=normalized_metric_version,
            symbol=normalized_symbol,
            items=[
                SectorIntelligenceHistoryItemResponse.from_bundle(bundle)
                for bundle in bundles
            ],
        )

    return _cached_response(
        SectorIntelligenceHistoryResponse,
        key_parts=_cache_key_parts(
            identity,
            endpoint="sectors-history",
            params={
                "date_from": date_from,
                "date_to": date_to,
                "symbol": normalized_symbol,
                "metric_version": normalized_metric_version,
                "limit": limit,
            },
        ),
        compute=compute,
        is_still_stable=(
            None
            if identity is None
            else lambda: _sector_published_identity(db) == identity
        ),
    )


@router.get(
    "/sectors/health",
    response_model=MarketIntelligenceHealthResponse,
)
def sector_intelligence_health(
    db: Session = Depends(get_db),
) -> MarketIntelligenceHealthResponse:
    repository = SqlMarketIntelligenceRepository(db)
    aggregate = repository.get_health_aggregate(LATEST_POINTER_KEY)
    latest_attempt = aggregate.latest_attempt
    latest_published = aggregate.latest_published
    last_success = aggregate.last_successful_attempt
    consecutive_failures = aggregate.consecutive_failures
    completed_sessions = _completed_us_sessions()
    now = _utc_now()
    published_response = (
        MarketIntelligenceHealthRunResponse.from_bundle(latest_published)
        if latest_published is not None
        else None
    )
    last_success_response = (
        MarketIntelligenceHealthRunResponse.from_bundle(last_success)
        if last_success is not None
        else None
    )
    latest_attempt_response = (
        MarketIntelligenceHealthRunResponse.from_bundle(latest_attempt)
        if latest_attempt is not None
        else None
    )
    publication_occurred = (
        latest_attempt is not None
        and latest_published is not None
        and latest_attempt.run_id == latest_published.run_id
    )
    if latest_published is None:
        actual_publication_status = "UNAVAILABLE"
    elif publication_occurred:
        actual_publication_status = "PUBLISHED"
    else:
        actual_publication_status = "SERVING_PREVIOUS"
    return MarketIntelligenceHealthResponse(
        universe_expected=len(MARKET_INTELLIGENCE_UNIVERSE),
        current_run_timestamp=(
            latest_attempt.audit.ingestion_timestamp
            if latest_attempt is not None
            else None
        ),
        latest_attempt=latest_attempt_response,
        latest_published=published_response,
        last_successful_run=last_success_response,
        last_complete_published_snapshot=(
            latest_published.as_of_date if latest_published is not None else None
        ),
        publication_occurred=publication_occurred,
        publication_status=actual_publication_status,
        freshness_status=classify_completed_session_freshness(
            latest_published.as_of_date if latest_published is not None else None,
            completed_sessions,
        ),
        last_attempt_age_seconds=_age_seconds(
            latest_attempt.audit.ingestion_timestamp
            if latest_attempt is not None
            else None,
            now=now,
        ),
        last_success_age_seconds=_age_seconds(
            last_success.audit.ingestion_timestamp
            if last_success is not None
            else None,
            now=now,
        ),
        provider_latency_ms=(
            latest_attempt_response.provider_latency_ms
            if latest_attempt_response is not None
            else None
        ),
        failure_category=(
            latest_attempt_response.failure_category
            if latest_attempt_response is not None
            else None
        ),
        consecutive_failures=consecutive_failures,
        last_successful_trading_date=(
            last_success.as_of_date if last_success is not None else None
        ),
        stale_threshold_completed_sessions=(
            FRESHNESS_STALE_THRESHOLD_COMPLETED_SESSIONS
        ),
        pipeline_version=(
            latest_attempt_response.pipeline_version
            if latest_attempt_response is not None
            else None
        ),
    )
