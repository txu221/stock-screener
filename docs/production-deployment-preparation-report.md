# Production Deployment Preparation Report

## Final status boundary

Repository-level target:

- **Production Deployment Preparation Complete** — subject to the final Ubuntu
  pull-request CI run reported with the delivery.
- **Real Production Deployment Pending** — no VPS, domain, DNS change, public
  TLS certificate, or production credential exists in this phase.
- **Long-term Production Monitoring Pending** — natural runs and elapsed-time
  behavior require a real deployed service.

This phase adds no product feature and does not modify Market Intelligence
ingestion, validation, metrics, ranking, snapshots, publication semantics, API
behavior, or frontend product behavior.

## Production deployment architecture

The only formal path is Ubuntu 24.04 LTS plus Docker Compose, pulling one
reviewed backend/frontend pair from GHCR by immutable digest. The stack always
layers, in order:

1. `docker-compose.yml`
2. `docker-compose.prod.yml`
3. `docker-compose.release.yml`
4. `docker-compose.https.yml`

Caddy alone exposes 80/443. It forwards to nginx, which serves the React build
and proxies `/api` to FastAPI. PostgreSQL 16, Redis 7 AOF, FastAPI, all Celery
processes, and the backup service remain private. The first deployment is
strictly `ENABLED_MARKETS=US`; the wrapper adds only `market-us` to the required
`backup` profile.

## Image and release identity

Production requires complete values of this form:

```text
BACKEND_IMAGE_REF=ghcr.io/<owner>/stockscreenclaude-backend@sha256:<64-hex>
FRONTEND_IMAGE_REF=ghcr.io/<owner>/stockscreenclaude-frontend@sha256:<64-hex>
RELEASE_GIT_SHA=<40-hex>
```

The validator rejects tags and non-GHCR refs. The Runbook pulls the two exact
digests and verifies each image's `org.opencontainers.image.revision` equals
the full source SHA. `latest`, branch tags, and semantic tags are discovery
aids only. An external candidate/current/previous release manifest records the
pair without secrets. Rollback promotes the previous pair together and never
automatically downgrades the live database.

## Files added or changed

### Configuration-only and Compose changes

- `.env.production.example` — placeholder-only production contract.
- `docker-compose.yml` — explicit existing admin/GitHub data token wiring and
  verified bounded backup service.
- `docker-compose.prod.yml` — backup health and backend-to-frontend healthy
  startup ordering.
- `docker-compose.release.yml` — mandatory digest refs, full revision labels,
  no builds, always-pull policy, and one formal production path.
- `docker-compose.https.yml` — Caddy internal health check and healthy frontend
  dependency.

### Deployment scripts

- `backend/scripts/validate_production_deployment.py` — fail-closed env, Git
  safety, image, market/profile, security setting, resource, and four-file
  Compose validation.
- `scripts/production/postgres-backup.sh` — custom-format `pg_dump`, catalog
  validation, SHA-256 sidecar, atomic publish, seven-dump retention, last-good
  preservation, unique concurrent container filenames, and one-shot mode.

### CI and deterministic contracts

- `.github/workflows/ci.yml` — Ubuntu `production-deployment-config` gate before
  image publication.
- `backend/tests/unit/test_production_deployment_config.py` — deployment/env/
  Compose/backup/CI contracts.
- `backend/tests/unit/test_release_docs.py` — digest-only Runbook/readiness/input
  documentation contracts.

### Documentation

- approved design and implementation plan under `docs/superpowers/`;
- `docs/runbooks/production-deployment.md`;
- `docs/production-readiness-checklist.md`;
- `docs/production-required-inputs.md`;
- this report; and
- aligned `README.md`, `docs/INSTALL_DOCKER.md`, and `docs/ENVIRONMENT.md`.

No dependency manifest/lockfile, database model/migration, backend application
module, or frontend source module changed.

## Environment and required external inputs

`.env.docker` remains Git ignored, untracked, mode 0600 on the target host, and
must pass the validator. Required values include the domain and matching HTTPS
origin, PostgreSQL identity/secret, independent server login/session secrets,
both digest refs, full Git SHA, fixed US market/profile/timezone/source mode,
resource settings, and backup timing/retention. `GITHUB_DATA_TOKEN` and
`ADMIN_API_KEY` remain optional existing capabilities.

