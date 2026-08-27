# Market Intelligence Engine Phase 2 Integration Report

**Date:** 2026-08-27  
**Branch:** `feat/market-intelligence-engine`  
**Phase 1 base:** `7627ac7d2ff47cdc5b15e4f7f2a0be84330a5c36`  
**Phase 2 status:** `PHASE 2 STILL BLOCKED — GITHUB EXECUTION ENVIRONMENT`

Phase 2 added repeatable production-integration validation around the Phase 1
sector-intelligence slice. The live Yahoo provider and every service-independent
path were exercised. Real PostgreSQL, Redis, and Celery worker execution remain
`BLOCKED_BY_ENVIRONMENT`; no SQLite or mock result is presented as a substitute.
No Phase 1 financial semantics or production code changed.

## 1. Environment and infrastructure

The host is Windows and has no usable Docker engine, WSL distribution,
`psql`, `redis-server`, or `redis-cli`. Docker Compose therefore could not start
the repository's existing PostgreSQL 16 and Redis 7 services. No system package,
service, WSL distribution, Docker component, PostgreSQL, Redis, or host setting
was installed or changed.

The existing production infrastructure was inspected and retained:

- Compose uses `postgres:16-alpine` and `redis:7-alpine`;
- the existing Celery application, `market_jobs_us` queue, SQLAlchemy Unit of
  Work, Alembic migration, `FeatureRun`, and `FeatureRunPointer` remain the only
  runtime path;
- installed Python clients are psycopg2 2.9.12, SQLAlchemy 2.0.25, Alembic
  1.14.1, redis-py 5.0.1, and Celery 5.3.4;
- PostgreSQL server version and Redis server version are unavailable because no
  server could be reached.

The opt-in service fixtures require explicit URLs and refuse unsafe
substitution. PostgreSQL checks accept only `PHASE2_POSTGRES_URL` whose database
name contains `phase2` or `test`, plus `RUN_MARKET_INTELLIGENCE_POSTGRES=1` and
the exact acknowledgement `PHASE2_ALLOW_DESTRUCTIVE_POSTGRES_TESTS=1`. They do
not fall back to the application's `DATABASE_URL`, and the repository-wide
PostgreSQL test fixture must remain disabled. A SQLite URL fails the environment
contract. Live Yahoo requires `RUN_MARKET_INTELLIGENCE_LIVE=1`. Service URLs
drop credentials, query strings, and fragments in diagnostics.

## 2. PostgreSQL migration and compatibility

The real-PostgreSQL suite was collected as eight explicit environment skips on
this host. Its migration-module smoke check applies the `20260826_0031`
`upgrade()`/`downgrade()` operations to a generated schema through Alembic
`Operations` and inspects the resulting Phase 1 schema:

```text
20260823_0030 -> upgrade 20260826_0031
              -> downgrade 20260823_0030
              -> re-upgrade 20260826_0031
```

It inspects all four Market Intelligence tables, columns, types, primary and
foreign keys, unique/check constraints, indexes, nullability, and defaults. It
also seeds predecessor `FeatureRun`/pointer state and verifies that state remains
readable after the module round trip. This is not an `alembic.command` traversal
from the repository's real `20260823_0030` schema and cannot satisfy the required
production migration acceptance criterion by itself. Real committed
upgrade/downgrade/re-upgrade, `alembic_version`, `env.py`, and existing-database
compatibility results are therefore `BLOCKED_BY_ENVIRONMENT`, not passed.

## 3. Transaction rollback and concurrent publication

The opt-in PostgreSQL tests use independent sessions, non-empty audit/bar/
rejection/snapshot evidence, and synchronization barriers. They cover:

- failure after candidate persistence but before pointer update;
- failure after pointer mutation but before commit;
- concurrent same-day revisions;
- a newer date racing an older backfill;
- a newer run starting first but committing after the older run;
- a same-day idempotency-key insertion race;
- same-day revision ordering and old-date backfill behavior.

The required observations are no orphan audit/bar/rejection/snapshot rows,
consistent `FeatureRun` state, rollback of pointer mutation, history/pointer
winner agreement through the repository history reader, and a latest pointer that never moves to an older trading
date. The harness is present and locally gates correctly, but all real
PostgreSQL rollback, advisory-lock, row-lock, monotonicity, revision, backfill,
and concurrency claims remain `BLOCKED_BY_ENVIRONMENT`.

