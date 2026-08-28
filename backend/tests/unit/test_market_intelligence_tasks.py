"""Runtime and Celery task contracts for Phase 1 sector intelligence."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.domain.market_intelligence.models import IngestionStatus


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
