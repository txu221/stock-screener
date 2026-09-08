# Production Deployment Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the completed Market Intelligence MVP deployable from immutable GHCR image digests on a fresh Ubuntu VPS as soon as the operator supplies infrastructure, DNS, and secrets, without changing product behavior.

**Architecture:** Preserve the existing PostgreSQL/Redis/FastAPI/nginx/Caddy/Celery Compose topology and make the existing four-file HTTPS release stack the single official production path. Add a strict environment validator, digest-only release references, bounded verified PostgreSQL backups, Linux CI rendering, and one exact operator Runbook; keep source builds only as an emergency fallback.

**Tech Stack:** Docker Engine and Compose v2, GHCR OCI images, Ubuntu 24.04 LTS, PostgreSQL 16, Redis 7, FastAPI/Uvicorn, nginx, Caddy 2, Celery, Python 3.11 standard library, pytest, GitHub Actions.

## Global Constraints

- Do not edit Market Intelligence domain, provider, metric, ranking, repository, snapshot, publication, API-business, or frontend-product code.
- The only official production path is `docker-compose.yml` + `docker-compose.prod.yml` + `docker-compose.release.yml` + `docker-compose.https.yml`.
- Production application images must use `ghcr.io/txu221/stockscreenclaude-backend@sha256:<64-hex>` and `ghcr.io/txu221/stockscreenclaude-frontend@sha256:<64-hex>` references; `latest` and bare tags are forbidden deployment inputs.
- `RELEASE_GIT_SHA` must be a full 40-character commit SHA and the release manifest must record both image digests.
- The first deployment is exactly `ENABLED_MARKETS=US`; do not add or enable another market.
- `.env.production.example` contains rejected sentinels only; the real `.env.docker` remains ignored, untracked, mode `0600`, and outside commits.
- PostgreSQL backups retain seven verified daily dumps locally by default and require encrypted off-host replication for disaster recovery.
- Emergency source build is a last-resort operator action, not a second production architecture.
- Do not create real credentials, provision a VPS, modify DNS/firewall, issue TLS, or claim a public deployment.
- Label public network, clean-VPS pull/start, restart, restore, natural schedule, and burn-in checks `REQUIRES VPS VALIDATION`.
- Preserve the three known Windows `WinError 193` Bash-wrapper failures; validate those paths on Ubuntu rather than skipping or weakening tests.
- Add no dependency and do not run `npm audit fix`.

## Scope mapping

| Approved design area | Implemented by |
| --- | --- |
| Digest-only GHCR release identity | Tasks 1, 2, 5 |
| Production environment/secrets contract | Tasks 1, 2, 5 |
| Four-file Compose path and Caddy health | Tasks 2, 4 |
| PostgreSQL backup retention and verification | Task 3 |
| Static validation and Ubuntu evidence | Tasks 1, 4 |
| Fresh VPS through first publish Runbook | Task 5 |
| Readiness/inputs/checklist classification | Task 6 |
| Regression, security, audit, final evidence | Tasks 7, 8 |

---

### Task 1: Production environment contract and validator

**Category:** Configuration-only changes; deployment script.

**Goal:** Define one safe environment contract and reject mutable images, placeholder secrets, wrong origins, non-US markets, or missing backup activation before Compose starts.

**Files:**
- Create: `.env.production.example`
- Create: `backend/scripts/validate_production_deployment.py`
- Create: `backend/tests/unit/test_production_deployment_config.py`

**Modification reason:** `.env.docker.example` mixes local, homelab, optional product, and production examples and currently permits tag-based release inputs. A focused production template plus a standard-library validator turns operator instructions into a deterministic gate without adding dependencies.

**Interfaces:**
- Consumes: UTF-8 `KEY=value` file, repository root, exact four Compose paths.
- Produces: `load_environment(path: Path) -> dict[str, str]`, `validate_environment(values: Mapping[str, str]) -> list[str]`, `validate_git_safety(root: Path, env_path: Path) -> list[str]`, `compose_command(root: Path, env_path: Path) -> list[str]`, and CLI exit 0 only for a safe contract and successful Compose rendering.

**Rollback / failure handling:** Delete the new template, validator, and tests. Validation must fail closed, print field names but never values, and leave the environment file untouched.

- [ ] **Step 1: Write failing environment-contract tests**

