"""Runtime composition for the Phase 1 Market Intelligence slice."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.infra.db.uow import SqlUnitOfWork
from app.infra.providers.market_intelligence_yahoo import (
    YahooMarketIntelligenceProvider,
)
from app.services.bulk_data_fetcher import BulkDataFetcher
from app.services.market_intelligence_session_source import CompletedSessionSource
from app.use_cases.market_intelligence.build_sector_snapshot import (
    BuildSectorSnapshotUseCase,
)


@dataclass(frozen=True)
class MarketIntelligenceServices:
    provider: YahooMarketIntelligenceProvider
    session_source: CompletedSessionSource
    runner: BuildSectorSnapshotUseCase


def build_market_intelligence_services(
    *,
    session_factory,
    market_calendar,
    bulk_fetcher=None,
) -> MarketIntelligenceServices:
    fetcher = bulk_fetcher or BulkDataFetcher()
    provider = YahooMarketIntelligenceProvider(
        fetcher,
        clock=lambda: datetime.now(timezone.utc),
    )
    session_source = CompletedSessionSource(market_calendar)
    runner = BuildSectorSnapshotUseCase(
        provider=provider,
        session_source=session_source,
        uow_factory=lambda: SqlUnitOfWork(session_factory),
        clock=lambda: datetime.now(timezone.utc),
    )
    return MarketIntelligenceServices(
        provider=provider,
        session_source=session_source,
        runner=runner,
    )
