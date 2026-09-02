# Market Intelligence MVP v1 Implementation Report

Status: COMPLETE

Date: 2026-08-28 (America/New_York)

Branch: `feat/market-intelligence-engine`

## 1. Outcome and architecture

Market Intelligence MVP v1 extends the selected `xang1234/stock-screener` base without a parallel application or ingestion system. The implemented path is:

```text
existing Yahoo / existing market pipeline
    -> existing cached stock_prices and published StockFeatureDaily
    -> Phase 1 canonical sector snapshots and Data Health
    -> deterministic MarketIntelligenceReadService
    -> typed FastAPI responses
    -> TanStack Query API boundary
    -> one lazy-loaded React Market Intelligence product area
```

Phase 1 sector publication remains the authoritative 12-symbol transactional pipeline. Movers and ETF Radar are read models over existing local tables; neither performs a provider request. React presents and filters server values but does not calculate returns, relative strength, RVOL, flow pressure, ranks, rank changes, or ETF scores.

## 2. Product routes and pages

| Route | Surface | Primary question |
|---|---|---|
| `/market-intelligence` | Today | What are SPY/QQQ/DIA/IWM and sector leadership doing? |
| `/market-intelligence/movers` | S&P 500 Movers | Which liquid constituents gained, lost, or traded at unusual relative volume? |
| `/market-intelligence/sectors` | Sector Heatmap and Rotation | Which of the 11 sectors is leading, lagging, improving, or declining? |
| `/market-intelligence/etfs` | ETF Radar | Which configured liquid ETFs have the strongest descriptive metrics? |
| `/market-intelligence/health` | Data Health | Is the latest attempt complete, and which stable snapshot is displayed? |

The existing application navigation, Watchlists, Stock Detail, scans, groups, themes, operations, and authentication behavior remain in place. The Market Intelligence bundle is page-level lazy loaded; the code-final local build emitted a 27.93 kB chunk (7.52 kB gzip).

## 3. Data sources and universes

### Market Pulse

The fixed pulse is SPY, QQQ, DIA, and IWM. VIX was not added because the approved existing local path did not provide an equally reliable completed-session VIX contract. Overview and ETF Radar use the date and publication timestamp of the existing `latest_published_market:US` feature run as their read boundary, then compare that date with `MarketCalendarService.last_completed_trading_day("US")`. Regressions prove neither a future row nor a same-session partial row beyond the last published boundary can become the displayed as-of session.

### Sectors

SPY is the benchmark and is never sector-ranked. The ranked universe remains exactly XLC, XLY, XLP, XLE, XLF, XLV, XLI, XLB, XLRE, XLK, and XLU.

### Movers

Movers use only active US S&P 500 members present in the existing `latest_published_market:US` feature run. Default eligibility is cached adjusted price above USD 5 plus existing average daily dollar volume of at least USD 100 million. Missing/non-finite evidence is excluded, including non-finite market cap before JSON serialization. The UI exposes ticker/company search, stable sector choices, direction, minimum price, minimum RVOL, and all existing market-cap groups.

### ETF Radar

The 28 unique unleveraged ETF symbols are configuration-owned and grouped as Broad Market, Sector, Semiconductor, Software, Biotech, Defense, Energy, Metals, and Uranium. The list is SPY, QQQ, IWM, DIA; the 11 Sector SPDR ETFs; SMH, SOXX, XSD; IGV; XBI, IBB; ITA, PPA; XOP; GDX, GDXJ, COPX; and URA. XLE belongs to both Sector and Energy without duplicating overall identity. Leveraged and inverse ETFs are absent.

## 4. Metrics and score formulas

Read-model price metrics use the existing `stock_prices.adj_close` field and real ordered sessions:

```text
return_N = adjusted_close_today / adjusted_close_N_sessions_ago - 1
relative_strength_N = instrument_return_N - SPY_return_N
RVOL20 = volume_today / mean(previous 20 completed-session volumes)
drawdown_60d = adjusted_close_today
               / max(adjusted_close over today plus previous 60 sessions) - 1
```