Add tests that build a valid in-memory mapping with:

```python
VALID = {
    "DOMAIN": "stocks.acme-finance.com",
    "CORS_ORIGINS": "https://stocks.acme-finance.com",
    "POSTGRES_DB": "stockscanner",
    "POSTGRES_USER": "stockscanner",
    "POSTGRES_PASSWORD": "p" * 32,
    "SERVER_AUTH_PASSWORD": "a" * 32,
    "SERVER_AUTH_SESSION_SECRET": "s" * 64,
    "BACKEND_IMAGE_REF": "ghcr.io/acme/stockscreenclaude-backend@sha256:" + "1" * 64,
    "FRONTEND_IMAGE_REF": "ghcr.io/acme/stockscreenclaude-frontend@sha256:" + "2" * 64,
    "RELEASE_GIT_SHA": "3" * 40,
    "ENABLED_MARKETS": "US",
    "COMPOSE_PROFILES": "backup",
    "POSTGRES_BACKUP_RETENTION_COUNT": "7",
}
```

Assert the valid mapping has no errors. Parametrize failures for every missing
required key, `latest`, tag-only image references, short/placeholder/equal
secrets, non-HTTPS or mismatched CORS, `US,HK`, missing `backup`, retention
below 2, and malformed Git SHA. Assert error text never contains secret values.
Assert `compose_command()` contains the exact four files in approved order and
ends in `config --quiet`.

- [ ] **Step 2: Run the new tests and observe RED**

Run:

```powershell
& '..\..\backend\venv\Scripts\python.exe' -m pytest backend/tests/unit/test_production_deployment_config.py -q
```

Expected: import failure because `validate_production_deployment.py` does not
exist.

- [ ] **Step 3: Implement the standard-library validator**

Use `argparse`, `pathlib`, `re`, and `subprocess`. Require the keys in `VALID`,
match image refs with:

```python
IMAGE_REF_PATTERN = re.compile(
    r"^ghcr\.io/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$"
)
GIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
```

Reject `CHANGE_ME`, `example`, `stockscanner` as the PostgreSQL password,
secrets under 32 characters, equal auth/session secrets, origins other than
exactly `https://{DOMAIN}`, any enabled-market string other than `US`, and a
profile set without `backup`. Require retention count at least 2. Use
`git check-ignore --quiet <env>` and `git ls-files --error-unmatch <env>` to
confirm the real environment is ignored and untracked. Invoke:

```python
[
    "bash", str(root / "scripts/docker-compose-enabled-markets.sh"),
    "--env-file", str(env_path),
    "-f", "docker-compose.yml",
    "-f", "docker-compose.prod.yml",
    "-f", "docker-compose.release.yml",
    "-f", "docker-compose.https.yml",
    "config", "--quiet",
]
```

Support `--skip-compose` only for deterministic validation on hosts without
Docker; the official Runbook and Ubuntu CI must not use that flag.

- [ ] **Step 4: Add the production template**

Write commented, non-secret sentinels for required values and safe defaults:

```dotenv
DOMAIN=CHANGE_ME_DOMAIN
CORS_ORIGINS=https://CHANGE_ME_DOMAIN
BACKEND_IMAGE_REF=CHANGE_ME_BACKEND_DIGEST_REF
FRONTEND_IMAGE_REF=CHANGE_ME_FRONTEND_DIGEST_REF
RELEASE_GIT_SHA=CHANGE_ME_FULL_GIT_SHA
POSTGRES_DB=stockscanner
POSTGRES_USER=stockscanner
POSTGRES_PASSWORD=CHANGE_ME_RANDOM_32_CHARS
SERVER_AUTH_PASSWORD=CHANGE_ME_RANDOM_32_CHARS
SERVER_AUTH_SESSION_SECRET=CHANGE_ME_INDEPENDENT_RANDOM_64_CHARS
ENABLED_MARKETS=US
COMPOSE_PROFILES=backup
POSTGRES_BACKUP_RETENTION_COUNT=7
POSTGRES_BACKUP_INTERVAL_SECONDS=86400
POSTGRES_BACKUP_INITIAL_DELAY_SECONDS=300
CELERY_TIMEZONE=America/New_York
WEB_CONCURRENCY=2
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=5
MARKET_DATA_SOURCE_MODE=github_first
SERVER_AUTH_SECURE_COOKIE=true
SERVER_EXPOSE_API_DOCS=false
STATIC_EXPORT_ENABLED=false
GITHUB_DATA_TOKEN=
ADMIN_API_KEY=
```

