from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.market_intelligence.constants import MARKET_INTELLIGENCE_UNIVERSE
from tests.integration.market_intelligence.support import (
    Phase2EnvironmentError,
    enabled_by_environment,
    explicitly_enabled,
    redact_service_url,
    require_postgresql_url,
)


EXPECTED_UNIVERSE = (
    "SPY",
    "XLC",
    "XLY",
    "XLP",
    "XLE",
    "XLF",
    "XLV",
    "XLI",
    "XLB",
    "XLRE",
    "XLK",
    "XLU",
)
PROJECT_ROOT = Path(__file__).resolve().parents[4]


def test_phase2_universe_is_fixed_to_twelve_etfs() -> None:
    assert MARKET_INTELLIGENCE_UNIVERSE == EXPECTED_UNIVERSE
    assert len(MARKET_INTELLIGENCE_UNIVERSE) == 12


@pytest.mark.parametrize(
    "value",
    ("sqlite://", "sqlite:///phase2.db", "mysql://localhost/phase2", ""),
)
def test_postgresql_gate_refuses_non_postgresql_urls(value: str) -> None:
    with pytest.raises(Phase2EnvironmentError, match="real PostgreSQL"):
        require_postgresql_url(value)


def test_postgresql_gate_accepts_explicit_postgresql_url() -> None:
    value = "postgresql+psycopg2://phase2:secret@localhost:5432/phase2"

    assert require_postgresql_url(value) == value


def test_postgresql_gate_accepts_exact_market_intelligence_slo_database() -> None:
    value = "postgresql+psycopg2://slo:secret@localhost/market_intelligence_slo"

    assert require_postgresql_url(value) == value


def test_postgresql_gate_does_not_accept_an_absent_dedicated_url() -> None:
    with pytest.raises(Phase2EnvironmentError, match="real PostgreSQL URL"):
        require_postgresql_url(None)


def test_postgresql_gate_rejects_a_non_test_database() -> None:
    with pytest.raises(Phase2EnvironmentError, match="dedicated phase2/test database"):
        require_postgresql_url("postgresql://localhost/stock_screener")


@pytest.mark.parametrize("value", ("1", "true", "TRUE", "yes", "on"))
def test_opt_in_gate_accepts_only_explicit_truthy_values(value: str) -> None:
    assert enabled_by_environment(value) is True


@pytest.mark.parametrize("value", (None, "", "0", "false", "off", "anything"))
def test_opt_in_gate_defaults_to_disabled(value: str | None) -> None:
    assert enabled_by_environment(value) is False


@pytest.mark.parametrize(
    ("value", "expected"),
    (("1", True), ("true", False), ("yes", False), ("on", False), (None, False)),
)
def test_destructive_gate_requires_exact_one(value: str | None, expected: bool) -> None:
    assert explicitly_enabled(value) is expected


def test_service_url_redaction_removes_credentials() -> None:
    value = "postgresql://phase2:super-secret@localhost:5432/phase2?sslmode=require"

    redacted = redact_service_url(value)

    assert redacted == "postgresql://***:***@localhost:5432/phase2"
    assert "phase2:super-secret" not in redacted


def test_service_url_redaction_handles_passwordless_url() -> None:
    assert (
        redact_service_url("redis://localhost:6379/0?token=secret#private")
        == "redis://localhost:6379/0"
    )


def test_yahoo_canary_workflow_is_weekday_manual_and_production_read_only() -> None:
    workflow = (
        PROJECT_ROOT
        / ".github"
        / "workflows"
        / "market-intelligence-yahoo-canary.yml"
    ).read_text(encoding="utf-8")

    assert "schedule:" in workflow
    assert "* * 1-5" in workflow
    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow
    assert "contents: read" in workflow
    assert "RUN_MARKET_INTELLIGENCE_LIVE: \"1\"" in workflow
    assert "test_live_yahoo_validation.py" in workflow
    for forbidden in (
        "DATABASE_URL",
        "POSTGRES_",
        "REDIS_URL",
        "CELERY_",
        "alembic",
        "docker",
        "services:",
        "contents: write",
    ):
        assert forbidden not in workflow

    integration_workflow = (
        PROJECT_ROOT
        / ".github"
        / "workflows"
        / "market-intelligence-integration.yml"
    ).read_text(encoding="utf-8")
    assert (
        "github.event_name == 'workflow_dispatch' && "
        "inputs.run_live_yahoo == true"
    ) in integration_workflow
    assert (
        "github.event_name == 'push' || inputs.run_live_yahoo == true"
        not in integration_workflow
    )
