"""Environment gates shared by opt-in Phase 2 integration checks."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


class Phase2EnvironmentError(ValueError):
    """Raised when an opt-in service check is pointed at the wrong service."""


def enabled_by_environment(value: str | None) -> bool:
    """Return true only for a small, explicit opt-in vocabulary."""

    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def explicitly_enabled(value: str | None) -> bool:
    """Use an exact opt-in for checks that can mutate external services."""

    return value == "1"


def require_postgresql_url(value: str | None) -> str:
    """Reject SQLite and other substitutes for real PostgreSQL evidence."""

    candidate = (value or "").strip()
    scheme = urlsplit(candidate).scheme.lower()
    if scheme not in {"postgres", "postgresql", "postgresql+psycopg2"}:
        raise Phase2EnvironmentError(
            "Phase 2 PostgreSQL checks require a real PostgreSQL URL"
        )
    database_name = urlsplit(candidate).path.rsplit("/", 1)[-1].lower()
    is_phase2_or_test = any(
        marker in database_name for marker in ("phase2", "test")
    )
    is_exact_slo_database = database_name == "market_intelligence_slo"
    if not database_name or not (is_phase2_or_test or is_exact_slo_database):
        raise Phase2EnvironmentError(
            "Phase 2 PostgreSQL checks require a dedicated phase2/test database"
        )
    return candidate


def redact_service_url(value: str) -> str:
    """Redact URL userinfo while retaining non-secret diagnostic topology."""

    parsed = urlsplit(value)
    if parsed.username is None:
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port is not None else ""
    credentials = "***:***" if parsed.password is not None else "***"
    return urlunsplit((parsed.scheme, f"{credentials}@{host}{port}", parsed.path, "", ""))
