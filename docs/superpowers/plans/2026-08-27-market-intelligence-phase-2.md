# Market Intelligence Engine Phase 2 Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task. Apply test-driven development to every production-behavior change and use verification-before-completion before any completion claim.

**Goal:** Validate the Phase 1 daily sector-intelligence slice against the repository's production integration boundaries and the live Yahoo provider without changing its fixed universe or financial semantics.

**Architecture:** Extend the existing `FeatureRun` / `FeatureRunPointer`, SQLAlchemy Unit of Work, Alembic, Celery, Yahoo `BulkDataFetcher`, and `/api/v1/market-intelligence` seams with opt-in integration verification. Keep deterministic tests network-free. Live-provider and real-service checks are separate, explicitly gated evidence paths; they never substitute SQLite for PostgreSQL or mocks for Redis/Celery.

**Tech Stack:** Python 3.12, pytest, FastAPI TestClient, SQLAlchemy/Alembic, PostgreSQL 16, Redis 7, Celery, existing Yahoo/yfinance bulk fetcher, React/Vitest for unchanged frontend regression coverage.

## Goal

Move Phase 1's status from deterministic unit-tested behavior to evidence-backed production integration validation where the host permits it. The fixed slice remains SPY plus XLC, XLY, XLP, XLE, XLF, XLV, XLI, XLB, XLRE, XLK, and XLU.

## Scope

- Reuse the existing Compose services (`postgres:16-alpine`, `redis:7-alpine`, backend, and existing Celery workers).
- Validate migration `20260826_0031`, PostgreSQL transaction behavior, publication races, and pointer monotonicity on real PostgreSQL when available.
- Validate Redis connectivity and a real Celery worker one-shot/idempotent run when available.
- Perform an opt-in live Yahoo fetch for exactly the 12-symbol universe and record provider evidence.
- Verify completed-session selection, controlled run-state behavior, APIs, Data Health, historical replay/no-lookahead, and a measurement-only performance baseline.
- Add only integration-test infrastructure, regression tests required by discovered defects, and documentation needed to make the validation repeatable.

## Non-goals

- No new instruments, full-market movers, themes, industries, ETF score, measured money flow, options, institutional flow, 13F, news, AI/LLM, recommendations, signals, portfolio, alerts, or frontend redesign.
- No alternate persistence system, SQLite concurrency claim, parallel Market Intelligence pipeline, dependency upgrade, `npm audit fix`, or system-level installation.
- No Phase 3 work and no change to Phase 1 formulas unless live evidence proves a defect and a red regression test documents it.

## Infrastructure plan

1. Inspect and reuse `docker-compose.yml`, `backend/Dockerfile`, `frontend/Dockerfile`, `Makefile`, `.github/workflows/ci.yml`, `backend/pytest.ini`, and existing test fixtures.
2. Record read-only host probes for Docker, WSL, `psql`, Redis, and Python/Celery.
3. If Docker is usable, start only the existing PostgreSQL and Redis services and use existing backend/Celery configuration. Do not create a second Compose stack.
4. If Docker/WSL or services are unavailable, do not install or reconfigure them. Run all service-independent checks and report each real-service criterion as `BLOCKED_BY_ENVIRONMENT`; overall status must be `PHASE 2 PARTIALLY BLOCKED`.

## PostgreSQL validation

- Require `PHASE2_POSTGRES_URL`, `RUN_MARKET_INTELLIGENCE_POSTGRES=1`, and the exact destructive-test acknowledgement `PHASE2_ALLOW_DESTRUCTIVE_POSTGRES_TESTS=1`; refuse SQLite, generic `DATABASE_URL`, and database names without `phase2`/`test`. Do not enable the repository-wide PostgreSQL fixture for this suite.
- Use an isolated test database/schema under existing Alembic metadata and destroy only explicitly created test data.
- Validate real SQL types, timezone columns, JSON behavior, foreign keys, checks, unique constraints, indexes, nullable fields, defaults, advisory locking, row locking, and committed query results.
- Record server version with `SELECT version()`.

