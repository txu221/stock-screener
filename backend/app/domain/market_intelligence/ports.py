"""Persistence port for the Phase 1 Market Intelligence bounded context."""

from __future__ import annotations

import abc
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from .models import (
    BarRejection,
    CanonicalBar,
    MarketIntelligenceHealthAggregate,
    MarketIntelligenceRunBundle,
    RunAudit,
    SectorSnapshot,
)
from .observability import MarketIntelligenceErrorCategory


class MarketIntelligenceIdempotencyConflict(RuntimeError):
    """A concurrent run already persisted the same deterministic input."""


@dataclass(frozen=True)
class PublishedHistoryGeneration:
    """Immutable boundary identifying the newest member of published history."""

    published_at: datetime
    run_id: int


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
    def update_observability(
        self,
        run_id: int,
        *,
        stage_timings: dict[str, float],
        failure_category: MarketIntelligenceErrorCategory | None,
        publication_status: str,
        retry_status: str,
        reuse_status: str,
    ) -> None:
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
    def get_last_successful_attempt(self) -> MarketIntelligenceRunBundle | None:
        ...

    @abc.abstractmethod
    def count_consecutive_failures(self) -> int:
        ...

    @abc.abstractmethod
    def get_health_aggregate(
        self,
        pointer_key: str,
    ) -> MarketIntelligenceHealthAggregate:
        ...

    @abc.abstractmethod
    def get_latest_published(
        self,
        pointer_key: str,
    ) -> MarketIntelligenceRunBundle | None:
        ...

    @abc.abstractmethod
    def get_published_by_run_id(
        self,
        run_id: int,
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
        max_generation: PublishedHistoryGeneration | None = None,
    ) -> tuple[MarketIntelligenceRunBundle, ...]:
        ...

    @abc.abstractmethod
    def get_published_history_generation(
        self,
        metric_version: str,
    ) -> PublishedHistoryGeneration | None:
        ...
