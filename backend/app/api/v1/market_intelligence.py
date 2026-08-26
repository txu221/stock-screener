"""Read-only Phase 1 sector intelligence and Data Health endpoints."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.domain.market_intelligence.constants import (
    LATEST_POINTER_KEY,
    MARKET_INTELLIGENCE_UNIVERSE,
    METRIC_VERSION,
)
from app.infra.db.repositories.market_intelligence_repo import (
    SqlMarketIntelligenceRepository,
)
from app.schemas.market_intelligence import (
    MarketIntelligenceHealthResponse,
    MarketIntelligenceHealthRunResponse,
    SectorIntelligenceHistoryItemResponse,
    SectorIntelligenceHistoryResponse,
    SectorIntelligenceLatestResponse,
)

router = APIRouter()


@router.get(
    "/sectors/latest",
    response_model=SectorIntelligenceLatestResponse,
)
def latest_sector_intelligence(
    db: Session = Depends(get_db),
) -> SectorIntelligenceLatestResponse:
    bundle = SqlMarketIntelligenceRepository(db).get_latest_published(
        LATEST_POINTER_KEY
    )
    if bundle is None:
        raise HTTPException(
            status_code=404,
            detail="No complete sector intelligence snapshot has been published",
        )
    return SectorIntelligenceLatestResponse.from_bundle(bundle)


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
    bundles = SqlMarketIntelligenceRepository(db).list_published_history(
        metric_version=metric_version,
        symbol=normalized_symbol,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    return SectorIntelligenceHistoryResponse(
        metric_version=metric_version,
        symbol=normalized_symbol,
        items=[
            SectorIntelligenceHistoryItemResponse.from_bundle(bundle)
            for bundle in bundles
        ],
    )


@router.get(
    "/sectors/health",
    response_model=MarketIntelligenceHealthResponse,
)
def sector_intelligence_health(
    db: Session = Depends(get_db),
) -> MarketIntelligenceHealthResponse:
    repository = SqlMarketIntelligenceRepository(db)
    latest_attempt = repository.get_latest_attempt()
    latest_published = repository.get_latest_published(LATEST_POINTER_KEY)
    published_response = (
        MarketIntelligenceHealthRunResponse.from_bundle(latest_published)
        if latest_published is not None
        else None
    )
    return MarketIntelligenceHealthResponse(
        universe_expected=len(MARKET_INTELLIGENCE_UNIVERSE),
        current_run_timestamp=(
            latest_attempt.audit.ingestion_timestamp
            if latest_attempt is not None
            else None
        ),
        latest_attempt=(
            MarketIntelligenceHealthRunResponse.from_bundle(latest_attempt)
            if latest_attempt is not None
            else None
        ),
        latest_published=published_response,
        last_successful_run=published_response,
        last_complete_published_snapshot=(
            latest_published.as_of_date if latest_published is not None else None
        ),
        publication_occurred=(
            latest_attempt is not None
            and latest_published is not None
            and latest_attempt.run_id == latest_published.run_id
        ),
    )