Document that GHCR authentication belongs to Docker's credential store and is
not an application environment variable.

- [ ] **Step 5: Run GREEN and commit**

Run the test file plus validator against a generated safe temporary fixture
with `--skip-compose`; run `git check-ignore -v .env.docker` and verify
`git ls-files .env.docker` is empty. Commit:

```bash
git add .env.production.example backend/scripts/validate_production_deployment.py backend/tests/unit/test_production_deployment_config.py
git commit -m "feat: validate production deployment inputs"
```

---

### Task 2: Digest-pinned Compose release and edge health wiring

**Category:** Compose changes; configuration-only changes.

**Goal:** Make release images intrinsically immutable, pass existing GitHub/admin settings into containers, and expose Caddy health to Compose.

**Files:**
- Modify: `docker-compose.yml`
- Modify: `docker-compose.release.yml`
- Modify: `docker-compose.https.yml`
- Modify: `backend/tests/unit/test_market_worker_config.py`
- Modify: `backend/tests/unit/test_production_deployment_config.py`

**Modification reason:** The release overlay currently concatenates a mutable
tag, `GITHUB_DATA_TOKEN` and `ADMIN_API_KEY` are defined by application settings
but absent from Compose application environment, and Caddy's existing internal
health endpoint is not a container health check.

**Interfaces:**
- Consumes: validated `BACKEND_IMAGE_REF`, `FRONTEND_IMAGE_REF`,
  `RELEASE_GIT_SHA`, optional `GITHUB_DATA_TOKEN`, optional `ADMIN_API_KEY`.
- Produces: identical backend image for API/workers/Beat, identical frontend
  image for nginx, OCI revision label, Caddy `healthy` state.

**Rollback / failure handling:** Revert only the three Compose files and test
assertions. A missing digest or Git SHA must stop Compose interpolation before
pull/start. Optional tokens default to empty and do not change existing
capabilities.

- [ ] **Step 1: Add failing Compose contract tests**

Assert release anchors contain:

```yaml
image: ${BACKEND_IMAGE_REF:?Set BACKEND_IMAGE_REF to a GHCR digest reference}
labels:
  org.opencontainers.image.revision: ${RELEASE_GIT_SHA:?Set RELEASE_GIT_SHA}
```

and the frontend equivalent. Assert `APP_IMAGE_TAG`, `:latest`, and
`${BACKEND_IMAGE}:` are absent from the release overlay. Assert every backend,
worker, and Beat service still inherits the backend release anchor. Assert the
base app environment forwards `GITHUB_DATA_TOKEN` and `ADMIN_API_KEY`. Assert
Caddy checks `http://127.0.0.1:8080/health`.

- [ ] **Step 2: Run the focused tests and observe RED**

Run:

```powershell
& '..\..\backend\venv\Scripts\python.exe' -m pytest backend/tests/unit/test_market_worker_config.py backend/tests/unit/test_production_deployment_config.py -k "release or github or caddy" -q
```

Expected: new digest/pass-through/health assertions fail.

- [ ] **Step 3: Apply minimal Compose changes**

Replace tag concatenation in the two release anchors with required full refs,
add the revision label, preserve `pull_policy: always`, and retain every
existing service inheritance. Add only:

```yaml
GITHUB_DATA_TOKEN: ${GITHUB_DATA_TOKEN:-}
ADMIN_API_KEY: ${ADMIN_API_KEY:-}
```

to `x-app-env`. Add a Caddy health check using BusyBox `wget`, 30-second
interval, 10-second timeout, three retries, and a 15-second start period.

- [ ] **Step 4: Run GREEN and commit**

Run all deterministic tests in both touched test files except the three known
Windows Bash-execution cases. Commit:

```bash
git add docker-compose.yml docker-compose.release.yml docker-compose.https.yml backend/tests/unit/test_market_worker_config.py backend/tests/unit/test_production_deployment_config.py
git commit -m "feat: pin production compose images by digest"
```

---

### Task 3: Verified bounded PostgreSQL backup service

**Category:** Deployment script; Compose change.