Sequential rollback/publication behavior continues to pass in the existing
Phase 1 and Phase 2 SQLite test harness. That result validates deterministic
application semantics only; it is not PostgreSQL transaction evidence.

## 4. Redis and Celery

The service-independent task registration assertion passed and confirms
`calculate_sector_intelligence_snapshot` is registered on `market_jobs_us`.
The opt-in suite also contains Redis ping/isolated-key cleanup and a real-worker
one-shot/idempotency check against explicit PostgreSQL and Redis URLs. Every
worker run receives a UUID queue, the readiness ping targets that exact worker
node, broker/result Redis DBs must differ, and task result keys are forgotten.

Local result: one registration test passed and two real-service checks skipped.
Redis connectivity, real Celery dependency injection, DB session creation,
provider call, run/canonical/snapshot persistence, pointer/Data Health results,
and same-input worker idempotency are `BLOCKED_BY_ENVIRONMENT`.

The task currently declares no Celery `autoretry_for` policy. Consequently a
real broker/worker retry sequence was not validated and remains a production
risk to resolve through observation or a separately specified retry policy; it
was not changed speculatively in Phase 2.

## 5. Yahoo live validation

The opt-in live check called the existing Yahoo `BulkDataFetcher` once for the
exact fixed universe:

```text
SPY XLC XLY XLP XLE XLF XLV XLI XLB XLRE XLK XLU
```

Observed on 2026-08-27:

| Observation | Result |
|---|---:|
| Target completed session | 2026-08-26 |
| Requested / returned | 12 / 12 |
| Missing / symbol failures | 0 / 0 |
| Bars per symbol | 125 |
| Canonical bars | 1,500 |
| Earliest / latest date | 2026-02-27 / 2026-08-26 |
| Rejected rows | 0 |
| Yahoo fetch | 3.6034 s |
| Validation and normalization | 0.0151 s |
| Metric calculation | 0.0036 s |
| Provider-to-candidate total | 8.1873 s |
| Candidate result | SUCCEEDED, 12 snapshots |

Freshness is derived per symbol: all 12 canonical symbol histories must end on
the target session. A stale target for even one ETF produces a STALE summary and
identifies that symbol; an explicit future/uncompleted target is rejected.
No raw provider payload, cookie, credential, provider-controlled error message,
or vendor response was committed.
A Redis connection timeout was logged by an existing cache path because Redis
was absent; the provider request itself completed successfully.

## 6. Manual data reconciliation

SPY, XLK, XLE, and XLU were followed from raw Yahoo OHLC/Adj Close/volume through
an independently recomputed adjustment factor/adjusted OHLC/volume comparison
and then independently recomputed metrics.
All adjustment factors in the inspected session were 1.0. Representative
2026-08-26 evidence follows; values are retained at provider/test precision.

| Symbol | Raw O/H/L/C | Adj Close | Volume | Return 1D / 5D / 20D / 60D | RVOL20 | Pressure 1D | CMF20 proxy |
|---|---|---:|---:|---|---:|---:|---:|
| SPY | 764.72998 / 767.34998 / 763.92999 / 766.08002 | 766.08002 | 28,459,700 | 0.000222 / -0.003875 / 0.050202 / 0.012542 | 0.635938 | 0.257331 | 0.023989 |
| XLK | 180.71001 / 183.35001 / 180.71001 / 182.84000 | 182.84000 | 4,662,700 | 0.006053 / -0.004356 / 0.097677 / -0.064886 | 0.608391 | 0.613629 | 0.056232 |
| XLE | 61.43000 / 63.02000 / 61.31000 / 62.43000 | 62.43000 | 24,394,100 | 0.005962 / -0.018087 / 0.064450 / 0.097386 | 0.929472 | 0.309941 | 0.206789 |
| XLU | 43.31000 / 43.71000 / 43.28000 / 43.51000 | 43.51000 | 12,240,200 | 0.004618 / -0.011586 / -0.031173 / 0.015959 | 0.593185 | 0.069765 | -0.082827 |

