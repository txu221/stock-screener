# Production Deployment Preparation Design

## 1. Status and intent

This phase prepares the completed Market Intelligence MVP for a first public
deployment after an operator supplies a VPS, domain, DNS access, and production
credentials. It does not deploy the system and does not claim that public
production is live.

The target terminal state is:

- **Production Deployment Preparation Complete**
- **Real Production Deployment Pending**

The repository baseline is `main` at
`c5a75c671bd647b7c688cc509b7807ec2cb7af03`. The implementation is isolated on
`codex/production-deployment-preparation`; the primary checkout remains clean.

## 2. Fixed scope

The only official production path is an Ubuntu 24.04 LTS VPS running immutable
GHCR image references through these Compose files, in this order:

1. `docker-compose.yml`
2. `docker-compose.prod.yml`
3. `docker-compose.release.yml`
4. `docker-compose.https.yml`

The runtime architecture remains PostgreSQL 16, Redis 7, FastAPI/Uvicorn,
nginx, Caddy, Celery workers, and Celery Beat. The first deployment remains
`ENABLED_MARKETS=US`.

Source builds remain an emergency recovery technique only. They are not a
second supported production architecture and are not an alternative in the
normal deployment sequence.

## 3. Non-goals

This phase must not change Market Intelligence ingestion, validation, metrics,
ranking, snapshot publication, idempotency, API semantics, or frontend product
behavior. It adds no News, AI, Options Flow, provider, market, metric, page, or
other product feature. It does not remediate unrelated dependency advisories or
perform broad dependency upgrades.

The phase does not create real secrets, change DNS, open a firewall, request a
certificate, authenticate to GHCR, start a public service, or describe a static
or CI check as real VPS evidence.

## 4. Audited baseline

The existing repository already provides:

- multi-stage non-root backend and frontend Docker images;
- PostgreSQL and Redis persistence plus health checks;
- automatic Alembic upgrade on FastAPI startup, serialized by a PostgreSQL
  advisory lock across Uvicorn workers;
- worker queue separation and an explicit US market profile;
- Celery Beat scheduling in `America/New_York`;
- the US daily market pipeline, including the sector-intelligence task;
- `/livez`, `/readyz`, frontend `/nginx-health`, and Market Intelligence Data
  Health endpoints;
- nginx API proxying and Caddy automatic HTTPS;
- GHCR image publication on `main` and version tags;
- a read-only scheduled Yahoo contract canary;
- an opt-in PostgreSQL backup service; and
- PostgreSQL/Redis/Celery/migration/Yahoo/production-build evidence in GitHub
  Actions.

The current Windows host has no Docker, PostgreSQL, Redis, or Linux shell
runtime. Full Compose cold start, public networking, DNS, TLS, restart, and
restore drills therefore remain **REQUIRES VPS VALIDATION**.

## 5. Deployment architecture

```text
Internet
   |
DNS A/AAAA -> VPS public address
   |
TCP 80/443
   |
Caddy (TLS, redirect, edge headers)
   |
nginx frontend (SPA, /api proxy, /static-data)
   |
FastAPI backend (/livez, /readyz, /api/v1/*)
   |                         |
PostgreSQL 16          Redis 7 AOF
   ^                         ^
   |                         |
Celery general/datafetch/US market/user workers + Celery Beat
   |
Yahoo provider -> canonical validation -> metrics -> atomic published snapshot
```

Only Caddy binds host ports 80 and 443 in the HTTPS stack. PostgreSQL, Redis,
FastAPI, nginx, and Celery remain on the private Compose network. The host SSH
port is restricted by the operator's firewall policy.

## 6. Immutable image and release identity

The release overlay will require two complete digest references:

- `BACKEND_IMAGE_REF=ghcr.io/<owner>/stockscreenclaude-backend@sha256:<64-hex>`
- `FRONTEND_IMAGE_REF=ghcr.io/<owner>/stockscreenclaude-frontend@sha256:<64-hex>`

