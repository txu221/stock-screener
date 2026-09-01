"""Runtime and Celery task contracts for Phase 1 sector intelligence."""

from __future__ import annotations

from datetime import date
import logging
from types import SimpleNamespace

import pytest

from app.domain.market_intelligence.models import IngestionStatus
from app.domain.market_intelligence.observability import (
    MarketIntelligenceErrorCategory,
)


def test_task_executes_explicit_calculation_date(monkeypatch) -> None:
    from app.tasks import market_intelligence_tasks as module

    commands = []

    class _Runner:
        def execute(self, command):
            commands.append(command)
            return SimpleNamespace(
                run_id=41,
                ingestion_status=IngestionStatus.PARTIAL,
                published=False,
                idempotency_key="a" * 64,
            )

    monkeypatch.setattr(module, "get_market_intelligence_runner", lambda: _Runner())
    result = module.calculate_sector_intelligence_snapshot.run("2026-05-15")

    assert commands[0].as_of == date(2026, 5, 15)
    assert commands[0].reuse_published is True
    assert result == {
        "status": "PARTIAL",
        "run_id": 41,
        "published": False,
        "as_of": "2026-05-15",
        "metric_version": "market_intelligence_v1",
        "idempotency_key": "a" * 64,
    }


def test_task_resolves_last_completed_us_session_when_date_omitted(monkeypatch) -> None:
    from app.tasks import market_intelligence_tasks as module

    seen = []
    calendar = SimpleNamespace(
        last_completed_trading_day=lambda market: (
            seen.append(market) or date(2026, 5, 15)
        )
    )
    runner = SimpleNamespace(
        execute=lambda command: SimpleNamespace(
            run_id=42,
            ingestion_status=IngestionStatus.SUCCEEDED,
            published=True,
            idempotency_key="b" * 64,
        )
    )
    monkeypatch.setattr(module, "get_market_calendar_service", lambda: calendar)
    monkeypatch.setattr(module, "get_market_intelligence_runner", lambda: runner)

    result = module.calculate_sector_intelligence_snapshot.run()

    assert seen == ["US"]
    assert result["as_of"] == "2026-05-15"
    assert result["status"] == "SUCCEEDED"
    assert result["published"] is True


def test_task_force_refresh_allows_explicit_same_day_revision(monkeypatch) -> None:
    from app.tasks import market_intelligence_tasks as module

    commands = []
    runner = SimpleNamespace(
        execute=lambda command: (
            commands.append(command)
            or SimpleNamespace(
                run_id=43,
                ingestion_status=IngestionStatus.SUCCEEDED,
                published=True,
                idempotency_key="c" * 64,
            )
        )
    )
    monkeypatch.setattr(module, "get_market_intelligence_runner", lambda: runner)

    module.calculate_sector_intelligence_snapshot.run(
        "2026-05-15",
        force_refresh=True,
    )

    assert commands[0].reuse_published is False


def test_publish_signal_logs_dispatched_with_structured_context(caplog) -> None:
    from app.tasks import market_intelligence_tasks as module

    with caplog.at_level(logging.INFO):
        module._log_sector_intelligence_dispatched(
            sender=module.TASK_NAME,
            headers={"id": "task-dispatched-1"},
            body=(("2026-05-15",), {"market": "US", "force_refresh": True}, {}),
        )

    record = next(
        item
        for item in caplog.records
        if getattr(item, "event", None) == "market_intelligence_task_dispatched"
    )
    assert record.task_id == "task-dispatched-1"
    assert record.as_of_date == "2026-05-15"
    assert record.pipeline_version == "market_intelligence_pipeline_v2"
    assert record.metric_version == "market_intelligence_v1"
    assert record.provider == "yahoo"
    assert record.stage == "dispatch"
    assert record.publication_status is None
    assert record.retry_status == "INITIAL"
    assert record.reuse_status == "FORCE_REFRESH"


def test_task_logs_redelivered_published_reuse_and_completion(
    monkeypatch,
    caplog,
) -> None:
    from app.tasks import market_intelligence_tasks as module

    runner = SimpleNamespace(
        execute=lambda command: SimpleNamespace(
            run_id=51,
            ingestion_status=IngestionStatus.SUCCEEDED,
            published=True,
            idempotency_key="d" * 64,
            reused=True,
            expected_symbols=12,
            received_symbols=12,
            valid_symbols=12,
            rejected_symbols=0,
        )
    )
    monkeypatch.setattr(module, "get_market_intelligence_runner", lambda: runner)
    monkeypatch.setattr(module.calculate_sector_intelligence_snapshot.request, "id", "task-51")
    monkeypatch.setattr(module.calculate_sector_intelligence_snapshot.request, "retries", 1)
    monkeypatch.setattr(
        module.calculate_sector_intelligence_snapshot.request,
        "delivery_info",
        {"redelivered": True},
    )

    with caplog.at_level(logging.INFO):
        module.calculate_sector_intelligence_snapshot.run("2026-05-15")

    records = {
        record.event: record
        for record in caplog.records
        if getattr(record, "event", "").startswith("market_intelligence_task_")
    }
    assert {
        "market_intelligence_task_started",
        "market_intelligence_task_broker_redelivery",
        "market_intelligence_task_published_result_reused",
        "market_intelligence_task_completed",
    } <= set(records)
    completed = records["market_intelligence_task_completed"]
    assert completed.task_id == "task-51"
    assert completed.run_id == 51
    assert completed.as_of_date == "2026-05-15"
    assert completed.stage == "completed"
    assert completed.duration_ms >= 0
    assert completed.publication_status == "PUBLISHED"
    assert completed.retry_status == "BROKER_REDELIVERED"
    assert completed.reuse_status == "PUBLISHED_RESULT_REUSED"
    assert completed.normalization_version == "market_intelligence_adjusted_ohlcv_v2"
    assert completed.expected_symbols == 12
    assert completed.received_symbols == 12
    assert completed.valid_symbols == 12
    assert completed.rejected_symbols == 0