## Migration validation

- Starting from revision `20260823_0030`, execute `alembic upgrade 20260826_0031`.
- Inspect the four Phase 1 tables and compare their tables, columns, PKs, FKs, unique/check constraints, indexes, nullable flags, and defaults to the migration/model contract.
- Execute `alembic downgrade 20260823_0030`; verify all four Phase 1 tables are absent and predecessor tables remain.
- Re-execute `alembic upgrade 20260826_0031`; repeat the schema assertions.
- Also test upgrading an existing predecessor database with representative existing `FeatureRun`/pointer data and verify that data remains readable.

## Rollback validation

- Inject an exception after candidate persistence but before pointer update; rollback must remove audit, bars, rejections, snapshots, and unfinished `FeatureRun` effects.
- Inject an exception after pointer mutation but before commit; rollback must restore the previous pointer and leave no partial/orphan rows.
- Assert FeatureRun lifecycle, audit, snapshot, and pointer state from a separate committed PostgreSQL session.

## Concurrency matrix

| Case | Concurrent publications | Required outcome |
|---|---|---|
| A | Two revisions for the same trading date | Newest lock-ordered published revision wins both history and latest; pointer and history winner agree. |
| B | Newer date and older backfill | Both may be historical; pointer remains on the newer date. |
| C | New date starts first/commits last while old date starts later/commits first | Final pointer advances to the new date and never regresses. |

All cases use independent PostgreSQL sessions, a synchronization barrier, bounded timeouts, and final reads from a third session. Add an explicit same-day idempotent duplicate race for the unique idempotency key.

## Redis validation

- Use the existing Redis service and `app.services.redis_pool` configuration.
- Verify ping, broker/result database separation, bounded key cleanup under a Phase 2-only prefix, and no credential exposure in output.
- Redis absence is a blocked service check, not a canonical-correctness failure.

## Celery validation

- Confirm `app.tasks.market_intelligence_tasks.calculate_sector_intelligence_snapshot` is registered on `market_jobs_us`.
- Start an existing Celery worker configuration against real Redis and PostgreSQL.
- Dispatch a one-shot task for an explicit completed session; verify dependency injection, session creation, provider call, FeatureRun/audit/canonical/snapshot state, final status, pointer, and health.
- Dispatch the same completed session/input twice and verify idempotent logical result and no duplicate evidence.
- Exercise a retryable provider failure without turning it into row rejections; verify final failed attempt and stable pointer.

## Yahoo live-data validation

- Add an explicit opt-in `live_provider` / `manual_provider` pytest path; it must not run in deterministic CI and must need `RUN_MARKET_INTELLIGENCE_LIVE=1`.
- Use the existing `YahooMarketIntelligenceProvider` and `BulkDataFetcher`; request exactly the 12 fixed symbols once per validation/replay dataset.
- Capture duration, requested/returned/missing symbols, earliest date, latest completed session, per-symbol bar counts, rejection counts/codes, and freshness without logging cookies, tokens, or full vendor payloads.
- Persist evidence as summarized documentation only; do not commit provider raw data or imply redistribution rights.

## Historical replay

- Reuse one real Yahoo historical response and replay at least five most recent completed sessions in chronological order.
- For each session slice, enforce `max(input_date) <= snapshot_date` before execution.
- Verify previous-rank continuity, rank changes, published history order, latest pointer, and `market_intelligence_v1`.
- Describe this evidence exactly as “historical replay using real provider data,” never as five past online scheduled runs.

## Data Health validation

- For SUCCEEDED, PARTIAL, and FAILED attempts, compare persisted audit truth with `/health` fields: expected/received/usable symbols, valid/rejected bars, missing symbols, duplicate/invalid counters, provider/request status, freshness, latest attempt, latest complete published snapshot, and metric/normalization versions.
- Verify a PARTIAL or FAILED latest attempt is shown even while `/latest` stays on the previous SUCCEEDED pointer.