It will also require `RELEASE_GIT_SHA=<40-hex>` as operator-visible release
identity. The chosen digests must be resolved from the GHCR `sha-<commit>` or
reviewed semantic-release tag produced by CI. A deployment manifest records
the Git SHA, both digest references, deployment timestamp, and previous release
manifest outside Git.

`latest`, branch-only tags, and bare semantic tags are discovery aids, not
production inputs. Compose deploys the digest references, so registry tag
movement cannot silently change a running or repeated deployment.

Rollback restores the previous manifest's two digest references. Backend and
frontend are always promoted and rolled back as one reviewed release pair.

## 7. Environment and secret contract

`.env.production.example` will be a committed, placeholder-only contract. The
operator copies it to `.env.docker`, which is already covered by `.gitignore`.
Static validation must fail if `.env.docker` is tracked, contains known example
defaults for required secrets, selects markets other than US, omits the backup
profile, or uses non-digest application image references.

Required production values are:

- `DOMAIN` and a matching single-origin `CORS_ORIGINS` value;
- `POSTGRES_DB`, `POSTGRES_USER`, and a random `POSTGRES_PASSWORD`;
- random, independent `SERVER_AUTH_PASSWORD` and
  `SERVER_AUTH_SESSION_SECRET` values;
- `BACKEND_IMAGE_REF`, `FRONTEND_IMAGE_REF`, and `RELEASE_GIT_SHA`;
- `ENABLED_MARKETS=US`;
- `COMPOSE_PROFILES=backup`; and
- resource/pool settings appropriate for the approved VPS baseline.

Optional credentials remain empty unless the operator intentionally enables
their existing capability. `GITHUB_DATA_TOKEN` may be supplied for authenticated
GitHub release-data reads and must be passed to application containers. GHCR
pull authentication is a Docker host credential and is never passed into the
application containers. Yahoo sector ingestion needs no API key.

The Runbook uses `chmod 600 .env.docker`, avoids printing secrets, and prohibits
committing the file. Secret rotation is performed by editing the host file and
recreating the affected containers during an approved window.

## 8. Startup and migration sequence

The standard deployment is deliberately staged:

1. validate host, DNS inputs, environment, image refs, and rendered Compose;
2. save the current release manifest and take a verified pre-deploy database
   backup when upgrading an existing host;
3. authenticate Docker to GHCR only if packages are private;
4. pull the exact backend and frontend digests;
5. start PostgreSQL and Redis and wait for their health checks;
6. run a one-shot `alembic upgrade head` with the pinned backend image;
7. verify `alembic current` reports the expected head;
8. start backend and frontend, then workers and Beat;
9. start Caddy after DNS points to the host and ports 80/443 are reachable;
10. verify all health, ingestion, publication, API, frontend, restart, and
    backup acceptance checks.

Although FastAPI also upgrades the schema safely at startup, the explicit
one-shot migration makes the first-deployment boundary observable and prevents
workers from starting against an unknown schema. Workers already depend on a
healthy backend.

## 9. Yahoo ingestion and publication verification

The first manual data run publishes the existing Celery task
`app.tasks.market_intelligence_tasks.calculate_sector_intelligence_snapshot`
to `market_jobs_us`. It uses the latest completed US trading session unless an
operator supplies a reviewed date. The operator observes the task result and
worker logs, then verifies:

- run status is `SUCCEEDED` for the first publish;
- expected/received/valid coverage is 12/12/12;
- the published pointer references that successful run;
- latest, history, and Data Health APIs agree on date, metric version, provider,
  coverage, and publication state; and
- the frontend renders the production API result through nginx.

A PARTIAL or FAILED retry is evidence to diagnose, not permission to move the
stable pointer. Existing transaction and publication semantics are unchanged.

