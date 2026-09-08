# Market Intelligence MVP v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clear every Phase 2 real-infrastructure gate, then ship a daily-use Market Intelligence MVP for sector leadership, S&P 500 movers, liquid ETF strength, and transparent data health.

**Architecture:** Extend the existing provider → canonical → metrics → snapshot → service → API → React pipeline. Phase 1 sector snapshots remain authoritative; movers read the latest published US `StockFeatureDaily` snapshot plus existing stock/universe metadata; ETF Radar computes versioned deterministic metrics from existing `stock_prices`. React only renders, filters, and sorts API values.

**Tech Stack:** Python 3.11/3.12, FastAPI, SQLAlchemy, Alembic, PostgreSQL 16, Redis 7, Celery 5, pytest, React 18, React Router, TanStack Query, Material UI, Vitest.

## Global Constraints

- Fixed sector intelligence universe remains SPY plus the 11 Sector SPDR ETFs.
- Movers universe is active US S&P 500 membership only, with price > $5 and deterministic liquidity eligibility.
- ETF Radar uses only the configured unleveraged liquid ETF list in this plan.
- No AI, news, options flow, institutional flow, actual fund flow, predictions, recommendations, alerts, backtesting, OTC scan, or Phase 1 semantic rewrite.
- Flow Pressure copy must say it is an OHLCV-derived proxy, not measured institutional or exchange net flow.
- All financial calculations live in Python and are deterministic, documented, versioned, and test-first.
- No unnecessary dependency, upstream push, merge, or non-draft PR.
- Phase 2 must be real-service green before MVP implementation begins.

---

### Task 1: Enable and complete Phase 2 GitHub Actions integration

**Files:**
- Modify: `.github/workflows/market-intelligence-integration.yml`
- Modify: `backend/tests/integration/market_intelligence/test_postgres_migration.py` only for evidenced failures
- Modify: `backend/tests/integration/market_intelligence/test_postgres_publication.py` only for evidenced failures
- Modify: `backend/tests/integration/market_intelligence/test_redis_celery_runtime.py` only for evidenced failures
- Modify: `backend/tests/integration/market_intelligence/test_postgres_api.py` only for evidenced failures
- Modify: `docs/phase2-integration-report.md`

**Interfaces:**
- Consumes: fork branch `origin/feat/market-intelligence-engine`, existing explicit Phase 2 environment gates.
- Produces: a feature-branch push-triggered Actions run proving PostgreSQL, Redis, Celery, migration, concurrency, pointer, and API behavior.

- [x] Add `push.branches: [feat/market-intelligence-engine]` while retaining `workflow_dispatch`; limit paths to the workflow, `backend/**`, and Phase 2 report.
- [x] Verify the YAML contains both triggers, commit `ci: run market intelligence integration on feature branch`, and push only to origin.
- [x] Capture the real run ID/event/branch/SHA and inspect every job/log.
- [x] For each failure, reproduce from logs, add the smallest regression assertion where production behavior changes, implement one minimal fix, and rerun.
- [x] Require core PostgreSQL/Redis job green and optional Yahoo/Celery job green; record actual `SELECT version()` and `INFO server` Redis version in artifacts.
- [x] Update `docs/phase2-integration-report.md` to `PHASE 2 COMPLETE` only after every Part A gate passes.

### Task 2: Add deterministic Market Intelligence read models

**Files:**
- Create: `backend/app/domain/market_intelligence/mvp.py`
- Create: `backend/app/services/market_intelligence_read_service.py`
- Test: `backend/tests/unit/services/test_market_intelligence_read_service.py`

**Interfaces:**
- Consumes: `FeatureRunPointer("latest_published_market:US")`, `StockFeatureDaily.details_json`, `StockUniverse`, `StockFundamental`, `StockPrice`, and Phase 1 sector bundles.
- Produces: `MarketPulseItem`, `MoverItem`, `MoverSummary`, `EtfStrengthItem`, `EtfCategory`, and deterministic filters/sorts.

- [x] Write failing domain tests for fixed pulse symbols SPY/QQQ/DIA/IWM, fixed ETF categories, leveraged/inverse exclusion, `MVP_METRIC_VERSION = "market_intelligence_mvp_v1"`, and derived-proxy wording.
- [x] Implement immutable configuration/value objects with no provider calls.
- [x] Write failing service tests using real SQLite tables for published-run selection, active S&P 500 membership, price > $5, RVOL20 excluding today, gains/losses/unusual-volume ordering, sector aggregation, ETF returns/RS/RVOL/drawdown, and insufficient history.
- [x] Implement SQLAlchemy reads and pure calculations; missing values remain null and never become zero.
- [x] Verify only existing database tables and dependencies are used.

### Task 3: Expose MVP APIs and contracts

**Files:**
- Modify: `backend/app/schemas/market_intelligence.py`
- Modify: `backend/app/api/v1/market_intelligence.py`
- Test: `backend/tests/unit/test_market_intelligence_mvp_endpoints.py`
- Test: `backend/tests/integration/market_intelligence/test_postgres_api.py`

**Interfaces:**
- Consumes: `MarketIntelligenceReadService` and existing sector/health repository.
- Produces:
  - `GET /api/v1/market-intelligence/overview`
  - `GET /api/v1/market-intelligence/movers`
  - `GET /api/v1/market-intelligence/etfs`
  - existing sector latest/history/health unchanged.

- [x] Write failing endpoint tests for overview pulse/freshness, top-20 gainers/losers/unusual volume, sector grouping, filter validation, ETF category ranking/score explanation, and empty/stale states.
- [x] Add typed Pydantic response models with explicit `as_of`, `published_at`, `provider`, `metric_version`, and freshness.
- [x] Add dependency wiring through the existing `get_db`; do not create a second FastAPI app.
- [x] Extend the PostgreSQL API integration test to compare committed DB values with overview/movers/ETF responses.
- [x] Run API and Phase 1 contract suites.

