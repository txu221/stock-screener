"""Persistence port for the Phase 1 Market Intelligence bounded context."""

from __future__ import annotations

import abc
from collections.abc import Sequence
from datetime import date

from .models import (
    BarRejection,
    CanonicalBar,
    MarketIntelligenceRunBundle,
    RunAudit,
    SectorSnapshot,
)


class MarketIntelligenceRepository(abc.ABC):
    @abc.abstractmethod
    def persist_candidate(
        self,
        run_id: int,
        audit: RunAudit,
        canonical_bars: Sequence[CanonicalBar],
        rejections: Sequence[BarRejection],
        snapshots: Sequence[SectorSnapshot],
    ) -> None:
        ...

    @abc.abstractmethod
    def find_exact(self, idempotency_key: str) -> MarketIntelligenceRunBundle | None:
        ...

    @abc.abstractmethod
    def get_previous_published(
        self,
        *,
        before: date,
        metric_version: str,
    ) -> MarketIntelligenceRunBundle | None:
        ...

    @abc.abstractmethod
    def get_latest_attempt(self) -> MarketIntelligenceRunBundle | None:
        ...

    @abc.abstractmethod
    def get_latest_published(
        self,
        pointer_key: str,
    ) -> MarketIntelligenceRunBundle | None:
        ...

    @abc.abstractmethod
    def list_published_history(
        self,
        *,
        metric_version: str,
        symbol: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 60,
    ) -> tuple[MarketIntelligenceRunBundle, ...]:
        ...