Celery Beat drives the production daily US pipeline. The GitHub scheduled Yahoo
workflow is a separate read-only provider-contract canary; its first natural
post-deployment observation remains **REQUIRES VPS VALIDATION** only in the
sense that it belongs to the post-deployment acceptance window, even though it
runs on GitHub infrastructure.

## 10. Health and observability

Acceptance checks cover:

- Compose container state and restart count;
- PostgreSQL `pg_isready` and persisted schema/version;
- Redis `PING`, AOF persistence, and Celery broker connectivity;
- Celery worker `inspect ping`, correct US queues, and Beat schedule logs;
- FastAPI `/livez` and `/readyz` semantics;
- nginx `/nginx-health`;
- Caddy internal health plus public HTTP-to-HTTPS redirect;
- valid public certificate hostname, chain, and expiry;
- Market Intelligence latest/history/Data Health responses;
- frontend route rendering through the production origin; and
- structured Docker JSON logs with existing rotation limits.

Caddy will receive a Compose health check against its existing internal
`:8080/health` endpoint. A degraded `/readyz` caused by a missing or stale
snapshot is recorded separately from a fatal database readiness failure.

The initial operational period includes daily checks of the published session,
Yahoo canary, worker/Beat status, error rate, storage, backup age, TLS expiry,
and container restarts. Multi-day evidence is **Post-deployment Monitoring**,
not preparation evidence.

## 11. Backup, retention, and restore

The `backup` Compose profile is mandatory for the official production
environment. The local backup job will produce PostgreSQL custom-format dumps
atomically, validate non-empty output, and retain a configurable bounded set;
the production default is seven daily dumps. It must not delete the last known
valid dump because a new backup failed.

Local dumps alone are not disaster recovery. The operator must copy each
verified dump and its release manifest to encrypted off-host storage controlled
by the operator or VPS provider. The Runbook defines the verification evidence:
timestamp, size, SHA-256, `pg_restore --list`, Git SHA, backend digest, and
frontend digest. Off-host provider selection and credentials are external
inputs and remain **REQUIRES CREDENTIALS**.

A restore drill uses a separate disposable PostgreSQL database or a fresh VPS:
restore the newest verified custom dump, run the pinned backend's Alembic
upgrade to head, start the matching application release, and verify the stable
snapshot and APIs before changing traffic.

## 12. Rollback and disaster recovery

Application rollback is the default response to a bad release:

1. stop Beat and workers to prevent new writes;
2. preserve logs and take a diagnostic database dump when safe;
3. restore the previous backend/frontend digest manifest;
4. inspect migration compatibility;
5. leave a backward-compatible schema at head, or restore the pre-deploy backup
   if the previous application cannot safely use the new schema;
6. recreate services and run the complete health/API/snapshot checks;
7. resume Beat only after verification.

Alembic downgrade is not an automatic rollback mechanism. It is used only when
the specific reviewed migration supports it and after a backup. Destructive or
ambiguous rollback restores the pre-deploy database backup instead.

For total VPS loss, the operator provisions a fresh approved VPS, checks out
the Runbook-compatible repository revision, restores `.env.docker` from the
secret manager, authenticates to GHCR, pulls the saved digest pair, restores
the newest verified off-host dump, upgrades to the saved release's schema head,
starts the stack, validates it, updates DNS if the IP changed, and monitors
until stable.

## 13. Planned deployment-only changes

The implementation may change only these responsibilities:

- `docker-compose.release.yml`: require digest-pinned image references and
  release identity;
- `docker-compose.https.yml`: add Caddy container health evidence;
- `docker-compose.yml`: pass through existing deployment credentials such as
  `GITHUB_DATA_TOKEN` and make backup retention explicit;
- `.env.production.example`: safe first-deployment contract;
- deployment validation script and deterministic tests: reject unsafe or
  incomplete production inputs and render the exact Compose stack;
- `.github/workflows/ci.yml`: run deployment static validation without real
  credentials or public deployment;