## API validation

- Exercise existing routes through the project FastAPI app/TestClient and, when real infrastructure is available, against committed PostgreSQL data:
  - `GET /api/v1/market-intelligence/sectors/latest`
  - `GET /api/v1/market-intelligence/sectors/history`
  - `GET /api/v1/market-intelligence/sectors/health`
- Assert latest has 11 sectors, SPY only as benchmark, the correct metric version, and only pointer-selected published data.
- Assert history chooses the latest revision per session and is date-ordered; assert database/API run IDs and values match.

## Failure injection

- Request failure: raise the production provider request exception and assert FAILED, zero fabricated row rejections, and stable pointer.
- Partial response: omit one symbol or inject one invalid OHLCV row and assert PARTIAL, quarantined evidence/counters, latest attempt visibility, and stable pointer.
- Transaction failures: inject before and after pointer mutation and validate real rollback on PostgreSQL.
- Concurrency: use barriers to force Cases A–C commit order.

## Completed-session policy

- Freeze instants for US market open, after official close, weekend, regular holiday, and early-close day through the existing calendar service.
- Assert the selected target is the latest officially completed session, never `today - 1 day`.
- Assert a provider daily row for an unfinished current session is excluded from canonical/snapshot input.

## Performance baseline

- Measure without optimization: Yahoo fetch, validation/normalization, metrics/ranking, DB persistence, total run, latest API, and history API.
- Use `time.perf_counter`; report sample size, environment, and observed values as diagnostic baselines, not SLOs or benchmarks guaranteed across hosts.

## Testing strategy

- Deterministic tests remain network-free and use fixtures/frozen clocks.
- Integration modules have explicit markers and environment gates. A PostgreSQL assertion fails fast if its supplied URL is not PostgreSQL.
- Live provider checks are separately opt-in and never become default CI gates.
- For each production defect: write the smallest failing regression test, observe the intended failure, apply the minimum fix, and rerun to green.
- Required final commands:
  - Phase 1 exact suite.
  - New Phase 2 service-independent tests.
  - Opt-in PostgreSQL/Redis/Celery tests, or documented environment block.
  - Opt-in Yahoo live validation.
  - Adjacent Market Intelligence backend tests.
  - Full backend diagnostic comparison against Phase 1 known failures.
  - Existing frontend `npm run test:run` and lint/build diagnostics.

## Security

- Never print or commit credentials, cookies, provider payloads, device IDs, `.env`, connection passwords, tokens, or local service state.
- Redact database/broker URLs in reports and command evidence.
- Constrain network calls to the 12 approved symbols; no provider fallback is introduced.
- Run tracked-file and diff scans for common secret patterns and inspect `backend/.local/state/gh/device-id` remains absent.
- Make no market-data redistribution claim; retain only derived/manual validation summaries.

## Acceptance criteria

- [x] Fixed 12-symbol universe is enforced in live and deterministic paths.
- [x] Existing infrastructure is reused and host capabilities are truthfully recorded.
- [x] Migration upgrade/downgrade/re-upgrade and schema contract are explicitly `BLOCKED_BY_ENVIRONMENT`.
- [x] Real-PostgreSQL transaction rollback failure points are explicitly `BLOCKED_BY_ENVIRONMENT`.
- [x] PostgreSQL concurrency Cases A–C, pointer monotonicity, same-day revision, old backfill, and unique race are explicitly `BLOCKED_BY_ENVIRONMENT`.
- [x] Redis connectivity and real Celery one-shot/retry/idempotency are explicitly `BLOCKED_BY_ENVIRONMENT`.
- [x] Live Yahoo exactly-12-symbol fetch completes with summarized lineage/freshness evidence.
- [x] SPY, XLK, XLE, and XLU raw-to-canonical/snapshot calculations are manually reconciled.
- [x] Completed-session open/after-close/weekend/holiday/early-close behavior is deterministic and excludes unfinished bars.
- [x] Controlled SUCCEEDED/PARTIAL/FAILED semantics preserve latest pointer and health truth in the service-independent harness.
- [x] Five-session chronological historical replay has no future leakage and preserves rank continuity/history/latest/version in the service-independent harness.
- [x] Latest/history/health API contracts match persisted service-independent test state; real-PostgreSQL comparison is blocked.
- [x] Performance observations are recorded without optimization or SLO claims.
- [x] All Phase 1 tests and new service-independent Phase 2 tests pass.
- [x] No new backend/frontend regression is introduced relative to Phase 1 baselines.
- [x] No unnecessary dependency, secret/local state, scope expansion, push, PR, or merge occurs.
- [x] `docs/phase2-integration-report.md` records evidence and all blocks/risks.

