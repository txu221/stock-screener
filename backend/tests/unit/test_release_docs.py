"""Regression checks for release workflow notes and deployment docs."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


PRODUCTION_COMPOSE_FILES = (
    "-f docker-compose.yml -f docker-compose.prod.yml "
    "-f docker-compose.release.yml -f docker-compose.https.yml"
)


def test_release_workflow_uses_curated_release_notes() -> None:
    content = (ROOT / ".github" / "workflows" / "ci.yml").read_text()

    assert "body_path: .github/release-notes.md" in content
    assert "generate_release_notes: true" not in content


def test_readme_documents_current_release_compose_flow() -> None:
    content = (ROOT / "README.md").read_text()

    assert "Production Deployment Runbook" in content
    assert "BACKEND_IMAGE_REF=ghcr.io/" in content
    assert "@sha256:" in content
    assert "APP_IMAGE_TAG" not in content
    assert PRODUCTION_COMPOSE_FILES in content


def test_install_docker_uses_current_release_example() -> None:
    content = (ROOT / "docs" / "INSTALL_DOCKER.md").read_text()

    assert "Python 3.11+" in content
    assert "STOCKSCREEN_PYTHON" in content
    assert "runbooks/production-deployment.md" in content
    assert "BACKEND_IMAGE_REF" in content
    assert "RELEASE_GIT_SHA" in content
    assert "APP_IMAGE_TAG" not in content
    assert "latest" in content
    assert "must not" in content
    assert "--env-file .env.docker" in content
    assert PRODUCTION_COMPOSE_FILES in content


def test_production_runbook_covers_the_approved_linear_path() -> None:
    content = (ROOT / "docs" / "runbooks" / "production-deployment.md").read_text()

    for required in (
        "Ubuntu 24.04 LTS",
        "ENABLED_MARKETS=US",
        "COMPOSE_PROFILES=backup",
        "validate_production_deployment.py",
        PRODUCTION_COMPOSE_FILES,
        "alembic upgrade head",
        "calculate_sector_intelligence_snapshot",
        "/api/v1/market-intelligence/sectors/latest",
        "/api/v1/market-intelligence/sectors/history",
        "/api/v1/market-intelligence/sectors/health",
        "/market-intelligence",
        "/livez",
        "/readyz",
        "/nginx-health",
        "POSTGRES_BACKUP_RUN_ONCE=1",
        "pg_restore --list",
        "restore drill",
        "release-manifest.env",
        "Emergency source-build fallback",
        "docker compose down -v",
    ):
        assert required in content
    assert "APP_IMAGE_TAG" not in content
    assert content.count("REQUIRES VPS VALIDATION") >= 8


def test_production_environment_docs_use_digest_contract() -> None:
    content = (ROOT / "docs" / "ENVIRONMENT.md").read_text()

    for required in (
        "BACKEND_IMAGE_REF",
        "FRONTEND_IMAGE_REF",
        "RELEASE_GIT_SHA",
        "POSTGRES_BACKUP_RETENTION_COUNT",
        "GITHUB_DATA_TOKEN",
        "ADMIN_API_KEY",
    ):
        assert required in content
    assert "APP_IMAGE_TAG" not in content


def test_production_readiness_checklist_is_honest_and_categorized() -> None:
    content = (ROOT / "docs" / "production-readiness-checklist.md").read_text()

    for category in (
        "Already Complete",
        "Can Complete Locally",
        "Requires VPS",
        "Requires Domain / DNS",
        "Requires Credentials",
        "Post-deployment Monitoring",
    ):
        assert category in content
    assert "Ubuntu 24.04 LTS / 4 vCPU / 8 GB RAM / 80 GB SSD" in content
    assert "Ubuntu 24.04 LTS / 2 vCPU / 4 GB RAM / 40 GB SSD" in content
    assert "Real Production Deployment Pending" in content


def test_required_inputs_inventory_covers_external_ownership() -> None:
    content = (ROOT / "docs" / "production-required-inputs.md").read_text()

    for required in (
        "VPS provider",
        "public IPv4",
        "SSH public key",
        "domain",
        "DNS",
        "GHCR",
        "POSTGRES_PASSWORD",
        "SERVER_AUTH_PASSWORD",
        "SERVER_AUTH_SESSION_SECRET",
        "BACKEND_IMAGE_REF",
        "FRONTEND_IMAGE_REF",
        "RELEASE_GIT_SHA",
        "off-host backup",
        "monitoring destination",
        "deployment window",
    ):
        assert required in content
    assert "LLM, News, AI, and Options Flow credentials are not required" in content
    assert "Real Production Deployment Pending" in content


def test_curated_release_notes_capture_v1_capabilities() -> None:
    content = (ROOT / ".github" / "release-notes.md").read_text()

    assert "# Stock Scanner v" in content
    assert "multi-market" in content.lower()
    assert "first-run bootstrap" in content.lower()
    assert "GHCR" in content
