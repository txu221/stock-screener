# Docker Deployment

Docker is the supported deployment method for servers, homelabs, and VPS hosting. The project uses a layered Docker Compose architecture with composable overlays for different scenarios.

Docker deployments use **PostgreSQL** as the application database. The shared `./data` mount handles non-database state (Celery beat schedule, caches).

## Prerequisites

- Docker Engine 20.10+
- Docker Compose v2 (`docker compose` or `docker-compose`)
- Python 3.11+ on the host for `scripts/docker-compose-enabled-markets.sh`; set `STOCKSCREEN_PYTHON=/path/to/python3.11` if your default `python3` is older.

## Quick Start (Local Development)

Zero-config local deployment:

```bash
# 1. Set up environment (required for chatbot/LLM features)
cp .env.docker.example .env
# Edit .env: Set SERVER_AUTH_PASSWORD and add your API keys (GROQ_API_KEY, MINIMAX_API_KEY, etc.)

# 2. Start the local-default stack
scripts/docker-compose-enabled-markets.sh up
```

This starts PostgreSQL, Redis, the Backend API, the shared Celery workers, the market workers selected by `ENABLED_MARKETS`, and the Frontend. Access at **http://localhost**.

> **Note:** Local backups are now opt-in so the default laptop stack stays lighter. Start `db-backup` with `COMPOSE_PROFILES=backup scripts/docker-compose-enabled-markets.sh up -d db-backup` (or add the profile in a local override) when you want local `pg_dump` snapshots under `./data/backups`.

> **Note:** This local quick start reads environment variables from `.env` in the project root. The production examples below pass `--env-file .env.docker` explicitly. `SERVER_AUTH_PASSWORD` is required for server access, and LLM API keys are required for chatbot features.

## Homelab (Behind Reverse Proxy)

For deployment behind Traefik, nginx proxy manager, or similar:

```bash
# 1. Configure environment
cp .env.docker.example .env.docker
# Edit .env.docker: Set SERVER_AUTH_PASSWORD, CORS_ORIGINS=https://stocks.home.lan,
# and SERVER_AUTH_SECURE_COOKIE=true if your proxy terminates HTTPS

# 2. Start with production settings
ENABLED_MARKETS=US,HK,CN scripts/docker-compose-enabled-markets.sh --env-file .env.docker -f docker-compose.yml -f docker-compose.prod.yml up -d

# 3. Configure your reverse proxy to forward to port 80
```

The production overlay adds resource limits, health checks, and JSON logging with rotation.

## Production on a VPS

The sole formal production path is the
[Production Deployment Runbook](runbooks/production-deployment.md). It uses the
four overlays below, always in this order, and pins both application images to
complete GHCR digest references that map to `RELEASE_GIT_SHA`:

```bash
cp .env.production.example .env.docker
python3 backend/scripts/validate_production_deployment.py --env-file .env.docker
scripts/docker-compose-enabled-markets.sh --env-file .env.docker -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.release.yml -f docker-compose.https.yml pull
scripts/docker-compose-enabled-markets.sh --env-file .env.docker -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.release.yml -f docker-compose.https.yml up -d --no-build
```

`BACKEND_IMAGE_REF` and `FRONTEND_IMAGE_REF` must be full
`ghcr.io/...@sha256:<64-hex>` references. `RELEASE_GIT_SHA` must be the full
40-character source commit. Rolling, branch, semantic-version, and `latest`
tags may help an operator discover a digest, but they must not be production
inputs. The first release is fixed to `ENABLED_MARKETS=US` and
`COMPOSE_PROFILES=backup`.

If packages are private, the host authenticates with a read-only package token;
that credential stays in Docker's host credential store and is never copied
into `.env.docker` or an application container. Exact authentication, staged
startup, migration, verification, backup, restore, rollback, and disaster
recovery commands are intentionally centralized in the Runbook.

## Services Architecture

