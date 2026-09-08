# Production Readiness Checklist

## Status boundary

**Production Deployment Preparation Complete** is the target for this branch.
**Real Production Deployment Pending** remains true until every applicable VPS,
DNS, credential, acceptance, and observation row below is evidenced on the real
host. A static test or green CI run cannot satisfy a VPS row.

Recommended host: **Ubuntu 24.04 LTS / 4 vCPU / 8 GB RAM / 80 GB SSD**.
Minimum light-use host: **Ubuntu 24.04 LTS / 2 vCPU / 4 GB RAM / 40 GB SSD**.
The minimum uses `WEB_CONCURRENCY=2` and requires close memory/disk monitoring.

Status meanings: COMPLETE is supported by repository evidence; READY means the
procedure/configuration exists but still needs execution; EXTERNAL means an
operator-supplied resource is absent; OBSERVE means only elapsed production
operation can prove it.

## Already Complete

| Status | Item | Evidence / command | Owner | Acceptance condition |
|---|---|---|---|---|
| COMPLETE | Approved deployment design | `docs/superpowers/specs/2026-09-07-production-deployment-preparation-design.md` | Engineering | Scope fixes digest-only GHCR path and US market |
| COMPLETE | Implementation plan | `docs/superpowers/plans/2026-09-08-production-deployment-preparation.md` | Engineering | Every task has files, reason, validation, rollback |
| COMPLETE | Immutable release inputs | `docker-compose.release.yml`; deployment config tests | Engineering | Backend/frontend require full `@sha256:` refs and full Git SHA |
| COMPLETE | Safe environment template | `.env.production.example` | Engineering | Placeholder-only; `ENABLED_MARKETS=US`; backup profile enabled |
| COMPLETE | Environment preflight | `python3 backend/scripts/validate_production_deployment.py --env-file .env.docker` | Engineering | Rejects placeholders, unsafe secrets, tags, wrong origin/market/profile |
| COMPLETE | Backup implementation | `scripts/production/postgres-backup.sh` | Engineering | Atomic verified custom dump, checksum, retention, last-good preservation |
| COMPLETE | Caddy container health probe | `docker-compose.https.yml` | Engineering | Probes Caddy's internal `:8080/health` endpoint |
| COMPLETE | CI deployment gate defined | `.github/workflows/ci.yml` job `production-deployment-config` | Engineering | Ubuntu renders four overlays and syntax-checks backup script |
| COMPLETE | Product behavior unchanged | `git diff --name-only main...HEAD` | Engineering | No Market Intelligence domain/API/frontend product source edits |

## Can Complete Locally

| Status | Item | Evidence / command | Owner | Acceptance condition |
|---|---|---|---|---|
| READY | Deployment contract tests | `pytest backend/tests/unit/test_production_deployment_config.py backend/tests/unit/test_release_docs.py -q` | Engineering | All deterministic tests pass |
| READY | Market Intelligence regression | Established focused backend suite | Engineering | Zero branch-caused failure |
| READY | Frontend regression/build | `npm ci`, focused tests, `npm run lint`, `npm run build` | Engineering | Tests/build pass; no new lint issue |
| READY | Dependency assessment | `pip check`; `npm audit --json`; `npm audit --omit=dev --json` | Engineering | Zero Critical; other advisories classified, no blind fix |
| READY | Secret scan | Scan branch diff plus `git ls-files .env.docker` | Engineering | No private key/token/usable secret; env file absent from index |
| READY | Image pinning scan | Inspect release Compose and production examples | Engineering | No production tag/`latest`; every app image is digest-pinned |
| READY | Git hygiene | `git diff --check`; clean worktrees; compare main/origin | Engineering | Branch clean; primary main clean and aligned |
| BLOCKED BY LOCAL ENVIRONMENT | Native Compose render and Bash wrappers | Run on Ubuntu CI | Engineering | Windows lacks Docker/Linux executable semantics; do not hide tests |

## Requires VPS

Every row is **REQUIRES VPS VALIDATION**.

