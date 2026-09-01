"""Celery entrypoint for the US sector-intelligence daily slice."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from datetime import date

from celery.signals import before_task_publish

from app.celery_app import celery_app
from app.domain.market_intelligence.constants import (
    MARKET_INTELLIGENCE_UNIVERSE,
    METRIC_VERSION,
    NORMALIZATION_VERSION,
)
from app.domain.market_intelligence.observability import (
    PIPELINE_VERSION,
    MarketIntelligenceErrorCategory,
    elapsed_milliseconds,
    failure_category_for_exception,
)
from app.tasks.market_queues import market_jobs_queue_for_market, normalize_market
from app.use_cases.market_intelligence.build_sector_snapshot import (
    BuildSectorSnapshotCommand,
)
from app.wiring.bootstrap import (
    get_market_calendar_service,
    get_market_intelligence_runner,
)


logger = logging.getLogger(__name__)
TASK_NAME = (
    "app.tasks.market_intelligence_tasks."
    "calculate_sector_intelligence_snapshot"
)


def _task_log_extra(
    *,
    event: str,
    task_id: str | None,
    as_of_date: str | None,
    stage: str,
    run_id: int | None = None,
    duration_ms: float | None = None,
    symbol_count: int | None = None,
    snapshot_count: int | None = None,
    publication_status: str | None = None,
    retry_status: str = "INITIAL",
    reuse_status: str = "NEW",
    failure_category: str | None = None,
    expected_symbols: int | None = len(MARKET_INTELLIGENCE_UNIVERSE),
    received_symbols: int | None = None,
    valid_symbols: int | None = None,
    rejected_symbols: int | None = None,
) -> dict:
    return {
        "event": event,
        "task_id": task_id,
        "run_id": run_id,
        "as_of_date": as_of_date,
        "pipeline_version": PIPELINE_VERSION,
        "metric_version": METRIC_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "provider": "yahoo",
        "stage": stage,
        "duration_ms": duration_ms,
        "symbol_count": symbol_count,
        "snapshot_count": snapshot_count,
        "publication_status": publication_status,
        "retry_status": retry_status,
        "reuse_status": reuse_status,
        "failure_category": failure_category,
        "expected_symbols": expected_symbols,
        "received_symbols": received_symbols,
        "valid_symbols": valid_symbols,
        "rejected_symbols": rejected_symbols,
    }


def _result_coverage(result) -> dict[str, int | None]:
    return {
        "expected_symbols": getattr(
            result,
            "expected_symbols",
            len(MARKET_INTELLIGENCE_UNIVERSE),
        ),
        "received_symbols": getattr(result, "received_symbols", None),
        "valid_symbols": getattr(result, "valid_symbols", None),
        "rejected_symbols": getattr(result, "rejected_symbols", None),
    }


def _published_body_arguments(body) -> tuple[tuple, dict]:
    if isinstance(body, (tuple, list)) and len(body) >= 2:
        args = body[0] if isinstance(body[0], (tuple, list)) else ()
        kwargs = body[1] if isinstance(body[1], Mapping) else {}
        return tuple(args), dict(kwargs)
    return (), {}


@before_task_publish.connect(sender=TASK_NAME, weak=False)
def _log_sector_intelligence_dispatched(
    sender=None,
    body=None,
    headers=None,
    **kwargs,
) -> None:
    del sender, kwargs
    args, task_kwargs = _published_body_arguments(body)
    calculation_date = (
        args[0]
        if args
        else task_kwargs.get("calculation_date")
    )
    force_refresh = bool(task_kwargs.get("force_refresh", False))
    event = "market_intelligence_task_dispatched"
    logger.info(
        event,
        extra=_task_log_extra(
            event=event,
            task_id=(headers or {}).get("id"),
            as_of_date=calculation_date,
            stage="dispatch",
            retry_status="INITIAL",
            reuse_status="FORCE_REFRESH" if force_refresh else "NEW",
        ),
    )


def _request_context(task) -> tuple[str | None, int, bool]:
    request = getattr(task, "request", None)
    task_id = getattr(request, "id", None)
    retries = int(getattr(request, "retries", 0) or 0)
    delivery_info = getattr(request, "delivery_info", None) or {}
    redelivered = bool(delivery_info.get("redelivered", False))
    return task_id, retries, redelivered


@celery_app.task(
    bind=True,
    name=TASK_NAME,
    queue=market_jobs_queue_for_market("US"),
)
def calculate_sector_intelligence_snapshot(
    self,
    calculation_date: str | None = None,
    *,
    market: str = "US",
    activity_lifecycle: str | None = None,
    force_refresh: bool = False,
) -> dict:
    del activity_lifecycle
    started = time.monotonic()
    task_id, retries, redelivered = _request_context(self)
    retry_status = (
        "BROKER_REDELIVERED"
        if redelivered
        else "RETRY" if retries > 0 else "INITIAL"
    )
    reuse_status = "FORCE_REFRESH" if force_refresh else "NEW"
    as_of: date | None = None
    active_stage = "validation"
    event = "market_intelligence_task_started"
    logger.info(
        event,
        extra=_task_log_extra(
            event=event,
            task_id=task_id,
            as_of_date=calculation_date,
            stage="started",
            retry_status=retry_status,
            reuse_status=reuse_status,
        ),
    )
    try:
        if normalize_market(market) != "US":
            raise ValueError("sector intelligence Phase 1 is US-only")
        as_of = (
            date.fromisoformat(calculation_date)
            if calculation_date is not None
            else get_market_calendar_service().last_completed_trading_day("US")
        )
        if force_refresh:
            event = "market_intelligence_task_force_refresh"
            logger.info(
                event,
                extra=_task_log_extra(
                    event=event,
                    task_id=task_id,
                    as_of_date=as_of.isoformat(),
                    stage="force_refresh",
                    retry_status=retry_status,
                    reuse_status=reuse_status,
                ),
            )
        if redelivered:
            event = "market_intelligence_task_broker_redelivery"
            logger.info(
                event,
                extra=_task_log_extra(
                    event=event,
                    task_id=task_id,
                    as_of_date=as_of.isoformat(),
                    stage="broker_redelivery",
                    retry_status=retry_status,
                    reuse_status=reuse_status,
                ),
            )

        active_stage = "pipeline"
        result = get_market_intelligence_runner().execute(
            BuildSectorSnapshotCommand(
                as_of=as_of,
                reuse_published=not force_refresh,
                force_refresh=force_refresh,
                retry_count=retries,
                broker_redelivered=redelivered,
            )
        )
        result_reused = bool(getattr(result, "reused", False))
        if result_reused and result.published:
            reuse_status = "PUBLISHED_RESULT_REUSED"
            event = "market_intelligence_task_published_result_reused"
            logger.info(
                event,
                extra=_task_log_extra(
                    event=event,
                    task_id=task_id,
                    run_id=result.run_id,
                    as_of_date=as_of.isoformat(),
                    stage="reuse",
                    publication_status="PUBLISHED",
                    retry_status=retry_status,
                    reuse_status=reuse_status,
                    **_result_coverage(result),
                ),
            )

        duration_ms = elapsed_milliseconds(started, time.monotonic())
        publication_status = "PUBLISHED" if result.published else "NOT_PUBLISHED"
        payload = {
            "status": result.ingestion_status.value,
            "run_id": result.run_id,
            "published": result.published,
            "as_of": as_of.isoformat(),
            "metric_version": METRIC_VERSION,
            "idempotency_key": result.idempotency_key,
        }
        if result.ingestion_status.value == "FAILED":
            category = getattr(result, "failure_category", None)
            category_value = getattr(category, "value", category)
            event = "market_intelligence_task_failed"
            logger.error(
                event,
                extra=_task_log_extra(
                    event=event,
                    task_id=task_id,
                    run_id=result.run_id,
                    as_of_date=as_of.isoformat(),
                    stage="pipeline",
                    duration_ms=duration_ms,
                    publication_status="FAILED",
                    retry_status=retry_status,
                    reuse_status=reuse_status,
                    failure_category=category_value,
                    **_result_coverage(result),
                ),
            )
            return payload
        event = "market_intelligence_task_completed"
        logger.info(
            event,
            extra=_task_log_extra(
                event=event,
                task_id=task_id,
                run_id=result.run_id,
                as_of_date=as_of.isoformat(),
                stage="completed",
                duration_ms=duration_ms,
                publication_status=publication_status,
                retry_status=retry_status,
                reuse_status=reuse_status,
                **_result_coverage(result),
            ),
        )
        return payload
    except Exception as exc:
        failure_stage = getattr(exc, "market_intelligence_stage", active_stage)
        category = getattr(
            exc,
            "market_intelligence_failure_category",
            failure_category_for_exception(exc, stage=active_stage),
        )
        category_value = getattr(category, "value", category)
        event = "market_intelligence_task_failed"
        logger.error(
            event,
            extra=_task_log_extra(
                event=event,
                task_id=task_id,
                as_of_date=(
                    as_of.isoformat()
                    if as_of is not None
                    else calculation_date
                ),
                stage=failure_stage,
                duration_ms=elapsed_milliseconds(started, time.monotonic()),
                publication_status="FAILED",
                retry_status=retry_status,
                reuse_status=reuse_status,
                failure_category=category_value,
            ),
            exc_info=True,
        )
        raise