**Goal:** Replace the embedded single-backup shell block with a testable POSIX
script that preserves failed-run safety and retains seven verified daily dumps.

**Files:**
- Create: `scripts/production/postgres-backup.sh`
- Modify: `docker-compose.yml`
- Modify: `docker-compose.prod.yml`
- Modify: `backend/tests/unit/test_production_deployment_config.py`

**Modification reason:** One same-disk dump is insufficient recovery depth,
the current command checks only non-zero size, and the Runbook needs a safe
one-shot pre-deploy backup path using the same implementation as scheduled
backups.

**Interfaces:**
- Consumes: `POSTGRES_DB`, `POSTGRES_USER`, `PGPASSWORD`, retention count,
  interval, initial delay, and optional `POSTGRES_BACKUP_RUN_ONCE=1`.
- Produces: `stockscanner_YYYYmmdd_HHMMSS.dump` plus `.sha256` under
  `/app/data/backups`; only a successful `pg_dump` and `pg_restore --list`
  reaches the final name.

**Rollback / failure handling:** Revert Compose to its prior embedded command
and delete the script. Failed dump/list/hash validation deletes only the
temporary file and never prunes known-good backups. Pruning occurs only after
a verified replacement exists and always keeps the newest retention-count
dumps.

- [ ] **Step 1: Write failing static and behavior-contract tests**

Assert Compose mounts the script read-only, passes all backup settings, and has
a freshness health check. Assert the script includes `set -eu`, restrictive
`umask 077`, temporary filename cleanup trap, `pg_dump --format=custom`,
`pg_restore --list`, SHA-256 generation, post-success pruning, and run-once
support. Assert the retention default is seven.

- [ ] **Step 2: Run tests and observe RED**

Run the backup-focused pytest selection. Expected: missing script and Compose
wiring assertions fail.

- [ ] **Step 3: Implement the POSIX backup loop**

Validate numeric inputs before use. Create `/app/data/backups`, sleep the
configured initial delay unless run-once, write to a unique hidden temporary
path, verify with `pg_restore --list`, atomically rename, write a SHA-256 sidecar,
then prune oldest complete dump/sidecar pairs beyond the retention count. On
SIGTERM/SIGINT, remove only the current temporary file. Loop at the configured
interval or exit after one successful/failing run in run-once mode.

- [ ] **Step 4: Wire Compose and health**

Mount the script into `db-backup`, replace the embedded shell, pass retention,
interval, delay, and run-once values, and retain the existing `backup` profile,
PostgreSQL dependency, volume, logging, and restart policy. The production
health check must require a non-empty dump newer than 26 hours after a
10-minute startup grace period.

- [ ] **Step 5: Verify syntax/tests and commit**

Run pytest locally. Record `sh -n` as Ubuntu CI validation because this Windows
host does not provide the supported shell runtime. Commit:

```bash
git add scripts/production/postgres-backup.sh docker-compose.yml docker-compose.prod.yml backend/tests/unit/test_production_deployment_config.py
git commit -m "feat: harden production database backups"
```

---

### Task 4: Ubuntu production Compose static validation gate

**Category:** CI validation change.

**Goal:** Render the exact official stack and validate deployment scripts on
Linux before images can be published.

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `backend/tests/unit/test_production_deployment_config.py`

**Modification reason:** Local Docker is unavailable and existing CI exercises
the local assistant stack, not the complete prod+release+HTTPS overlays. Image
publication should depend on a successful production deployment contract.

**Interfaces:**
- Consumes: committed example/template, validator, backup script, four Compose
  files, synthetic CI-only environment file removed at job exit.
- Produces: Linux evidence for validator execution, POSIX syntax, exact Compose
  rendering, and configuration image refs containing `@sha256:`.

**Rollback / failure handling:** Remove the dedicated job and its
`publish-images.needs` entry. The job never pulls application images, starts
services, reaches Yahoo, or uses production credentials. Failure blocks image
publication and uploads no secrets.

- [ ] **Step 1: Add a failing workflow-contract test**

Assert `ci.yml` defines `production-deployment-config`, creates a trap-protected
`.env.docker` with CI-only 32/64-character values and two syntactically valid
dummy digests, runs the validator without `--skip-compose`, runs
`sh -n scripts/production/postgres-backup.sh`, removes `.env.docker`, and adds
the job to `publish-images.needs`.

