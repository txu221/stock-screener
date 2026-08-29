# Market Intelligence Production Hardening v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing Market Intelligence MVP corporate-action-aware, observable, and governed by a measured read-API/cache SLO.

**Architecture:** Extend the shared current-row `stock_prices` model with explicit provenance and an append-only revision ledger, upgrade the bounded sector canonical contract to v2, persist structured pipeline timings/error state, and add stable-pointer-versioned Redis read-through caching with PostgreSQL fallback. Preserve all prior snapshots and reuse existing FastAPI/Celery/Redis patterns.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, Alembic, Celery, PostgreSQL 16, Redis 7, pytest, React, Vitest, GitHub Actions.

## Global Constraints

- Do not add pages or expand the existing stock/ETF universes.
- Do not add AI, News, Options, 13F, institutional/fund flow, predictions, alerts, backtests, or recommendations.
- Yahoo remains the only Market Intelligence provider; no new dependency is expected.
- Old snapshots remain immutable and v1 normalization remains readable.
- Redis is always a soft dependency; PostgreSQL is the read fallback.
- Every production behavior change follows red-green-refactor and receives a focused commit.

---

### Task 1: Corporate-action price contract and migration

**Files:**
- Modify: `backend/app/models/stock.py`
- Create: `backend/alembic/versions/20260828_0034_market_intelligence_production_hardening_v2.py`
- Modify: `backend/app/infra/db/models/market_intelligence.py`
- Test: `backend/tests/unit/test_market_intelligence_production_hardening_migration.py`
- Test: `backend/tests/integration/market_intelligence/test_postgres_migration.py`

**Interfaces:**
- Produces: nullable v2 current-row provenance fields and `StockPriceRevision` append-only evidence.

- [ ] Write migration tests asserting legacy-row compatibility, v2 columns, indexes, revision uniqueness, and downgrade preservation.
- [ ] Run the focused tests and confirm missing schema failures.
- [ ] Add the SQLAlchemy models and additive Alembic migration with down revision `20260828_0033`.
- [ ] Run migration tests green and commit `feat: add corporate action price provenance schema`.

### Task 2: Deterministic price normalization and revision persistence

**Files:**
- Modify: `backend/app/services/price_row_normalization.py`
- Modify: `backend/app/services/stock_price_persistence.py`
- Modify: `backend/app/services/price_cache_service.py`
- Modify: `backend/app/services/benchmark_cache_service.py`
- Modify: `backend/app/services/daily_price_bundle_service.py`
- Create: `backend/tests/fixtures/market_intelligence/corporate_actions.json`
- Test: `backend/tests/unit/test_price_row_normalization.py`
- Test: `backend/tests/unit/test_stock_price_persistence.py`

**Interfaces:**
- Produces: `stock_price_row_from_ohlcv(..., provider, source_timestamp, normalization_version)` mappings with factor/action/hash; `persist_stock_price_mappings` appends distinct revisions and idempotently skips identical inputs.

- [ ] Add failing fixture tests for 2-for-1, 3-for-1, reverse split, dividend adjustment, missing Adj Close, and deterministic content hashes.
- [ ] Add failing persistence tests for first reconciliation, identical replay, and changed provider history.
- [ ] Implement v2 mapping and append-only revision logic without changing unrelated cache behavior.
- [ ] Run focused and existing price-cache tests green; commit `feat: reconcile corporate action price revisions`.

### Task 3: Upgrade the bounded Yahoo/canonical contract

**Files:**
- Modify: `backend/app/domain/market_intelligence/constants.py`
- Modify: `backend/app/domain/market_intelligence/models.py`
- Modify: `backend/app/domain/market_intelligence/validation.py`
- Modify: `backend/app/infra/providers/market_intelligence_yahoo.py`
- Modify: `backend/app/infra/db/repositories/market_intelligence_repo.py`
- Test: `backend/tests/unit/market_intelligence/test_yahoo_adapter.py`
- Test: `backend/tests/unit/market_intelligence/test_validation.py`
- Test: `backend/tests/unit/market_intelligence/test_metrics.py`

**Interfaces:**
- Produces: `market_intelligence_adjusted_ohlcv_v2` canonical bars with `dividend_cash` and `split_ratio`; batch schema failures use `PROVIDER_SCHEMA_DRIFT`.

- [ ] Write failing schema-order/type/timezone/action-column and split/dividend tests.
- [ ] Implement strict batch schema validation and v2 action provenance.
- [ ] Prove split/reverse-split/dividend metrics use one adjusted basis and old v1 persisted rows still deserialize.
- [ ] Run the Market Intelligence domain suite green; commit `feat: harden yahoo corporate action contract`.

### Task 4: Persisted pipeline observability and error taxonomy

**Files:**
- Create: `backend/app/domain/market_intelligence/observability.py`
- Modify: `backend/app/domain/market_intelligence/models.py`
- Modify: `backend/app/use_cases/market_intelligence/build_sector_snapshot.py`
- Modify: `backend/app/infra/db/repositories/market_intelligence_repo.py`
- Modify: `backend/app/tasks/market_intelligence_tasks.py`
- Test: `backend/tests/unit/use_cases/test_build_sector_intelligence_snapshot.py`
- Test: `backend/tests/unit/test_market_intelligence_tasks.py`

**Interfaces:**
- Produces: `MarketIntelligenceErrorCategory`, persisted `stage_timings`, `pipeline_version`, `publication_status`, and structured task/run logs.