## Rollback strategy

- Test-only integration harnesses and marker changes can be reverted independently without changing production data semantics.
- Any production fix is a separate small commit with its regression test and can be reverted independently.
- Migration validation uses `downgrade 20260823_0030`; never run downgrade against a user database without an explicitly isolated test target.
- Failed candidate/pointer transactions are rolled back through the existing UoW; the last complete pointer remains authoritative.
- If live provider shape is incompatible, leave production semantics and latest data untouched, record the provider drift, and stop rather than silently coercing it.

## Execution tasks

### Task 1: Preflight and plan checkpoint

**Files:**
- Create: `docs/superpowers/plans/2026-08-27-market-intelligence-phase-2.md`

- [x] Confirm branch `feat/market-intelligence-engine`, Phase 1 HEAD `7627ac7d`, clean tree, and no nested AGENTS instructions.
- [x] Inspect existing Compose, Dockerfiles, Makefile, CI, pytest, Alembic, Redis, Celery, UoW, repository, provider, and API conventions.
- [x] Probe Docker/WSL/PostgreSQL/Redis without installing or altering the host.
- [x] Commit the plan alone: `git add docs/superpowers/plans/2026-08-27-market-intelligence-phase-2.md && git commit -m "docs: plan market intelligence phase 2 validation"`.

### Task 2: Add explicit integration gates and environment preflight tests

**Files:**
- Modify: `backend/pytest.ini`
- Create: `backend/tests/integration/market_intelligence/conftest.py`
- Create: `backend/tests/integration/market_intelligence/test_environment_contract.py`

- [x] Write tests proving a PostgreSQL suite refuses SQLite, live checks require explicit opt-in, the universe is exactly 12 symbols, and secret-bearing URLs are redacted.
- [x] Run the new tests and observe the intended red state for missing helpers/markers.
- [x] Implement the minimal reusable fixtures/gates; rerun to green.
- [x] Commit: `test: add phase 2 integration environment gates`.

### Task 3: Add PostgreSQL migration and publication integration coverage

**Files:**
- Create: `backend/tests/integration/market_intelligence/test_postgres_migration.py`
- Create: `backend/tests/integration/market_intelligence/test_postgres_publication.py`
- Modify only if a defect is proven: `backend/app/infra/db/repositories/market_intelligence_repo.py`
- Modify only if a defect is proven: `backend/app/infra/db/repositories/feature_run_repo.py`

- [x] Add opt-in real-PostgreSQL upgrade/downgrade/re-upgrade, schema, compatibility, rollback, same-day revision, backfill, concurrency A–C, and unique-race tests.
- [x] Verify collection/gating locally; real PostgreSQL execution is `BLOCKED_BY_ENVIRONMENT`.
- [x] No production defect was evidenced without a PostgreSQL server; no speculative production fix was made.
- [x] Commit the test harness in a logical commit.

### Task 4: Add Redis and real Celery integration coverage

**Files:**
- Create: `backend/tests/integration/market_intelligence/test_redis_celery_runtime.py`
- Modify only if a defect is proven: `backend/app/tasks/market_intelligence_tasks.py`
- Modify only if a defect is proven: `backend/app/wiring/market_intelligence_services.py`