The independently recomputed sector relative returns also matched the production
snapshot. For example, XLK relative returns versus SPY were 0.005831, -0.000482,
0.047475, and -0.077428 for 1/5/20/60 sessions. SPY relative-return fields are
correctly unavailable because SPY is the benchmark, not zero. An initial
diagnostic checker treated them as zero and reported a checker-only mismatch;
after correcting the independent checker to the documented `None` contract, all
four inspected symbols matched. Production code was not changed.

## 7. Completed-session policy

Seven frozen-clock tests passed through the existing US exchange calendar:

- 2026-08-24 during market hours selects 2026-08-21;
- after the 16:30 completion buffer selects 2026-08-24;
- a weekend selects the prior Friday;
- Labor Day selects the prior Friday;
- the 2026-11-27 early close before its 13:30 buffer selects 2026-11-25;
- after that buffer selects 2026-11-27;
- an unfinished current-session provider row is excluded.

No path uses `today - 1 day` as a market-session policy.

## 8. Controlled run-state and pointer semantics

Four production-use-case/UoW tests passed with frozen inputs:

- SUCCEEDED: 12/12 valid, 12 snapshots, pointer moves;
- PARTIAL: one missing/invalid symbol, latest attempt is PARTIAL, pointer and
  `/latest` stay on the preceding SUCCEEDED snapshot;
- FAILED: provider request failure, no fabricated twelve-row rejection set,
  latest attempt is FAILED, pointer and `/latest` remain stable;
- exact rerun: same logical result and no duplicate snapshot/evidence rows.

Data Health was compared to committed audit state for coverage, rejection,
missing-symbol, provider/request status, freshness, versions, latest attempt,
and last complete publication. Latest-attempt truth remains independent of the
latest stable pointer.

These checks exercise production domain/use-case/repository/API code with the
deterministic SQLite test UoW. They do not establish real PostgreSQL transaction
or Celery worker behavior.

## 9. API validation

FastAPI/TestClient checks passed for:

- `GET /api/v1/market-intelligence/sectors/latest`;
- `GET /api/v1/market-intelligence/sectors/history`;
- `GET /api/v1/market-intelligence/sectors/health`.

The checks compare API values/run IDs with committed repository state. Latest
contains 11 sector rows and SPY only as the benchmark, follows the published
pointer, and reports `market_intelligence_v1`. History is chronological and
chooses the latest published revision per session. Health reports the latest
attempt even when it is PARTIAL or FAILED. Repeating the comparison against
committed real-PostgreSQL state is `BLOCKED_BY_ENVIRONMENT`.

## 10. Historical replay and look-ahead prevention

One real Yahoo historical response was reused to replay five completed sessions
in chronological order:

```text
2026-08-20, 2026-08-21, 2026-08-24, 2026-08-25, 2026-08-26
```

This is **historical replay using real provider data**, not five historical
online task executions. Every slice asserted `max(input_date) == snapshot_date`,
therefore also `max(input_date) <= snapshot_date`. Each candidate was SUCCEEDED
with 12 snapshots and `market_intelligence_v1`. The first session had no prior
ranks; each later session had all 66 previous-rank entries and exact
previous/current rank identity checks. Chronological history, latest selection,
rank continuity/change, version, and repeat-run idempotency passed in the
production-use-case SQLite harness. PostgreSQL pointer/history persistence for
the replay remains blocked.

## 11. Performance observations

These are diagnostic observations, not SLOs:

- live Yahoo sample: 12 symbols and 1,500 bars; fetch 3.6034 s, validation and
  normalization 0.0151 s, calculation 0.0036 s, total 8.1873 s;
- deterministic harness sample: 12 symbols and 1,140 input bars; total use-case
  run 0.1797 s, latest API 0.00483 s, history API 0.00409 s.

The latter uses SQLite and is not a PostgreSQL performance benchmark. Real
PostgreSQL persistence time, real worker overhead, and deployed API latency are
`BLOCKED_BY_ENVIRONMENT`.

## 12. Test and regression results