- production deployment Runbook, readiness checklist, and input/secrets list;
- existing deployment documentation where it conflicts with the one official
  path; and
- backup implementation only if needed to implement bounded retention without
  weakening last-good-backup preservation.

No application-domain module or frontend source module is an allowed edit.

## 14. Verification strategy

Local deterministic verification will include:

- environment-contract tests with valid, missing, placeholder, wrong-market,
  non-digest, mismatched-domain/CORS, and missing-backup-profile cases;
- static assertions for the exact four-file Compose path, private service
  exposure, Caddy health check, PostgreSQL/Redis persistence, US workers,
  migration command, and backup retention;
- `git check-ignore` and `git ls-files` checks for `.env.docker`;
- `git diff --check` and high-confidence secret-pattern scanning;
- existing deployment/release-document tests;
- focused Market Intelligence backend and frontend tests;
- frontend lint/build where deployment files can affect the image; and
- full CI on the final commit.

Docker Compose rendering and Linux shell behavior will be validated in GitHub
Actions. Because this Windows host has no Docker, local Docker execution will
be labeled **BLOCKED BY LOCAL ENVIRONMENT**, not passed.

Fresh-VPS pull, cold start, DNS, TLS, firewall, external reachability,
restart persistence, backup/restore, and natural scheduled operation are
**REQUIRES VPS VALIDATION**.

## 15. Acceptance criteria

Preparation is complete only when:

1. one official digest-pinned GHCR deployment path is documented;
2. every production image maps to a full Git SHA and recorded digest;
3. the four Compose files render together with safe example inputs;
4. the production template contains no secret and rejects unsafe placeholders;
5. `.env.docker` remains ignored and untracked;
6. `ENABLED_MARKETS=US` and the backup profile are enforced;
7. PostgreSQL, Redis, backend, frontend, US workers, Beat, and Caddy startup and
   health order are explicit;
8. migration, first Yahoo ingestion, snapshot/API/frontend verification,
   restart, backup, restore, rollback, and disaster recovery commands are exact;
9. every required external input and credential is classified;
10. local/CI evidence is distinguished from VPS-only evidence;
11. no Market Intelligence or frontend product behavior changes;
12. focused regression tests and final CI pass;
13. no Critical vulnerability or committed secret is introduced; and
14. the final report states **Real Production Deployment Pending**.

## 16. Required external inputs

Before real deployment, the operator must provide:

- VPS provider, approved Ubuntu host, public IPv4 and optional IPv6 address;
- SSH administrator identity, SSH public key, and allowed management source
  addresses;
- domain/subdomain and DNS-provider access;
- GitHub/GHCR username plus a read-only package token if images are private;
- reviewed backend and frontend digest references plus their full Git SHA;
- random PostgreSQL, server-auth, and session-signing secrets;
- off-host encrypted backup destination and credentials;
- notification/monitoring destination; and
- an approved deployment and rollback window.

LLM, news, social, assistant, and unrelated provider keys are not required for
the Market Intelligence MVP deployment and remain empty.

## 17. VPS capacity baseline

The recommended first host remains Ubuntu 24.04 LTS, 4 vCPU, 8 GB RAM, and an
80 GB SSD. The stack runs PostgreSQL, Redis, two web layers, Caddy, multiple
Celery processes, and the US data/market queues simultaneously; 8 GB provides
reasonable headroom below the Compose resource ceilings and for data refresh
bursts.

The minimum remains 2 vCPU, 4 GB RAM, and a 40 GB SSD with
`WEB_CONCURRENCY=2`, conservative worker concurrency, and swap configured for
emergency pressure rather than normal use. It is suitable only for evaluation
or light single-user operation and requires close memory/disk observation.

Storage must support Docker layers, PostgreSQL growth, Redis AOF, seven local
dumps, logs, and temporary restore space. Production capacity review must alert
before either disk or inode use reaches 80%.