| Service | Purpose |
|---------|---------|
| `redis` | Celery broker (DB 0) and result backend (DB 1) |
| `postgres` | Application database |
| `backend` | FastAPI API server |
| `celery-worker` | General compute queue (2 workers) |
| `celery-datafetch` | Data fetch queue (1 worker, serialized for rate limits; listens only to enabled-market queues) |
| `celery-userscans` | Shared user scan queue |
| `celery-marketjobs-*` | Enabled-market compute queues, selected through Compose profiles |
| `celery-userscans-*` | Enabled-market user scan queues, selected through Compose profiles |
| `celery-beat` | Celery Beat scheduler |
| `db-backup` | Automated PostgreSQL backups to `./data/backups` |
| `frontend` | React app served via nginx |
| `caddy` | (HTTPS overlay only) TLS termination with Let's Encrypt |

## Docker Compose File Reference

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Base configuration for local development |
| `docker-compose.prod.yml` | Production overlay: resource limits, health checks, logging |
| `docker-compose.release.yml` | Release overlay: deploy required immutable GHCR digest references |
| `docker-compose.https.yml` | HTTPS overlay: Caddy with automatic Let's Encrypt |
| `.env.production.example` | Strict placeholder-only production environment contract |
| `.env.docker.example` | Development/homelab Docker environment template |
| `Caddyfile` | Caddy configuration for TLS termination |

## PostgreSQL Notes

### Upgrade from Older Versions

The backend runs as non-root user (uid 1000). If upgrading from an older version:
```bash
sudo chown -R 1000:1000 ./data
```

### Backups and Restore

PostgreSQL backups are automatically written by the `db-backup` service via `pg_dump`. Restore with:
```bash
pg_restore -d <database> <dump-file>
```

### Legacy Pre-Alembic Upgrade Path

Older installs that already have a populated PostgreSQL schema but no `alembic_version` marker should run the one-shot reconciliation script before the first post-upgrade boot:

Use the same Compose file stack you deployed with for both commands below. Examples:
- Local/default stack: `scripts/docker-compose-enabled-markets.sh ...`
- Production overlay: `scripts/docker-compose-enabled-markets.sh --env-file .env.docker -f docker-compose.yml -f docker-compose.prod.yml ...`
- HTTPS overlay: `scripts/docker-compose-enabled-markets.sh --env-file .env.docker -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.https.yml ...`

```bash
scripts/docker-compose-enabled-markets.sh --env-file .env.docker -f docker-compose.yml -f docker-compose.prod.yml run --rm backend python scripts/run_legacy_runtime_migrations.py
scripts/docker-compose-enabled-markets.sh --env-file .env.docker -f docker-compose.yml -f docker-compose.prod.yml run --rm backend alembic upgrade head
```

Fresh installs now auto-seed `ibd_industry_groups` from the bundled canonical CSV on backend startup when the table is empty.
If you already have an existing database with an empty `ibd_industry_groups` table, repair it with:
```bash
scripts/docker-compose-enabled-markets.sh --env-file .env.docker -f docker-compose.yml -f docker-compose.prod.yml run --rm backend python scripts/seed_ibd_industry_groups.py
```

## Troubleshooting

### Chatbot not responding
Docker Compose reads API keys from `.env` in the project root. If `.env` is missing or keys are empty, scanning still works but the chatbot won't. Check:
```bash
docker compose exec backend env | grep -i API_KEY
```

### CORS errors in browser
Set `CORS_ORIGINS` in your environment file to match your access URL (e.g., `https://stocks.home.lan`). Restart the backend after changes.

### Permission denied on ./data
The backend container runs as uid 1000. Fix with:
```bash
sudo chown -R 1000:1000 ./data
```

### Container health checks failing
```bash
docker compose ps
docker compose logs backend
docker compose exec backend curl -f http://localhost:8000/readyz
curl -f http://localhost/nginx-health
```