### Task 4: Add Market Intelligence frontend data boundary and navigation

**Files:**
- Create: `frontend/src/api/marketIntelligence.js`
- Create: `frontend/src/api/marketIntelligence.test.js`
- Create: `frontend/src/features/marketIntelligence/MarketIntelligenceShell.jsx`
- Create: `frontend/src/features/marketIntelligence/MarketIntelligenceShell.test.jsx`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/components/Layout/Layout.jsx`
- Modify: `frontend/src/components/Layout/Layout.test.jsx`

**Interfaces:**
- Consumes: the three new endpoints and existing sectors/health endpoints.
- Produces: `/market-intelligence`, `/market-intelligence/movers`, `/market-intelligence/sectors`, `/market-intelligence/etfs`, `/market-intelligence/health`.

- [x] Write failing API-client tests for exact URLs/query parameters and error propagation.
- [x] Implement query helpers and stable query keys.
- [x] Write failing navigation/route tests for the Market Intelligence primary entry and Today/Movers/Sectors/ETFs/Data Health subnavigation.
- [x] Implement lazy routes and a compact MUI shell that follows the existing design tokens.

### Task 5: Build Today, sector heatmap, rotation, and Data Health views

**Files:**
- Create: `frontend/src/features/marketIntelligence/TodayPage.jsx`
- Create: `frontend/src/features/marketIntelligence/SectorsPage.jsx`
- Create: `frontend/src/features/marketIntelligence/DataHealthPage.jsx`
- Create: `frontend/src/features/marketIntelligence/components/FreshnessBanner.jsx`
- Create: `frontend/src/features/marketIntelligence/components/MetricTooltip.jsx`
- Test: corresponding `*.test.jsx` files.

**Interfaces:**
- Consumes: backend overview, sector latest, and health payloads.
- Produces: Market Pulse, 11-sector period switch, rotation table, biggest improvers/decliners, and stable-vs-latest-attempt health disclosure.

- [x] Write failing loading/error/empty/stale/PARTIAL tests before components.
- [x] Write failing tests proving SPY is benchmark-only, exactly 11 sectors render, 1D/5D/20D/60D switching uses server values, and rank directions include text/arrows.
- [x] Implement accessible non-color-only cells and tooltips for Relative Strength, RVOL20, Flow Pressure, and Rank Change.
- [x] Show As of, Last updated, Provider, metric version, and explicit stable snapshot date on every page.

### Task 6: Build S&P 500 Movers view

**Files:**
- Create: `frontend/src/features/marketIntelligence/MoversPage.jsx`
- Create: `frontend/src/features/marketIntelligence/MoversPage.test.jsx`
- Create: `frontend/src/features/marketIntelligence/components/MoversTable.jsx`
- Test: `frontend/src/features/marketIntelligence/components/MoversTable.test.jsx`

**Interfaces:**
- Consumes: backend mover groups and filters.
- Produces: top gainers, top losers, unusual volume, sector concentration, ticker search, sector/direction/min-price/min-RVOL filters.

- [x] Write failing tests for server ordering, null display, top-20 limits, filter query changes, loading/error/empty states, and high-volume gain/loss distinction.
- [x] Implement presentation-only tables; no return or RVOL calculation in JavaScript.
- [x] Add sector grouping counts and freshness banner.

### Task 7: Build ETF Radar and versioned Strength Score

**Files:**
- Create: `frontend/src/features/marketIntelligence/EtfsPage.jsx`
- Create: `frontend/src/features/marketIntelligence/EtfsPage.test.jsx`
- Modify: `docs/market-intelligence-spec.md`

**Interfaces:**
- Consumes: backend ETF metrics, category ranks, and score components.
- Produces: All/Broad Market/Sector/Semiconductor/Software/Biotech/Defense/Energy/Metals/Uranium category views.

- [x] Define and document score v1 as a deterministic percentile-weighted strength measure using 20D RS, 60D RS, 20D return, RVOL confirmation, and drawdown penalty; no expected-return language.
- [x] Write failing backend score/rank/tie/version tests and frontend category/explanation/loading/error tests.
- [x] Implement score components in Python and render them with an explanation panel.
- [x] Verify leveraged/inverse ETFs are absent and category ranks are deterministic.

### Task 8: End-to-end verification, live reconciliation, review, and delivery

**Files:**
- Modify: `docs/phase2-integration-report.md`
- Create: `docs/market-intelligence-mvp-report.md`
- Modify only if evidence requires: implementation/test files above.

**Interfaces:**
- Consumes: complete application and CI evidence.
- Produces: verified MVP branch and optional draft PR.

- [x] Run Phase 1 exact, new backend unit/API, real PostgreSQL/Redis/Celery integration, full backend regression comparison, frontend focused/full tests, lint, and production build.
- [x] Run manual Yahoo validation for SPY/XLK/XLE plus pulse/ETF symbols; reconcile AAPL/NVDA/MU movers when present without committing raw provider payloads.
- [x] Run secret/device-state/dependency diff/security scans and record known baseline advisories without `audit fix`.
- [x] Perform an independent review of financial semantics, stale data, proxy naming, ranking, score, S&P 500 scope, transaction boundaries, API schema, performance, and accessibility; fix findings test-first.
- [x] Push final logical commits and require the latest integration workflow green.
- [ ] Create a draft PR to `txu221/stock-screener:main` only if every quality gate is green; never merge it.
- [x] Record routes, screenshots when possible, run IDs, actual service versions, tests, limitations, and risks in the MVP report.