Actual deployment additionally needs VPS/IP/SSH and provider firewall access,
domain/DNS control, private-GHCR read credentials if applicable, encrypted
off-host backup storage and credential, a monitoring/notification destination,
and a deployment/rollback window. Yahoo sector ingestion needs no API key.
News, AI, LLM, Options Flow, multi-provider, and non-US credentials are not
required.

## Exact first-deployment sequence

The complete copy/pasteable procedure is in
[`runbooks/production-deployment.md`](runbooks/production-deployment.md). It has
one ordered path:

1. freeze inputs, full SHA, and two image digests;
2. provision/harden Ubuntu, SSH, time sync, provider firewall, and UFW;
3. install Docker Engine/Compose from Docker's official Ubuntu repository;
4. check out the exact detached Git SHA;
5. authenticate the Docker host to private GHCR only when required;
6. create mode-0600 `.env.docker`, verify Git ignore, and run the validator;
7. render the four overlays, pull digests, verify OCI revision labels, and
   write the candidate manifest;
8. start and verify PostgreSQL/Redis;
9. run explicit `alembic upgrade head` and `alembic current --verbose`;
10. start backend/frontend, US workers, Beat, and backup service in health
    order;
11. confirm DNS, start Caddy, and verify public HTTPS;
12. dispatch the existing Yahoo sector task on `market_jobs_us`;
13. verify `SUCCEEDED`, publication, latest/history/Data Health APIs, and UI;
14. verify restart persistence;
15. make/checksum/catalog/restore a backup and copy it off-host;
16. promote the candidate manifest and begin monitoring.

`docker compose down -v` is explicitly forbidden. An emergency same-SHA local
build is documented only as an incident fallback, not a second production
architecture.

## Migration, Yahoo, snapshot, and health procedures

Migrations run once with the pinned backend image before workers. Failure is a
hard stop; no automatic Alembic downgrade is recommended. FastAPI's existing
serialized startup migration remains defense in depth.

The first data run calls
`app.tasks.market_intelligence_tasks.calculate_sector_intelligence_snapshot`
on `market_jobs_us`. Acceptance requires `SUCCEEDED`, `published=true`, SPY plus
11 sectors, no missing/rejected symbol, and agreement among latest, history,
Data Health, and frontend identities. PARTIAL/FAILED remain audit results and
must not move the complete published pointer; this existing invariant is not
changed here.

Internal `/livez` proves process liveness. Internal `/readyz` makes PostgreSQL/
schema fatal and reports Redis or snapshot degradation honestly. Public
`/nginx-health` verifies the edge path. Caddy has a separate internal
`:8080/health` container check.

The existing GitHub Yahoo canary remains scheduled at 23:30 UTC Monday-Friday
and is read-only. It does not replace Celery Beat's production publication run.

## TLS, backup, rollback, and disaster recovery

The operator must prove public A/AAAA resolution, only reachable IPv6 records,
provider/host firewall behavior, Caddy ACME issuance, trusted hostname coverage,
HTTP-to-HTTPS redirect, external reachability, and eventual renewal.

Backups use PostgreSQL custom format, validate with `pg_restore --list`, publish
with a SHA-256 sidecar, retain seven verified local dumps, and never prune the
last good set after a failed attempt. Acceptance includes a disposable-database
restore plus off-host encrypted copies of dump/checksum/current and previous
release manifests.

Rollback stops Beat/workers, takes evidence and a verified dump, restores the
previous digest pair, checks database compatibility, and re-runs all gates. If
the old image is incompatible with the current schema, the matching pre-deploy
backup is restored to a new database/host; the live database is not blindly
downgraded. Disaster recovery repeats the process on a fresh VPS before DNS
cutover.

## Recommended capacity

- Recommended: Ubuntu 24.04 LTS, 4 vCPU, 8 GB RAM, 80 GB SSD.
- Minimum: Ubuntu 24.04 LTS, 2 vCPU, 4 GB RAM, 40 GB SSD,
  `WEB_CONCURRENCY=2`, light single-user load, and close observation.

The recommended size leaves headroom for PostgreSQL, Redis, FastAPI/nginx/
Caddy, several Celery processes, data-refresh bursts, image layers, logs, seven
dumps, and restore workspace. Alert before disk or inode utilization reaches
80%.

