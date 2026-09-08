#!/usr/bin/env python3
"""Fail-closed validation for the single production Docker Compose path."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
APPROVED_COMPOSE_FILES = (
    "docker-compose.yml",
    "docker-compose.prod.yml",
    "docker-compose.release.yml",
    "docker-compose.https.yml",
)
REQUIRED_VALUES = (
    "DOMAIN",
    "CORS_ORIGINS",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "SERVER_AUTH_PASSWORD",
    "SERVER_AUTH_SESSION_SECRET",
    "BACKEND_IMAGE_REF",
    "FRONTEND_IMAGE_REF",
    "RELEASE_GIT_SHA",
    "ENABLED_MARKETS",
    "COMPOSE_PROFILES",
    "POSTGRES_BACKUP_RETENTION_COUNT",
    "POSTGRES_BACKUP_INTERVAL_SECONDS",
    "POSTGRES_BACKUP_INITIAL_DELAY_SECONDS",
    "CELERY_TIMEZONE",
    "WEB_CONCURRENCY",
    "DB_POOL_SIZE",
    "DB_MAX_OVERFLOW",
    "MARKET_DATA_SOURCE_MODE",
    "SERVER_AUTH_SECURE_COOKIE",
    "SERVER_EXPOSE_API_DOCS",
    "STATIC_EXPORT_ENABLED",
)
IMAGE_REF_PATTERN = re.compile(
    r"^ghcr\.io/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$"
)
GIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
REJECTED_DOMAIN_SUFFIXES = (".example", ".invalid", ".localhost", ".test")
REJECTED_SECRET_FRAGMENTS = (
    "change_me",
    "changeme",
    "example",
    "password",
    "replace_me",
    "stockscanner",
)


def load_environment(path: Path) -> dict[str, str]:
    """Parse the Compose-compatible KEY=value subset used by the template."""
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"line {line_number} is not KEY=value")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"line {line_number} has an empty key")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _require_positive_integer(
    values: Mapping[str, str],
    key: str,
    errors: list[str],
    *,
    minimum: int,
) -> None:
    raw = values.get(key, "")
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        errors.append(f"{key} must be an integer >= {minimum}")
        return
    if parsed < minimum:
        errors.append(f"{key} must be >= {minimum}")


def _validate_secret(
    values: Mapping[str, str],
    key: str,
    errors: list[str],
    *,
    minimum_length: int,
) -> None:
    value = values.get(key, "")
    normalized = value.casefold()
    if len(value) < minimum_length or any(
        fragment in normalized for fragment in REJECTED_SECRET_FRAGMENTS
    ):
        errors.append(
            f"{key} must be a non-placeholder secret of at least "
            f"{minimum_length} characters"
        )


def validate_environment(values: Mapping[str, str]) -> list[str]:
    """Return field-only errors for an unsafe production environment."""
    errors: list[str] = []
    for key in REQUIRED_VALUES:
        if not str(values.get(key, "")).strip():
            errors.append(f"{key} is required")

    domain = values.get("DOMAIN", "").strip().lower()
    if (
        not DOMAIN_PATTERN.fullmatch(domain)
        or domain in {"localhost", "example.com", "example.net", "example.org"}
        or domain.endswith(REJECTED_DOMAIN_SUFFIXES)
        or "change_me" in domain
    ):
        errors.append("DOMAIN must be a real lowercase DNS hostname without scheme or port")

    if values.get("CORS_ORIGINS", "").strip() != f"https://{domain}":
        errors.append("CORS_ORIGINS must equal https://DOMAIN exactly")

    for key in ("POSTGRES_DB", "POSTGRES_USER"):
        if not IDENTIFIER_PATTERN.fullmatch(values.get(key, "")):
            errors.append(f"{key} must be a PostgreSQL-safe identifier")

    _validate_secret(values, "POSTGRES_PASSWORD", errors, minimum_length=32)
    _validate_secret(values, "SERVER_AUTH_PASSWORD", errors, minimum_length=32)
    _validate_secret(
        values,
        "SERVER_AUTH_SESSION_SECRET",
        errors,
        minimum_length=32,
    )
    auth_secret = values.get("SERVER_AUTH_PASSWORD", "")
    session_secret = values.get("SERVER_AUTH_SESSION_SECRET", "")
    if auth_secret and auth_secret == session_secret:
        errors.append(
            "SERVER_AUTH_PASSWORD and SERVER_AUTH_SESSION_SECRET must be independent"
        )

    for key in ("BACKEND_IMAGE_REF", "FRONTEND_IMAGE_REF"):
        if not IMAGE_REF_PATTERN.fullmatch(values.get(key, "")):
            errors.append(f"{key} must be a complete lowercase GHCR @sha256 digest reference")
    if not GIT_SHA_PATTERN.fullmatch(values.get("RELEASE_GIT_SHA", "")):
        errors.append("RELEASE_GIT_SHA must be a full lowercase 40-character Git SHA")

    if values.get("ENABLED_MARKETS", "").strip() != "US":
        errors.append("ENABLED_MARKETS must be exactly US for the first production release")
    if values.get("COMPOSE_PROFILES", "").strip() != "backup":
        errors.append("COMPOSE_PROFILES must be exactly backup; the wrapper adds market-us")

    _require_positive_integer(
        values,
        "POSTGRES_BACKUP_RETENTION_COUNT",
        errors,
        minimum=2,
    )
    _require_positive_integer(
        values,
        "POSTGRES_BACKUP_INTERVAL_SECONDS",
        errors,
        minimum=1,
    )
    _require_positive_integer(
        values,
        "POSTGRES_BACKUP_INITIAL_DELAY_SECONDS",
        errors,
        minimum=0,
    )
    _require_positive_integer(values, "WEB_CONCURRENCY", errors, minimum=1)
    _require_positive_integer(values, "DB_POOL_SIZE", errors, minimum=1)
    _require_positive_integer(values, "DB_MAX_OVERFLOW", errors, minimum=0)

    fixed_values = {
        "CELERY_TIMEZONE": "America/New_York",
        "MARKET_DATA_SOURCE_MODE": "github_first",
        "SERVER_AUTH_SECURE_COOKIE": "true",
        "SERVER_EXPOSE_API_DOCS": "false",
        "STATIC_EXPORT_ENABLED": "false",
    }
    for key, expected in fixed_values.items():
        if values.get(key, "").strip() != expected:
            errors.append(f"{key} must be exactly {expected}")

    return errors


def validate_git_safety(root: Path, env_path: Path) -> list[str]:
    """Confirm the real production environment is ignored and untracked."""
    errors: list[str] = []
    expected = (root / ".env.docker").resolve()
    if env_path.resolve() != expected:
        return ["production environment path must be repository-root .env.docker"]

    ignored = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "--quiet", str(env_path)],
        check=False,
    )
    if ignored.returncode != 0:
        errors.append(".env.docker must be covered by .gitignore")

    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", str(env_path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if tracked.returncode == 0:
        errors.append(".env.docker must not be tracked by Git")
    return errors


def compose_command(root: Path, env_path: Path) -> list[str]:
    """Build the single approved production Compose validation command."""
    command = [
        "bash",
        str(root / "scripts" / "docker-compose-enabled-markets.sh"),
        "--env-file",
        str(env_path),
    ]
    for compose_file in APPROVED_COMPOSE_FILES:
        command.extend(("-f", compose_file))
    command.extend(("config", "--quiet"))
    return command


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=ROOT / ".env.docker",
        help="Production environment file; the supported path is .env.docker",
    )
    parser.add_argument(
        "--skip-compose",
        action="store_true",
        help="Validate values/Git safety only on a host without Docker",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    env_path = args.env_file
    if not env_path.is_absolute():
        env_path = (ROOT / env_path).resolve()
    if not env_path.is_file():
        print("ERROR: .env.docker does not exist", file=sys.stderr)
        return 1

    try:
        values = load_environment(env_path)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"ERROR: unable to parse .env.docker: {type(exc).__name__}", file=sys.stderr)
        return 1

    errors = validate_environment(values)
    errors.extend(validate_git_safety(ROOT, env_path))
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print("Production environment contract: PASS")
    print("Git ignore/tracking contract: PASS")
    if args.skip_compose:
        print("Compose rendering: SKIPPED (host has no Docker)")
        return 0

    compose_env = os.environ.copy()
    compose_env.update(values)
    completed = subprocess.run(
        compose_command(ROOT, env_path),
        cwd=ROOT,
        env=compose_env,
        check=False,
    )
    if completed.returncode != 0:
        print("ERROR: production Compose rendering failed", file=sys.stderr)
        return completed.returncode or 1
    print("Production Compose rendering: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
