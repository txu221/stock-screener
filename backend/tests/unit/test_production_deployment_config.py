"""Production deployment input and Compose contract tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "backend" / "scripts" / "validate_production_deployment.py"


def _load_validator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "validate_production_deployment",
        SCRIPT_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_environment() -> dict[str, str]:
    return {
        "DOMAIN": "stocks.acme-finance.com",
        "CORS_ORIGINS": "https://stocks.acme-finance.com",
        "POSTGRES_DB": "stockscanner",
        "POSTGRES_USER": "stockscanner",
        "POSTGRES_PASSWORD": "p" * 32,
        "SERVER_AUTH_PASSWORD": "a" * 32,
        "SERVER_AUTH_SESSION_SECRET": "s" * 64,
        "BACKEND_IMAGE_REF": (
            "ghcr.io/txu221/stockscreenclaude-backend@sha256:" + "1" * 64
        ),
        "FRONTEND_IMAGE_REF": (
            "ghcr.io/txu221/stockscreenclaude-frontend@sha256:" + "2" * 64
        ),
        "RELEASE_GIT_SHA": "3" * 40,
        "ENABLED_MARKETS": "US",
        "COMPOSE_PROFILES": "backup",
        "POSTGRES_BACKUP_RETENTION_COUNT": "7",
        "POSTGRES_BACKUP_INTERVAL_SECONDS": "86400",
        "POSTGRES_BACKUP_INITIAL_DELAY_SECONDS": "300",
        "CELERY_TIMEZONE": "America/New_York",
        "WEB_CONCURRENCY": "2",
        "DB_POOL_SIZE": "5",
        "DB_MAX_OVERFLOW": "5",
        "MARKET_DATA_SOURCE_MODE": "github_first",
        "SERVER_AUTH_SECURE_COOKIE": "true",
        "SERVER_EXPOSE_API_DOCS": "false",
        "STATIC_EXPORT_ENABLED": "false",
    }


def test_valid_production_environment_is_accepted() -> None:
    validator = _load_validator()

    assert validator.validate_environment(_valid_environment()) == []


@pytest.mark.parametrize("missing_key", sorted(_valid_environment()))
def test_every_required_production_value_is_enforced(missing_key: str) -> None:
    validator = _load_validator()
    values = _valid_environment()
    values.pop(missing_key)

    errors = validator.validate_environment(values)

    assert any(missing_key in error for error in errors)


@pytest.mark.parametrize(
    ("field", "value", "expected_fragment"),
    [
        ("DOMAIN", "stocks.example.com", "DOMAIN"),
        ("DOMAIN", "https://stocks.acme-finance.com", "DOMAIN"),
        ("CORS_ORIGINS", "http://stocks.acme-finance.com", "CORS_ORIGINS"),
        ("CORS_ORIGINS", "https://other.acme-finance.com", "CORS_ORIGINS"),
        ("POSTGRES_PASSWORD", "stockscanner", "POSTGRES_PASSWORD"),
        ("POSTGRES_PASSWORD", "short", "POSTGRES_PASSWORD"),
        ("SERVER_AUTH_PASSWORD", "CHANGE_ME_RANDOM_32_CHARS", "SERVER_AUTH_PASSWORD"),
        ("SERVER_AUTH_SESSION_SECRET", "short", "SERVER_AUTH_SESSION_SECRET"),
        (
            "BACKEND_IMAGE_REF",
            "ghcr.io/txu221/stockscreenclaude-backend:latest",
            "BACKEND_IMAGE_REF",
        ),
        (
            "FRONTEND_IMAGE_REF",
            "ghcr.io/txu221/stockscreenclaude-frontend:sha-abcdef0",
            "FRONTEND_IMAGE_REF",
        ),
        ("RELEASE_GIT_SHA", "abcdef0", "RELEASE_GIT_SHA"),
        ("ENABLED_MARKETS", "US,HK", "ENABLED_MARKETS"),
        ("COMPOSE_PROFILES", "assistant,backup", "COMPOSE_PROFILES"),
        ("COMPOSE_PROFILES", "market-us", "COMPOSE_PROFILES"),
        ("POSTGRES_BACKUP_RETENTION_COUNT", "1", "POSTGRES_BACKUP_RETENTION_COUNT"),
        ("POSTGRES_BACKUP_INTERVAL_SECONDS", "0", "POSTGRES_BACKUP_INTERVAL_SECONDS"),
        ("POSTGRES_BACKUP_INITIAL_DELAY_SECONDS", "-1", "POSTGRES_BACKUP_INITIAL_DELAY_SECONDS"),
        ("CELERY_TIMEZONE", "UTC", "CELERY_TIMEZONE"),
        ("WEB_CONCURRENCY", "0", "WEB_CONCURRENCY"),
        ("DB_POOL_SIZE", "0", "DB_POOL_SIZE"),
        ("DB_MAX_OVERFLOW", "-1", "DB_MAX_OVERFLOW"),
        ("MARKET_DATA_SOURCE_MODE", "live_only", "MARKET_DATA_SOURCE_MODE"),
        ("SERVER_AUTH_SECURE_COOKIE", "false", "SERVER_AUTH_SECURE_COOKIE"),
        ("SERVER_EXPOSE_API_DOCS", "true", "SERVER_EXPOSE_API_DOCS"),
        ("STATIC_EXPORT_ENABLED", "true", "STATIC_EXPORT_ENABLED"),
    ],
)
def test_unsafe_production_values_are_rejected(
    field: str,
    value: str,
    expected_fragment: str,
) -> None:
    validator = _load_validator()
    values = _valid_environment()
    values[field] = value

    errors = validator.validate_environment(values)

    assert any(expected_fragment in error for error in errors)


def test_auth_and_session_secrets_must_be_independent() -> None:
    validator = _load_validator()
    values = _valid_environment()
    values["SERVER_AUTH_SESSION_SECRET"] = values["SERVER_AUTH_PASSWORD"]

    errors = validator.validate_environment(values)

    assert any("independent" in error.lower() for error in errors)


def test_validation_errors_do_not_echo_secret_values() -> None:
    validator = _load_validator()
    values = _valid_environment()
    secret = "do-not-echo-this-secret"
    values["POSTGRES_PASSWORD"] = secret
    values["SERVER_AUTH_PASSWORD"] = secret

    errors = validator.validate_environment(values)

    assert errors
    assert secret not in "\n".join(errors)


def test_load_environment_supports_comments_and_quoted_values(tmp_path: Path) -> None:
    validator = _load_validator()
    env_file = tmp_path / ".env.docker"
    env_file.write_text(
        "# deployment\nDOMAIN='stocks.acme-finance.com'\n"
        'CORS_ORIGINS="https://stocks.acme-finance.com"\n',
        encoding="utf-8",
    )

    assert validator.load_environment(env_file) == {
        "DOMAIN": "stocks.acme-finance.com",
        "CORS_ORIGINS": "https://stocks.acme-finance.com",
    }


def test_compose_command_uses_approved_files_in_order(tmp_path: Path) -> None:
    validator = _load_validator()
    env_file = tmp_path / ".env.docker"

    command = validator.compose_command(ROOT, env_file)

    expected_files = [
        "docker-compose.yml",
        "docker-compose.prod.yml",
        "docker-compose.release.yml",
        "docker-compose.https.yml",
    ]
    positions = [command.index(name) for name in expected_files]
    assert positions == sorted(positions)
    assert command[:2] == ["bash", str(ROOT / "scripts" / "docker-compose-enabled-markets.sh")]
    assert command[-2:] == ["config", "--quiet"]


def test_production_template_contains_only_rejected_secret_sentinels() -> None:
    content = (ROOT / ".env.production.example").read_text(encoding="utf-8")

    assert "ENABLED_MARKETS=US" in content
    assert "COMPOSE_PROFILES=backup" in content
    assert "POSTGRES_BACKUP_RETENTION_COUNT=7" in content
    assert "SERVER_AUTH_SECURE_COOKIE=true" in content
    assert "APP_IMAGE_TAG" not in content
    assert ":latest" not in content
    assert "CHANGE_ME_RANDOM_32_CHARS" in content


def test_release_overlay_requires_digest_refs_and_full_revision() -> None:
    content = (ROOT / "docker-compose.release.yml").read_text(encoding="utf-8")

    assert (
        "image: ${BACKEND_IMAGE_REF:?Set BACKEND_IMAGE_REF to a GHCR digest reference}"
        in content
    )
    assert (
        "image: ${FRONTEND_IMAGE_REF:?Set FRONTEND_IMAGE_REF to a GHCR digest reference}"
        in content
    )
    assert content.count(
        "org.opencontainers.image.revision: ${RELEASE_GIT_SHA:?Set RELEASE_GIT_SHA}"
    ) == 2
    assert "pull_policy: always" in content
    assert "APP_IMAGE_TAG" not in content
    assert "${BACKEND_IMAGE}:" not in content
    assert "${FRONTEND_IMAGE}:" not in content
    assert ":latest" not in content


def test_base_compose_forwards_existing_optional_admin_and_github_data_tokens() -> None:
    content = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "GITHUB_DATA_TOKEN: ${GITHUB_DATA_TOKEN:-}" in content
    assert "ADMIN_API_KEY: ${ADMIN_API_KEY:-}" in content


def test_caddy_compose_healthcheck_uses_internal_health_endpoint() -> None:
    content = (ROOT / "docker-compose.https.yml").read_text(encoding="utf-8")

    assert "http://127.0.0.1:8080/health" in content
    assert "interval: 30s" in content
    assert "timeout: 10s" in content
    assert "retries: 3" in content
    assert "start_period: 15s" in content