Missing history, missing/non-positive cached adjusted close, missing/negative volume, a zero RVOL denominator, missing benchmark, or non-finite output remains null. RVOL excludes today. MVP responses explicitly publish `price_basis=cached_adjusted_close` and `price_history_quality=not_corporate_action_reconciled`; the UI displays the limitation instead of claiming revision-consistent total-return history. Phase 1 Flow Pressure retains its documented adjusted-OHLC/provider-volume semantics and exact UI disclosure: `OHLCV-derived pressure proxy. Not measured institutional or exchange net flow.`

`etf_strength_v1` applies inclusive empirical percentiles to every complete ETF and combines:

```text
30% relative_strength_20d
25% relative_strength_60d
20% return_20d
15% signed volume confirmation
10% drawdown_60d

signed volume confirmation = clamp(RVOL20, 0, 3) - 1
signed positive for positive 20D return, negated for negative 20D return
```

The score is deterministic, versioned, descriptive, and 0–100. It is not a prediction, expected return, recommendation, or fund-flow measure. Missing one component leaves score/ranks unavailable. Overall and category ranks use descending score with ascending symbol as the deterministic ordinal tie-breaker. The API returns weights, percentile method, volume transform, language classification, component percentiles, and ranks.

## 5. API contracts

The existing FastAPI application now exposes:

- `GET /api/v1/market-intelligence/overview`;
- `GET /api/v1/market-intelligence/movers`;
- `GET /api/v1/market-intelligence/etfs`;
- `GET /api/v1/market-intelligence/sectors/latest`;
- `GET /api/v1/market-intelligence/sectors/history`;
- `GET /api/v1/market-intelligence/sectors/health`.

Responses are typed Pydantic models. Overview and ETF Radar use `market_intelligence_mvp_v1`; ETF score semantics use `etf_strength_v1`. Movers returns backend-ordered top gainers, top losers, unusual volume, eligible count, and sector breadth. The Phase 1 sector contracts and transactional pointer semantics are unchanged.

## 6. Freshness and data-quality behavior

Every populated page shows As of, Last updated, Provider, metric version, price basis when applicable, and explicit freshness state. Overview, Movers, and ETF Radar publish expected session plus `FRESH`/`STALE`/`FUTURE`/`UNAVAILABLE`; their displayed update time is the published feature-run boundary rather than `StockPrice.created_at`. Loading, request error, source-unavailable, valid-empty, and missing-value states have explicit UI tests. Data Health displays provider status, source freshness, expected/received/valid/rejected/missing/duplicate/invalid-volume/invalid-OHLC counts, latest attempt, and latest published state.

Sector and Today pages query Data Health independently from the published sector payload. If the latest attempt is PARTIAL or FAILED, the UI labels that attempt separately and shows the date of the stable snapshot still being displayed. A partial attempt cannot move the Phase 1 latest pointer. Today labels Market Pulse and sector leadership as separate sources so mismatched dates/provider/version are visible. Missing data is never rendered as 0%, rank 0, or fabricated activity.

## 7. Testing and route validation

Current evidence:

- post-target-integration deterministic backend Market Intelligence suite: 149 passed;
- post-target-integration focused frontend Market Intelligence suite: 11 files and 35 tests passed;
- local frontend full diagnostic: 631 passed, 9 known concurrent `App.static` failures, plus the known Windows doubled-drive-path suite-load error;
- isolated `App.static`: 9/9 passed immediately;
- pre-target-integration Linux Actions frontend checkpoint: 100 files and 662 tests passed;
- code-final production build: 2,510 modules transformed and passed;
- lint: 0 errors and the same 4 pre-existing warnings; an initially introduced fifth warning was removed before closure;
- source-neutral Windows backend full-unit comparison: 6,141 passed, 13 known baseline failures, and 3 skipped in 15m11s;
- unmodified Windows backend collection remains blocked by the upstream Unix-only `resource` import; the source-neutral comparison injects only an in-memory process shim and changes no file or dependency.

