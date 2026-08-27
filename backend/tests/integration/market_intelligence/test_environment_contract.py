from __future__ import annotations

import pytest

from app.domain.market_intelligence.constants import MARKET_INTELLIGENCE_UNIVERSE
from tests.integration.market_intelligence.support import (
    Phase2EnvironmentError,
    enabled_by_environment,
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


@pytest.mark.parametrize("value", ("1", "true", "TRUE", "yes", "on"))
def test_opt_in_gate_accepts_only_explicit_truthy_values(value: str) -> None:
    assert enabled_by_environment(value) is True


@pytest.mark.parametrize("value", (None, "", "0", "false", "off", "anything"))
def test_opt_in_gate_defaults_to_disabled(value: str | None) -> None:
    assert enabled_by_environment(value) is False


def test_service_url_redaction_removes_credentials() -> None:
    value = "postgresql://phase2:super-secret@localhost:5432/phase2?sslmode=require"

    redacted = redact_service_url(value)

    assert redacted == "postgresql://***:***@localhost:5432/phase2?sslmode=require"
    assert "phase2:super-secret" not in redacted


def test_service_url_redaction_handles_passwordless_url() -> None:
    assert (
        redact_service_url("redis://localhost:6379/0")
        == "redis://localhost:6379/0"
    )