- [ ] **Step 2: Run test and observe RED**

Run the CI-contract test. Expected: missing job assertions fail.

- [ ] **Step 3: Add the isolated CI job**

Use `ubuntu-latest`, `actions/checkout@v4`, a shell trap to delete
`.env.docker`, and CI-only values. Run:

```bash
python backend/scripts/validate_production_deployment.py --env-file .env.docker
sh -n scripts/production/postgres-backup.sh
docker compose --env-file .env.docker \
  -f docker-compose.yml -f docker-compose.prod.yml \
  -f docker-compose.release.yml -f docker-compose.https.yml config --quiet
test -z "$(git ls-files .env.docker)"
```

Make `publish-images` depend on the new job in addition to existing quality
gates.

- [ ] **Step 4: Run GREEN and commit**

Run the deployment-config tests and YAML whitespace checks. Commit:

```bash
git add .github/workflows/ci.yml backend/tests/unit/test_production_deployment_config.py
git commit -m "ci: validate production deployment compose"
```

---

### Task 5: Exact VPS Production Deployment Runbook

**Category:** Documentation change.

**Goal:** Give an operator one linear command sequence from fresh Ubuntu to a
verified first snapshot, with exact stop conditions and no competing primary
path.

**Files:**
- Create: `docs/runbooks/production-deployment.md`
- Modify: `docs/INSTALL_DOCKER.md`
- Modify: `docs/ENVIRONMENT.md`
- Modify: `README.md`
- Modify: `backend/tests/unit/test_release_docs.py`

**Modification reason:** Existing Docker documentation presents local,
homelab, source-build, and tag-based GHCR flows as peers. It lacks a complete
hardening/firewall/migration/first-publish/restart/restore sequence and still
documents `APP_IMAGE_TAG` as the release control.

**Interfaces:**
- Consumes: Ubuntu 24.04, supplied infrastructure/credentials, validated
  `.env.docker`, immutable image refs, exact Compose wrapper.
- Produces: one copy/pasteable production path and a clearly subordinate
  emergency source-build appendix.

**Rollback / failure handling:** Documentation-only rollback restores the old
docs. Every stage has a stop condition: do not advance on failed host hardening,
environment validation, image pull, database health, migration, app health,
first `SUCCEEDED` snapshot, TLS, restart, or backup verification. Failed first
publish leaves the stable pointer unchanged and blocks launch acceptance.

- [ ] **Step 1: Update failing release-document contracts**

Replace tag expectations with digest-reference and full-SHA expectations.
Require the exact four-file command, validator invocation, `ENABLED_MARKETS=US`,
`COMPOSE_PROFILES=backup`, `/livez`, `/readyz`, first Yahoo task, latest/history/
health APIs, restart check, backup run-once, restore drill, rollback manifest,
and every `REQUIRES VPS VALIDATION` category.

- [ ] **Step 2: Run document tests and observe RED**

Run `test_release_docs.py`. Expected: old `APP_IMAGE_TAG=v1.3.0` expectations
fail until docs and tests align with the approved design.

- [ ] **Step 3: Write the unique standard sequence**

Use these exact major stages, in order:

1. collect inputs and freeze release digest pair;
2. provision Ubuntu 24.04 and non-root sudo operator;
3. patch OS, configure SSH keys, disable password/root SSH after access proof,
   configure time sync and firewall ports 22/80/443;
4. install Docker Engine from Docker's official Ubuntu repository and verify
   Engine/Compose versions;
5. checkout the repository revision matching `RELEASE_GIT_SHA`;
6. authenticate to private GHCR with a read-only package token if required;
7. copy `.env.production.example` to `.env.docker`, populate secrets/digests,
   set mode 0600, and validate;
8. resolve/pull/inspect both exact digests and write `release-manifest.env`;
9. start PostgreSQL and Redis and wait healthy;
10. run one-shot Alembic upgrade/current;
11. start backend/frontend, then all US workers/Beat, then Caddy;
12. set/confirm DNS and validate HTTPS;
13. dispatch the existing sector-intelligence task to `market_jobs_us`;
14. verify SUCCEEDED coverage, stable publication, APIs, frontend, and Data
    Health;