Component tests validate all five routes through the shared shell. Page tests cover pulse, exactly 11 sectors, SPY benchmark exclusion, the authoritative `return_1d` rank key, period switching, rank direction, PARTIAL/stable disclosure, separate Today source lineage, mover filters/order/nulls/directions/unavailable reasons, ETF categories/score explanation, accessible keyboard tooltips, freshness, loading, error, and empty states. No screenshot was committed because the approved Windows host has no full PostgreSQL/Redis/Celery runtime; route tests, Linux CI, and the production bundle provide reproducible route/build evidence without fabricating live UI data.

## 8. Real infrastructure and live data

Phase 2 is complete. The dedicated Actions workflow uses Ubuntu 24.04, PostgreSQL 16.15, and Redis 7.4.11. It proves Alembic upgrade/downgrade/re-upgrade, predecessor compatibility, rollback before/after pointer mutation, same-date revision concurrency, old backfill monotonicity, PostgreSQL-backed APIs, Redis, a real isolated Celery worker, Yahoo ingestion, and idempotent repeated delivery.

Target-base integration exposed that two independent live Yahoo calls can occasionally return a corrected completed-session value. The scheduled task now resolves an existing published run for the same date/version before a retry performs provider I/O; an explicit `force_refresh=True` remains available for intentional, audited same-day revisions. Red-first regressions prove both published-session reuse and recovery of an unpublished partial session; they are included in the 149-test deterministic suite. This changes delivery behavior, not metric formulas or publication atomicity.