- [ ] Write failing tests that inspect log-record fields, timer keys, reuse/force-refresh/redelivery states, and categorized failures.
- [ ] Add minimal monotonic timing instrumentation and stable error mapping.
- [ ] Persist timing/error/publication fields before commit and emit database failures even when persistence is impossible.
- [ ] Run focused tests green; commit `feat: add market intelligence observability`.

### Task 5: Completed-session freshness, health, and readiness

**Files:**
- Create: `backend/app/domain/market_intelligence/freshness.py`
- Modify: `backend/app/infra/db/repositories/market_intelligence_repo.py`
- Modify: `backend/app/api/v1/market_intelligence.py`
- Modify: `backend/app/schemas/market_intelligence.py`
- Modify: `backend/app/services/market_intelligence_read_service.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/unit/market_intelligence/test_freshness.py`
- Test: `backend/tests/unit/test_market_intelligence_endpoints.py`
- Test: `backend/tests/unit/test_health_endpoints.py`

**Interfaces:**
- Produces: `classify_completed_session_freshness(as_of, completed_sessions)` and hardened persisted Data Health response.

- [ ] Write failing same-day, one-session, weekend, holiday, stale, unavailable, consecutive-failure, and readiness-degradation tests.
- [ ] Implement FRESH/AGING/STALE/UNAVAILABLE using completed sessions only.
- [ ] Add provider latency, attempt/success ages, failure category, consecutive failures, last successful trading date, threshold, and pipeline version.
- [ ] Keep liveness dependency-free and Redis/Yahoo/snapshot readiness soft; run tests green and commit `feat: harden market intelligence health`.

### Task 6: Pointer-versioned Redis read cache

**Files:**
- Create: `backend/app/services/market_intelligence_read_cache.py`
- Modify: `backend/app/api/v1/market_intelligence.py`
- Test: `backend/tests/unit/services/test_market_intelligence_read_cache.py`
- Test: `backend/tests/integration/market_intelligence/test_redis_celery_runtime.py`

**Interfaces:**
- Produces: `cached_market_intelligence_payload(key_parts, compute)` with versioned JSON keys, TTL, local miss coalescing, and PostgreSQL fallback.

- [ ] Write failing A-success/B-partial/C-failed/D-success cache-transition tests, Redis-down tests, parameter-key tests, and concurrent-miss tests.
- [ ] Implement keys from stable pointer run/date/version and normalized endpoint parameters; never cache health or unpublished attempts.
- [ ] Run unit and real Redis tests green; commit `feat: cache stable market intelligence reads`.

### Task 7: PostgreSQL performance baseline and SLO

**Files:**
- Create: `backend/tests/integration/market_intelligence/test_postgres_performance_slo.py`
- Create: `docs/market-intelligence-slo.md`
- Modify only if evidence requires: `backend/app/infra/db/models/market_intelligence.py`
- Modify only if evidence requires: `backend/app/models/stock.py`

**Interfaces:**
- Produces: repeat-sample p50/p95/worst JSON evidence for all six APIs and captured `EXPLAIN (ANALYZE, BUFFERS)` plans.

- [ ] Measure without new indexes and record the real PostgreSQL baseline.
- [ ] Inspect the slowest query plan for scans, sorts, N+1, and decoding cost.
- [ ] Set a conservative enforceable SLO from evidence; add only plan-justified indexes.
- [ ] Re-run and document before/after values; commit `perf: enforce market intelligence read SLO`.

### Task 8: UI disclosure and provider canary

**Files:**
- Modify: `backend/app/domain/market_intelligence/mvp.py`
- Modify: `frontend/src/features/marketIntelligence/components/FreshnessBanner.jsx`
- Modify: `frontend/src/features/marketIntelligence/components/FreshnessBanner.test.jsx`
- Create: `.github/workflows/market-intelligence-yahoo-canary.yml`
- Modify: `.github/workflows/market-intelligence-integration.yml`

**Interfaces:**
- Produces: accurate `corporate_action_adjusted`/partial quality labels and a scheduled read-only Yahoo canary.

- [ ] Write failing backend/frontend quality-state and disclosure tests.
- [ ] Derive quality from actual row provenance and show the exact adjusted-history explanation.
- [ ] Add a low-pressure weekday/manual canary that writes no production data.
- [ ] Run frontend tests/lint/build and workflow contract tests; commit `feat: disclose adjusted price quality`.

### Task 9: Security assessment, documentation, and final verification

**Files:**
- Create: `docs/dependency-security-assessment.md`
- Create: `docs/market-intelligence-production-hardening-report.md`
- Modify: `docs/market-intelligence-spec.md`
- Modify: `.github/workflows/market-intelligence-integration.yml`

**Interfaces:**
- Produces: npm critical/high reachability assessment and final evidence report.

- [ ] Run deterministic backend/provider fixtures, frontend, lint, build, compile, and `pip check`.
- [ ] Run PostgreSQL 16, Redis 7, Celery, migration, cache, performance, and optional Yahoo suites in GitHub Actions.
- [ ] Run secret scan, dependency diff, `npm audit --json`, and `git diff --check` without `npm audit fix`.
- [ ] Request an independent code review, fix every in-scope finding through TDD, update Draft PR #1, verify all checks green, and commit `docs: complete production hardening v2 report`.