15. restart containers and prove PostgreSQL/Redis/snapshot persistence;
16. create/verify a one-shot backup and begin scheduled backup profile;
17. copy dump, SHA, and release manifest off-host;
18. begin post-deployment monitoring.

Define every Compose invocation once as a shell array or documented alias so
operators cannot accidentally omit an overlay. Make explicit that `docker
compose down -v` is destructive and forbidden in normal deploy/rollback.

- [ ] **Step 4: Add exact migration, ingestion, API, and frontend commands**

Use one-shot backend `alembic upgrade head` and `alembic current --verbose`.
Dispatch the named Celery task on `market_jobs_us`, retain its task ID, inspect
the US worker logs/result, authenticate through `/api/v1/auth/login`, then query:

```text
/api/v1/market-intelligence/sectors/latest
/api/v1/market-intelligence/sectors/history
/api/v1/market-intelligence/sectors/health
```

Verify the UI at `/market-intelligence`, `/livez`, `/readyz`, and
`/nginx-health`. Distinguish degraded snapshot readiness from database-fatal
503.

- [ ] **Step 5: Add backup, rollback, and disaster recovery commands**

Document one-shot backup, SHA and `pg_restore --list` verification, seven-dump
local retention, off-host copy, disposable-database restore drill, worker/Beat
pause, previous-digest manifest restore, migration compatibility decision,
fresh-host restore, and DNS cutover. Never recommend an automatic Alembic
downgrade.

- [ ] **Step 6: Preserve emergency source-build fallback only**

Add a final appendix that requires checkout of the same reviewed Git SHA,
locally builds both existing Dockerfiles, records resulting image IDs, and uses
the same database/environment/verification procedure. Label it emergency-only,
non-primary, and unsuitable as a routine release path.

- [ ] **Step 7: Run GREEN and commit**

Run `test_release_docs.py`, link checks via `rg`, and `git diff --check`. Commit:

```bash
git add docs/runbooks/production-deployment.md docs/INSTALL_DOCKER.md docs/ENVIRONMENT.md README.md backend/tests/unit/test_release_docs.py
git commit -m "docs: add production deployment runbook"
```

---

### Task 6: Readiness checklist and external input inventory

**Category:** Documentation change.

**Goal:** Make current evidence, external prerequisites, VPS-only acceptance,
and post-deployment monitoring impossible to confuse.

**Files:**
- Create: `docs/production-readiness-checklist.md`
- Create: `docs/production-required-inputs.md`
- Modify: `backend/tests/unit/test_release_docs.py`

**Modification reason:** The final operator handoff needs explicit ownership and
status, not a prose implication that CI equals a public deployment.

**Interfaces:**
- Consumes: approved design, final Runbook, existing hardening evidence.
- Produces: categorized checklist and secrets/input matrix with owner, timing,
  storage location, rotation expectations, and whether each value enters
  Compose or stays in the Docker host credential store.

**Rollback / failure handling:** Delete the new documents and revert tests.
Unverified items remain unchecked and labeled; no item can transition based
only on static configuration.

- [ ] **Step 1: Add failing document-coverage assertions**

Require the checklist categories `Already Complete`, `Can Complete Locally`,
`Requires VPS`, `Requires Domain / DNS`, `Requires Credentials`, and
`Post-deployment Monitoring`; require the exact recommended/minimum VPS sizes,
all external inputs from the design, no LLM/news key requirement, and explicit
`Real Production Deployment Pending` status.

- [ ] **Step 2: Write both documents**

For each checklist row include status, evidence/command, owner, and acceptance
condition. For each input include required/optional classification, example
format rather than value, where it is stored, who supplies it, exposure scope,
and rotation/recovery note. Include VPS/IP/SSH/DNS/domain, GHCR read token when
private, three application/database secrets, two digest refs, Git SHA, off-host
backup destination/credential, monitoring destination, and deployment window.

- [ ] **Step 3: Run GREEN and commit**

```bash
git add docs/production-readiness-checklist.md docs/production-required-inputs.md backend/tests/unit/test_release_docs.py
git commit -m "docs: record production readiness inputs"
```

---

### Task 7: Local regression, security, and static verification

**Category:** Verification only; no planned product change.

**Goal:** Prove all currently executable gates without hiding Windows limits or
claiming VPS evidence.

**Files:**
- Modify only if a deployment-scope defect is exposed, always with a preceding
  failing deployment test.