The pre-target-integration checkpoint is GitHub Actions run `33193795883`: all three jobs completed successfully at commit `43cb90e1` ([run URL](https://github.com/txu221/stock-screener/actions/runs/33193795883)). Final target-base evidence is attached to the Draft PR checks for the final feature-branch commit.

An additional non-persisting local Yahoo reconciliation on 2026-08-28 requested SPY, XLK, XLE, AAPL, NVDA, MU, SMH, and QQQ for completed session 2026-08-27. All 8 returned 125 sessions through the target, and the backend calculation produced finite 1D/5D/20D/60D returns, RVOL20, and 60D drawdown. Representative semantic checks included NVDA at approximately +8.74% 1D with 2.56 RVOL, XLE at approximately -0.22% 1D with 1.15 RVOL, and SMH at approximately +3.10% 1D. These observations were printed as summarized metrics only; no raw provider payload was written or committed.

## 9. Performance

MVP read paths use bounded local-table queries: 120 calendar days for four pulse symbols or 28 ETFs, and 60 calendar days for eligible Movers symbols. Lists are capped server-side (default 20), financial calculations are linear in the bounded row set, and the frontend is lazy loaded. The workflow's full frontend test and build gates pass. These are diagnostic results, not deployed latency SLOs; production query latency and cache policy remain operational follow-up work.

## 10. Security, dependencies, and scope

- `pip check`: no broken requirements;
- dependency manifests/lockfiles: no diff from the selected base for this work;
- npm audit baseline: 21 advisories (1 low, 3 moderate, 16 high, 1 critical); no `npm audit fix` was run;
- added-line secret scan: no private-key, GitHub/OpenAI/AWS token, or assigned-secret pattern match;
- `backend/.local/state/gh/device-id`: absent;
- no raw Yahoo payload, token, cookie, `.env`, local database, broker URL, device identifier, or machine-local state was committed;
- no new dependency, authentication system, static publisher, provider, or parallel data pipeline was added;
- no upstream push or feature-to-main merge was performed; fork `main` was merged into the feature branch before Draft PR review so the exact target-base combination could be validated.

## 11. Independent review findings

The independent review confirmed the read-only architecture, deterministic universes/formulas/ties, S&P 500 eligibility, null handling, typed contracts, unchanged Phase 1 transaction path, no new dependency, and green pre-review CI. Review-driven fixes then:

1. mapped 1D sector presentation to the authoritative `return_1d` rank;
2. used Data Health for latest-attempt versus displayed-stable disclosure;
3. separated Today pulse and sector lineage;
4. anchored pulse/ETF reads to the published US feature-run boundary and used its publication time;
5. exposed expected-session freshness and cached-price basis/quality;
6. normalized non-finite market cap before API serialization;
7. made metric tooltips keyboard/screen-reader accessible;
8. separated Movers source-unavailable from a valid empty filter result;
9. removed the undefined best-category rank from ETF All view;
10. stabilized sector filter choices and exposed the supported small-cap group.

The reviewer also identified that the existing `stock_prices` history is not fully corporate-action reconciled because bounded historical anchors are not revised on every adjustment change. Correcting that is a cross-cutting ingestion/schema/backfill project outside this MVP's no-rewrite scope. The product now discloses the limitation in API and UI, uses a published date boundary, and does not claim revision-consistent total-return correctness.

The implementation was also reviewed for big-bang refactors, provider calls in read paths, duplicate financial calculations in React, stale-data disclosure, proxy language, ordinal ties, completed-session bounds, S&P 500/liquidity eligibility, null handling, API types, transaction safety, security, performance, and non-color accessibility. Review-driven fixes are recorded in the commit history and all affected tests are rerun.

Draft PR integration exposed two target-base issues that did not exist at the Phase 0 baseline: fork `main` had independently created a second Alembic head and its v1.5 release notes no longer satisfied the retained release-note contract. The branch now includes an explicit no-op merge revision with a single-head regression test and restores the accurate `first-run bootstrap` release wording. This keeps the feature PR migration-safe without rewriting either additive parent migration.

## 12. Known limitations and remaining risks

- Today deliberately shows raw completed-session pulse rather than inventing an unapproved Market Status score.
- VIX is absent because no equally reliable approved completed-session local contract was available.
- Movers depend on the existing published US feature run, S&P 500 membership quality, cached daily prices, and stored average dollar volume.
- Existing `stock_prices.adj_close` history is not fully corporate-action reconciled. A later canonical-price milestone must persist revision/update provenance and backfill split/dividend-aware bounded windows; until then MVP outputs are explicitly labeled `not_corporate_action_reconciled` and must not be interpreted as guaranteed total-return series.
- ETF metrics are computed as bounded read models rather than separately materialized historical MVP snapshots; Phase 1 sector history remains persisted.
- Yahoo remains an external provider with availability, shape, entitlement, and redistribution risk.
- There is no explicit Celery `autoretry_for` policy; outage recovery is a later reliability workflow.
- Native Windows still cannot execute PostgreSQL/Redis/Celery integration locally; Linux Actions is the real-service authority.
- Existing Windows backend/frontend portability failures and npm advisories remain intentionally separate baseline work.
- Deployed API latency, caching behavior under sustained load, and browser screenshots against a production-like populated environment remain unmeasured.

## 13. Recommended next major milestone

The next milestone should be operational hardening and observation of the shipped MVP: deploy against a controlled PostgreSQL/Redis environment, measure endpoint latency and query plans, define cache/freshness SLOs, exercise worker outage/retry recovery, capture populated route screenshots, and gather user feedback on scanability. Do not add AI, news, options/institutional flow, predictions, alerts, backtesting, or broad theme expansion until these daily-use surfaces and data-health semantics are stable.

## 14. Code-final closure

GitHub Actions run `33193795883` validated review-fix commit `43cb90e1` on the feature branch. Results:

- PostgreSQL and Redis integration: success, including migration traversal, rollback, concurrent publication, monotonic pointers, PostgreSQL-backed sector and MVP APIs, Redis connectivity, and deterministic Market Intelligence suites;
- Optional Yahoo and Celery integration: success, including completed-session Yahoo validation and a real Redis-brokered worker one-shot/idempotent rerun;
- Frontend tests and production build: success, including lint, the full Linux Vitest suite, and Vite production bundle;
- independent review regressions: 1D rank contract, same-day partial boundary, non-finite market cap, latest-attempt/stable disclosure, separate Today lineage, accessible metric help, source-unavailable Movers, and ETF All-rank semantics all passed.

No upstream push, feature-to-main merge, dependency change, credential, local device state, or scope expansion occurred. Fork `main` was merged into the feature branch only to validate the exact Draft PR target; the feature remains unmerged.