| Suite | Result |
|---|---|
| Phase 1 exact Market Intelligence suite | 123 passed, 2 warnings |
| Phase 2 default integration directory | 39 passed, 12 skipped, 2 warnings |
| Live Yahoo opt-in test | 1 passed |
| PostgreSQL opt-in collection | 8 skipped: service unavailable |
| Redis/Celery checks | 1 passed, 2 skipped: services unavailable |
| Completed-session/freshness tests | 8 passed |
| Runtime semantics/API/replay/performance group | 4 passed |
| Adjacent source-neutral feature-run/UoW group | 113 passed |
| Adjacent provider/calendar/Market-RS group | 103 passed |
| Python compileall | passed |
| `pip check` | No broken requirements found |
| Frontend lint | 0 errors, 4 pre-existing warnings |
| Frontend production build | passed, 2,497 modules transformed |

The full source-neutral backend diagnostic completed with `16 failed, 6359
passed, 21 skipped`. The unit subset retained exactly `13 failed, 6122 passed, 3
skipped`, matching the Phase 1 known unit failure set. The three additional
failures are existing theme-pipeline API integration 503 failures. No Phase 2
test failed.

The frontend full run produced `597 passed, 9 failed` plus the same Windows
doubled-drive-path suite-load failure. The initial `App.static` timeout caused
all nine tests in that file to cascade in the full concurrent run. Immediate
isolated execution passed all 9/9 tests. Phase 1 recorded the same path failure
and eight order/timeout-sensitive `App.static` failures (`598 passed, 8 failed`),
and Phase 2 has no frontend source diff. This evidence supports zero new
frontend regression while preserving the exact observed full-run result.

No new Phase 2 failure was hidden with skip or xfail. The 11 default Phase 2
skips are explicit service/live opt-in gates; the live test was separately
enabled and passed, while the ten service-dependent checks remain blocked.

## 13. Security, dependencies, and scope

- `git diff --check` passed.
- `pip check` passed.
- Dependency manifests and lockfiles have no diff from Phase 1; no dependency
  was added or upgraded.
- A changed-file secret-pattern scan found only redaction-test identifiers and
  security documentation, not a credential or assigned secret.
- `backend/.local/state/gh/device-id` is untracked and absent from the worktree.
- The Phase 1 npm advisory baseline remains 21 advisories because the lockfile
  is unchanged; no `npm audit fix` was run.
- No provider payload, `.env`, database/broker URL, token, cookie, password,
  device ID, or machine-local state was committed.
- No Phase 1 production file, financial formula, universe, API contract,
  frontend, or provider fallback changed. The fixed universe remains SPY plus
  the eleven Sector SPDR ETFs.
- No upstream push, merge, or PR was made. The Phase 2B commits were pushed only
  to the user-owned fork `txu221/stock-screener`, branch
  `feat/market-intelligence-engine`.

`docs/market-intelligence-spec.md` was intentionally not changed because live
validation found no defect in the Phase 1 price basis, return, relative-return,
RVOL, flow-proxy, ranking, version, or publication semantics.

## 14. Remaining blocked items and risks

`BLOCKED_BY_ENVIRONMENT`:

1. PostgreSQL server/version and real migration upgrade/downgrade/re-upgrade;
2. existing-database upgrade compatibility on PostgreSQL;
3. rollback visibility from an independent PostgreSQL session;
4. concurrency Cases A-C, first-publication locking, pointer monotonicity,
   same-day revision, old-date backfill, and unique idempotency race;
5. Redis server/version/connectivity and isolated key cleanup;
6. real Celery worker one-shot, dependency injection, persistence, retry,
   pointer/health result, and repeated-task idempotency;
7. API-to-real-PostgreSQL value comparison and replay persistence;
8. PostgreSQL persistence and deployed API performance timings.

Remaining risks:

- PostgreSQL advisory/row-lock and transactional assumptions are covered by an
  executable harness but have not run against a server;
- broker/worker configuration, delivery behavior, and outage recovery remain
  unobserved;
- there is no explicit Celery automatic retry policy to exercise;
- Yahoo is an external provider whose shape and availability can drift;
- existing backend, frontend, lint, and npm advisory baselines remain and were
  deliberately not repaired in this phase.

## 15. Files and commits

Phase 2 adds one plan, this report, one opt-in live validation script, explicit
pytest markers, a focused `backend/tests/integration/market_intelligence`
package, one PostgreSQL-backed API contract test, and one manual-dispatch
GitHub Actions workflow. It does not add or modify production behavior.