## Local evidence

Observed on Windows against the branch after implementation/review:

- deployment and release contracts: 71 passed;
- focused Market Intelligence backend regression: 343 passed, 16 explicitly
  deselected service/live cases, zero failed;
- frontend Market Intelligence: 37 passed in 11 files;
- frontend lint: zero errors and the same four unrelated warnings on main;
- frontend production build: 2,515 modules transformed successfully;
- `pip check`: no broken requirements;
- high-confidence private-key/AWS/GitHub/Slack/JWT scan: zero hits;
- `.env.docker`: ignored and zero tracked files;
- product source files changed: zero;
- dependency files changed: zero; and
- primary `main` and fetched `origin/main` both remained
  `c5a75c671bd647b7c688cc509b7807ec2cb7af03`, with both worktrees clean at the
  recorded check.

This host has no Docker or POSIX `sh`. Local Compose execution and native Bash
syntax/runtime are therefore **BLOCKED BY LOCAL ENVIRONMENT**. The three known
Bash-wrapper `WinError 193` tests reproduce on unmodified main and remain
unchanged; Ubuntu CI is the authority.

## Dependency and security assessment

No dependency changed. Current registry advisories reproduce identically on
main:

- full frontend graph: 22 total — 1 low, 4 moderate, 17 high, 0 Critical;
- `--omit=dev`: 8 total — 2 moderate, 6 high, 0 Critical.

Production-classified findings are:

| Packages | Severity | Exposure assessment | Follow-up |
|---|---:|---|---|
| `axios` (direct), `follow-redirects`, `form-data` | High / Moderate / High | Application uses a browser same-origin API client with fixed base/paths; Node proxy/redirect/multipart attack paths are not shipped by the nginx runtime. Prototype-pollution cases are not known reachable from an attacker-controlled Axios config, but the direct dependency warrants priority. | Separate tested Axios upgrade PR; do not use blind audit fix. |
| `react-router-dom` (direct), `react-router`, `@remix-run/router` | High | SPA routes use fixed internal destinations or encoded symbols; no arbitrary user-controlled navigation target was found. SSR-only issue is inapplicable, while redirect/XSS advisories still warrant compatibility testing. | Separate React Router upgrade and route/security tests. |
| `lodash` through Recharts | High | Reported vulnerable template/import and unset/omit APIs have no identified attacker-controlled call path in this app. | Upgrade through a reviewed Recharts/lodash dependency PR. |
| `yaml` through Emotion/Babel | Moderate | Build dependency path; the final nginx image contains only static `dist`, not Node packages or a YAML parser. | Resolve during reviewed frontend toolchain refresh. |

The 14 dev-only findings are 1 low, 2 moderate, and 11 high across Babel/humanfs/
AJV and glob/YAML/PostCSS/Rollup/Vite/Undici-related build/test tooling. They are
absent from the final nginx runtime layer. They remain supply-chain/local-build
risk and should be handled in a separate dependency-security PR with build,
test, and browser regression evidence. No `npm audit fix` was run.

## Final review findings

Review found and corrected two deployment-only gaps before closeout:

1. all Compose headers now describe the same four-file formal path, and Caddy
   waits for a healthy frontend which in production waits for a healthy backend;
2. backup filenames now include sanitized container identity, preventing a
   scheduled and operator one-shot backup in the same second from overwriting
   one another.

No big-bang refactor, duplicate data pipeline, product semantic change, new
dependency, production tag use, real credential, or Critical advisory remains.

## REQUIRES VPS VALIDATION

The following are not claimed complete: VPS provisioning/hardening; Docker and
Compose installation; clean-host private GHCR pull; complete cold start;
PostgreSQL/Redis persistence; Linux backup execution; worker/Beat queues;
public firewall behavior; DNS; TLS issuance/redirect/renewal; external access;
live Yahoo ingestion; first published snapshot and UI; production restart;
backup/restore and off-host recovery; rollback/DR drills; first natural Celery
and GitHub canary runs; performance under host load; and long-term monitoring.

There is no known repository-level deployment blocker. Real deployment is
blocked only by the intentionally absent external infrastructure, domain/DNS,
production secrets/image selections, backup target, monitoring destination,
and operator window.