**Modification reason:** Final evidence must cover configuration, product
regression, build, secrets, dependency risk, and repository hygiene.

**Rollback / failure handling:** If a new failure originates in this branch,
fix only its deployment root cause through red-green-refactor. If unrelated or
environmental, reproduce on `main`, record it, and do not skip/xfail/disable it.
Stop on any Critical advisory or secret finding.

- [ ] **Step 1: Run deployment and release tests**

```powershell
& '..\..\backend\venv\Scripts\python.exe' -m pytest backend/tests/unit/test_production_deployment_config.py backend/tests/unit/test_release_docs.py -q
```

- [ ] **Step 2: Run Market Intelligence backend regression**

Run the established deterministic Market Intelligence domain, repository,
read-service, use-case, task, endpoint, migration, and integration-without-live-
services selection. Require zero new failures.

- [ ] **Step 3: Run frontend regression and build**

Run `npm ci`, the 11 Market Intelligence test files, `npm run lint`, and
`npm run build`. Require all focused tests/build to pass; preserve the known
four unrelated lint warnings if unchanged.

- [ ] **Step 4: Run dependency checks**

Run `pip check`, `npm audit --json`, and `npm audit --omit=dev --json`. Require
zero Critical findings and compare all other counts to the recorded baseline;
do not run an automatic fix.

- [ ] **Step 5: Run secret and image-reference scans**

Scan changed files for PEM private keys, AWS access keys, GitHub tokens, Slack
tokens, JWT-like secrets, and non-sentinel credentials. Assert release Compose
contains no `latest` or tag concatenation and every production example image
uses `@sha256:`.

- [ ] **Step 6: Verify environment and Git hygiene**

Run `git check-ignore -v .env.docker`, require `git ls-files .env.docker` empty,
run `git diff --check`, inspect `git diff --stat main...HEAD`, and confirm the
primary main checkout remains clean/aligned.

---

### Task 8: CI evidence and deployment-preparation closeout

**Category:** CI validation; documentation change.

**Goal:** Capture authoritative Ubuntu validation and publish an honest final
preparation report while leaving public deployment pending.

**Files:**
- Create: `docs/production-deployment-preparation-report.md`
- Modify: `docs/superpowers/plans/2026-09-08-production-deployment-preparation.md` (checkboxes/evidence only)

**Modification reason:** The final handoff must state what is proven locally or
in CI, what requires a VPS/domain/credentials, and whether any deployment
blocker remains.

**Interfaces:**
- Consumes: final branch SHA, local outputs, Ubuntu CI run, workflow/config
  evidence.
- Produces: final state, file list, architecture, release strategy, exact
  sequence, environment/input list, migration/ingestion/snapshot/health/TLS/
  backup/rollback/DR summaries, VPS sizing, checklist status, blockers, and
  remaining VPS validation.

**Rollback / failure handling:** Delete the report if evidence is incomplete.
Do not mark preparation complete until the final SHA has green CI, zero new
Market Intelligence regressions, zero Critical findings, no committed secret,
and clean branch/main worktrees. A red CI job remains a blocker.

- [ ] **Step 1: Commit final local evidence**

Update plan checkboxes with exact command results and write the report using
only observed evidence. Commit:

```bash
git add docs/superpowers/plans/2026-09-08-production-deployment-preparation.md docs/production-deployment-preparation-report.md
git commit -m "docs: report production deployment readiness"
```

- [ ] **Step 2: Run Ubuntu CI for the final SHA**

Push the phase branch and create/update its pull request only as needed to run
the standard `pull_request` workflow. Require backend quality gates, all four
backend shards, frontend tests/smoke, assistant Compose smoke, and the new
production deployment config job to pass. Image publication is correctly
skipped on a pull request.

- [ ] **Step 3: Review final diff and state**

Confirm no application-domain/frontend-product file changed, no dependency file
changed, `.env.docker` is absent/untracked, branch is clean, primary `main` is
clean and still equals `origin/main`, and the feature branch remains separate.

- [ ] **Step 4: Stop at the approved terminal state**

Report:

```text
Production Deployment Preparation Complete
Real Production Deployment Pending
```

List every `REQUIRES VPS VALIDATION` item and any true blocker. Do not merge,
deploy, provision, change DNS, issue TLS, generate real credentials, or begin a
new product phase.