Phase 2 commits:

```text
c91e818b docs: plan market intelligence phase 2 validation
d24b3ae3 test: add phase 2 integration environment gates
4937dea1 test: cover postgres market intelligence publication
cccbc43c test: cover market intelligence service runtime
e388bbb5 test: validate completed sessions and live yahoo data
470740e2 test: validate sector intelligence runtime semantics
97aff611 test: harden phase 2 integration validation
67fb5e5d docs: report market intelligence phase 2 validation
763849be docs: close market intelligence phase 2 checkpoint
```

Phase 2B commits pushed to the user-owned fork only:

```text
b51ce3b8 test: add postgres-backed market intelligence api coverage
a96785bc ci: add market intelligence integration workflow
```

## 16. Phase 2B GitHub Actions validation

The current branch was pushed to the user-owned fork
`https://github.com/txu221/stock-screener`, with upstream kept read-only. The
workflow file is present on `feat/market-intelligence-engine` and is configured
for manual dispatch with `ubuntu-latest`, disposable `postgres:16-alpine` and
`redis:7-alpine` services, migration upgrade/downgrade/re-upgrade checks,
real-PostgreSQL publication/concurrency/API tests, Redis connectivity, and a
separate opt-in Yahoo/Celery job.

An attempt to dispatch the core workflow was rejected by GitHub before a run
was created:

```text
gh workflow run market-intelligence-integration.yml \\
  --repo txu221/stock-screener \\
  --ref feat/market-intelligence-engine \\
  -f run_live_yahoo=false
HTTP 404: workflow market-intelligence-integration.yml not found on the default branch
```

The fork reports `main` as its default branch; the workflow exists only on the
feature branch because this phase forbids merging or opening a PR. GitHub's
workflow-dispatch API requires the workflow to be registered from the default
branch. The fork's workflow listing currently reports zero registered
workflows, and the existing `ci.yml` likewise returns 404 from the Actions
workflow API despite its source file being present. No Actions run ID, run URL,
runner OS, actual PostgreSQL server version, actual Redis server version, or
uploaded artifact therefore exists. The workflow's declared images/runner are
configuration intent only, not execution evidence.

The local re-run after adding the workflow remains deterministic: the complete
Market Intelligence integration directory is `39 passed, 12 skipped, 2
warnings`; the new PostgreSQL-backed API test is one explicit environment skip.
No new Phase 2 failure was observed. PostgreSQL, Redis, Celery, and optional
Yahoo Actions evidence remain `BLOCKED_BY_ENVIRONMENT` /
`PHASE 2 STILL BLOCKED — GITHUB EXECUTION ENVIRONMENT` under the current
no-merge/no-PR constraint.

This is an execution-environment block, not a claim that the workflow or its
real-service tests passed. To clear it, a later explicitly authorized action
must make the workflow visible on the fork's default branch (or enable a
repository Actions configuration that registers fork workflows); that action
was not taken here.

## 17. Final review and recommended next phase

The independent final review found two Critical and ten Important concerns in
the first harness revision. The Critical database-target and shared-Celery-queue
hazards were fixed before the report commit: only a dedicated, explicitly
acknowledged Phase 2/test database is accepted, and the spawned worker now uses
a unique queue with a directed readiness probe. Review-driven fixes also added
child-row rollback evidence, repository history-winner comparison, per-symbol
Yahoo freshness, independent raw-to-canonical reconciliation, stricter output
redaction, and exact destructive gates.

The remaining Important findings are not silently called complete. Full Alembic
command traversal, production-app/auth API integration, live replay persistence,
controlled Celery failure/retry/health behavior, task database cleanup, and all
real-service executions remain blocked items or risks above. The bare-router API
test is valid contract evidence but not full application-startup evidence.

Recommended Phase 3, **not started**: first execute the prepared opt-in suite in
an isolated environment using the repository's PostgreSQL 16, Redis 7, and
Celery services. Capture server versions, migration/reversal evidence,
transaction/concurrency results, worker idempotency, and deployed latency. Only
after those Phase 2 blocks are cleared should a separately approved Phase 3
consider product expansion. Do not expand the universe, add movers/themes/news/
AI/flow claims, or redesign the frontend while these production boundaries are
unverified.