- [x] Validate registration/queue without services and add opt-in Redis ping plus real-worker one-shot/retry/idempotency assertions.
- [x] Execute only with explicit service URLs; real services are recorded `BLOCKED_BY_ENVIRONMENT`.
- [x] No production defect was evidenced without the services; no speculative production fix was made.
- [x] Commit: `test: cover market intelligence service runtime`.

### Task 5: Validate completed sessions and live Yahoo data

**Files:**
- Create: `backend/tests/integration/market_intelligence/test_completed_session_policy.py`
- Create: `backend/tests/integration/market_intelligence/test_live_yahoo_validation.py`
- Create: `backend/scripts/validate_market_intelligence_live.py`
- Modify only if a defect is proven: `backend/app/services/market_intelligence_session_source.py`
- Modify only if a defect is proven: `backend/app/infra/providers/market_intelligence_yahoo.py`

- [x] Add frozen open/after-close/weekend/holiday/early-close and unfinished-bar exclusion tests.
- [x] Add an opt-in live check that requests exactly 12 symbols and emits redacted summary JSON.
- [x] Execute the live fetch; inspect SPY, XLK, XLE, and XLU raw/adjusted OHLCV and independently recompute returns, RVOL, RS, and proxy metrics.
- [x] Record provider timing, coverage, rejection, date range, counts, and freshness.
- [x] Commit: `test: validate completed sessions and live yahoo data`.

### Task 6: Validate state semantics, APIs, replay, and performance

**Files:**
- Create: `backend/tests/integration/market_intelligence/test_runtime_semantics.py`
- Create: `backend/tests/integration/market_intelligence/test_historical_replay.py`
- Create: `backend/tests/integration/market_intelligence/test_api_contract.py`
- Create: `backend/tests/integration/market_intelligence/test_performance_baseline.py`

- [x] Add deterministic end-to-end SUCCEEDED/PARTIAL/FAILED tests through the production use case/UoW/API with frozen inputs.
- [x] Add chronological five-session replay/no-lookahead/rank/history/latest/version tests; execute the same replay over the live response without persisting vendor payloads.
- [x] Compare API responses to committed repository state and record diagnostic timings.
- [x] Distinguish deterministic evidence from real-PostgreSQL variants that are `BLOCKED_BY_ENVIRONMENT`.
- [x] Commit: `test: validate sector intelligence runtime semantics`.

### Task 7: Regression and security verification

**Files:**
- Modify only if required by discovered defects and corresponding tests.

- [x] Run Phase 1 exact and adjacent tests.
- [x] Run all new Phase 2 service-independent tests and collect opt-in blocked/pass results.
- [x] Run full backend diagnostic with the Phase 1 source-neutral environment and compare the exact known failure set.
- [x] Run unmodified Windows backend diagnostic and record platform collection blocks separately.
- [x] Run frontend tests, lint, and build diagnostics without fixing unrelated baselines.
- [x] Run compile/import checks, dependency diff, tracked secret scan, device-state check, and review scope.

### Task 8: Integration report and final review

**Files:**
- Create: `docs/phase2-integration-report.md`
- Modify only if evidence changes semantics: `docs/market-intelligence-spec.md`

- [x] Write the report with commands, versions, outcomes, timings, manual reconciliations, blocks, and risks; do not call blocked infrastructure complete.
- [x] Perform the final code review against every Phase 2 acceptance item and inspect transaction/concurrency/security language; retain remaining blocked/risk findings explicitly.
- [x] Commit: `docs: report market intelligence phase 2 validation`.
- [x] Show `git status`, `git log --oneline upstream/main..HEAD`, and `git diff --stat upstream/main...HEAD`.
- [x] PostgreSQL/Redis/Celery remain unverified, so report `PHASE 2 PARTIALLY BLOCKED` and stop.