def test_task_logs_force_refresh_state(monkeypatch, caplog) -> None:
    from app.tasks import market_intelligence_tasks as module

    runner = SimpleNamespace(
        execute=lambda command: SimpleNamespace(
            run_id=52,
            ingestion_status=IngestionStatus.SUCCEEDED,
            published=True,
            idempotency_key="e" * 64,
            reused=False,
        )
    )
    monkeypatch.setattr(module, "get_market_intelligence_runner", lambda: runner)

    with caplog.at_level(logging.INFO):
        module.calculate_sector_intelligence_snapshot.run(
            "2026-05-15", force_refresh=True
        )

    force_refresh = next(
        record
        for record in caplog.records
        if getattr(record, "event", None)
        == "market_intelligence_task_force_refresh"
    )
    assert force_refresh.reuse_status == "FORCE_REFRESH"
    assert force_refresh.stage == "force_refresh"


def test_worker_execution_failure_is_not_labeled_as_delivery_failure(
    monkeypatch,
    caplog,
) -> None:
    from app.tasks import market_intelligence_tasks as module

    runner = SimpleNamespace(
        execute=lambda command: (_ for _ in ()).throw(RuntimeError("runner failed"))
    )
    monkeypatch.setattr(module, "get_market_intelligence_runner", lambda: runner)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="runner failed"):
            module.calculate_sector_intelligence_snapshot.run("2026-05-15")

    failure = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "market_intelligence_task_failed"
    )
    assert failure.failure_category == "INVALID_MARKET_DATA"
    assert failure.stage == "pipeline"
    assert failure.publication_status == "FAILED"


def test_worker_execution_failure_preserves_pipeline_category(
    monkeypatch,
    caplog,
) -> None:
    from app.tasks import market_intelligence_tasks as module

    error = RuntimeError("observability write failed")
    error.market_intelligence_failure_category = (
        MarketIntelligenceErrorCategory.DATABASE_FAILURE
    )
    error.market_intelligence_stage = "persistence"
    runner = SimpleNamespace(
        execute=lambda command: (_ for _ in ()).throw(error)
    )
    monkeypatch.setattr(module, "get_market_intelligence_runner", lambda: runner)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="observability write failed"):
            module.calculate_sector_intelligence_snapshot.run("2026-05-15")

    failure = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "market_intelligence_task_failed"
    )
    assert failure.failure_category == "DATABASE_FAILURE"
    assert failure.stage == "persistence"


def test_task_logs_logical_failed_result_with_pipeline_category(
    monkeypatch,
    caplog,
) -> None:
    from app.tasks import market_intelligence_tasks as module

    runner = SimpleNamespace(
        execute=lambda command: SimpleNamespace(
            run_id=53,
            ingestion_status=IngestionStatus.FAILED,
            published=False,
            idempotency_key="f" * 64,
            reused=False,
            failure_category=MarketIntelligenceErrorCategory.PROVIDER_FAILURE,
        )
    )
    monkeypatch.setattr(module, "get_market_intelligence_runner", lambda: runner)

    with caplog.at_level(logging.INFO):
        result = module.calculate_sector_intelligence_snapshot.run("2026-05-15")

    events = [getattr(record, "event", None) for record in caplog.records]
    assert "market_intelligence_task_failed" in events
    assert "market_intelligence_task_completed" not in events
    failure = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "market_intelligence_task_failed"
    )
    assert failure.run_id == 53
    assert failure.failure_category == "PROVIDER_FAILURE"
    assert failure.stage == "pipeline"
    assert result["status"] == "FAILED"


def test_task_logs_started_before_market_validation_failure(caplog) -> None:
    from app.tasks import market_intelligence_tasks as module

    with caplog.at_level(logging.INFO):
        with pytest.raises(ValueError, match="US-only"):
            module.calculate_sector_intelligence_snapshot.run(
                "2026-05-15", market="HK"
            )

    events = [getattr(record, "event", None) for record in caplog.records]
    assert events.index("market_intelligence_task_started") < events.index(
        "market_intelligence_task_failed"
    )
    failure = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "market_intelligence_task_failed"
    )
    assert failure.failure_category == "INVALID_MARKET_DATA"
    assert failure.stage == "validation"


def test_runtime_container_returns_one_process_scoped_runner(monkeypatch) -> None:
    from app.wiring import bootstrap

    sentinel = object()
    calls = []

    def fake_builder(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(runner=sentinel)

    monkeypatch.setattr(
        "app.wiring.market_intelligence_services.build_market_intelligence_services",
        fake_builder,
    )
    runtime = bootstrap.RuntimeServices(session_factory=lambda: None)

    assert runtime.market_intelligence_runner() is sentinel
    assert runtime.market_intelligence_runner() is sentinel
    assert len(calls) == 1
    assert calls[0]["market_calendar"] is runtime.market_calendar_service()


def test_task_rejects_non_us_bootstrap_market() -> None:
    from app.tasks import market_intelligence_tasks as module

    with pytest.raises(ValueError, match="US-only"):
        module.calculate_sector_intelligence_snapshot.run(market="HK")