| Status | Item | Evidence / command | Owner | Acceptance condition |
|---|---|---|---|---|
| EXTERNAL | Ubuntu host hardening | Runbook steps 2–3 | Operator | Patched OS, key-only approved SSH, time sync, least-exposure firewall |
| EXTERNAL | Clean-host GHCR pull | Runbook steps 5–7 | Operator | Both exact digests pull and OCI revisions equal full Git SHA |
| EXTERNAL | Four-layer cold start | `"${COMPOSE_PROD[@]}" up` stages | Operator | Only expected services start; no app DB/broker port exposed |
| EXTERNAL | PostgreSQL persistence | Migration + restart checks | Operator | Alembic head/data survive container restart |
| EXTERNAL | Redis persistence/fallback | `redis-cli ping`, restart, readiness response | Operator | Broker/cache behavior and AOF survive restart; degradation is honest |
| EXTERNAL | Celery worker and Beat | `inspect ping`, `active_queues`, logs | Operator | US queues present, Beat active, no other market worker |
| EXTERNAL | Yahoo ingestion | Manual named task on `market_jobs_us` | Operator | `SUCCEEDED`, published, 12 symbols, no rejection/missing symbol |
| EXTERNAL | Atomic stable publication | Data Health before/after controlled non-success evidence | Engineering/operator | PARTIAL/FAILED cannot replace last complete pointer |
| EXTERNAL | API and UI data | Authenticated latest/history/health plus browser | Operator | API identities agree and UI renders production data |
| EXTERNAL | Health probes | Internal `/livez`, `/readyz`; public `/nginx-health` | Operator | Expected HTTP/state semantics observed |
| EXTERNAL | Restart test | Runbook step 14 | Operator | Schema, Redis, stable snapshot, services survive restart |
| EXTERNAL | Backup and restore drill | One-shot dump, checksum, catalog, disposable DB restore | Operator | Verified restore includes schema and Market Intelligence state |
| EXTERNAL | Rollback drill | Previous release manifest and matched backup | Operator | Previous image pair can be restored without unsafe live downgrade |
| EXTERNAL | Disaster-recovery drill | Fresh host + off-host artifacts | Operator | Accepted release and latest valid backup recover independently |

## Requires Domain / DNS

Every row is **REQUIRES VPS VALIDATION**.

| Status | Item | Evidence / command | Owner | Acceptance condition |
|---|---|---|---|---|
| EXTERNAL | Domain ownership | Registrar/DNS account | User/operator | Approved production FQDN controlled by operator |
| EXTERNAL | A/AAAA records | Public resolver checks | DNS owner | A equals VPS IPv4; AAAA only when IPv6 is reachable |
| EXTERNAL | HTTP/HTTPS reachability | External curl/browser | Operator | Provider firewall and host rules allow only intended 80/443 |
| EXTERNAL | TLS issue/redirect | Caddy logs, `openssl s_client`, HTTP headers | Operator | Trusted certificate, hostname coverage, HTTP redirects to HTTPS |
| OBSERVE | TLS renewal | Caddy logs/monitoring over time | Operator | Renewal succeeds before expiry |

## Requires Credentials

| Status | Item | Evidence / command | Owner | Acceptance condition |
|---|---|---|---|---|
| EXTERNAL | VPS and SSH access | Provider console plus approved SSH key | User | Recovery console and named operator access work |
| EXTERNAL | DNS access | DNS provider role/token | User | Operator can change/rollback records |
| EXTERNAL | Private GHCR read access, if applicable | Host `docker login ghcr.io` | User | Classic PAT has only necessary package read access; not in Compose |
| EXTERNAL | Database secret | `POSTGRES_PASSWORD` in mode-0600 `.env.docker` | User/operator | Random 32+ chars, not reused, recoverable from secret manager |
| EXTERNAL | Login secret | `SERVER_AUTH_PASSWORD` in mode-0600 `.env.docker` | User/operator | Independent random 32+ chars |
| EXTERNAL | Session signing secret | `SERVER_AUTH_SESSION_SECRET` in mode-0600 `.env.docker` | User/operator | Independent random 32+ chars and not equal to login secret |
| EXTERNAL | Off-host backup credential | Backup agent/secret manager, not Compose unless required | User/operator | Least privilege, encrypted destination, restore access tested |
| EXTERNAL | Monitoring destination | Alert/log service credential | User/operator | Test alert reaches the responsible person |

## Post-deployment Monitoring

Every row is **REQUIRES VPS VALIDATION**.

| Status | Item | Evidence / command | Owner | Acceptance condition |
|---|---|---|---|---|
| OBSERVE | First 24 hours | Host/container/API/Data Health dashboard | Operator | No crash loop, exhaustion, or unexplained failure streak |
| OBSERVE | Natural daily Yahoo run | Beat/worker audit and published pointer | Operator | Scheduled completed-session run succeeds and publishes once |
| OBSERVE | GitHub Yahoo canary | Scheduled workflow run | Engineering | Read-only contract run completes naturally after deployment |
| OBSERVE | Backup retention cycle | Seven-plus scheduled intervals | Operator | New verified dumps prune only beyond retention; off-host copies exist |
| OBSERVE | Resource capacity | CPU/RAM/disk/inodes/DB/Redis/queue metrics | Operator | Alerts work; disk and inode usage stay below 80% |
| OBSERVE | TLS lifecycle | Certificate expiry/renewal alert | Operator | Renewal evidence and alert path recorded |
| OBSERVE | Security/dependency cadence | Separate review workflow | Engineering | Advisories triaged; fixes occur only in reviewed dependency PRs |

## Launch decision

Go live only when all applicable EXTERNAL rows through backup/restore are
accepted and an explicit rollback owner is present. Until then the correct
state is **Real Production Deployment Pending**, never “Production deployed.”
