# Market Intelligence Production Hardening v2 Design

Date: 2026-08-28
Status: Approved by the Production Hardening v2 master execution directive

## Goal

Make the existing Market Intelligence MVP production-reliable without adding product scope. The release must provide corporate-action-aware analytical prices with auditable provider revisions, persisted pipeline observability and completed-session freshness, and a measured API/cache SLO with PostgreSQL fallback when Redis is unavailable.

## Constraints

- Keep the current Today, Sector, Movers, ETF, and Data Health product surfaces.
- Keep Yahoo as the sole Market Intelligence market-data provider.
- Do not expand the existing product universes.
- Do not add AI, News, Options, institutional/fund-flow claims, predictions, alerts, or backtesting.
- Reuse the existing SQLAlchemy, Alembic, Celery, Redis, FastAPI, React, and GitHub Actions architecture.
- Add no dependency unless the existing stack cannot meet a requirement.
- Preserve prior snapshots and prior normalization semantics.

## Considered Approaches

### 1. Extend `stock_prices` plus an append-only revision ledger — selected

`stock_prices` remains the current materialized row used by existing readers. New nullable provenance fields record provider adjusted close semantics, factor, cash dividend, split ratio, source time, normalization version, content hash, and revision number. Every distinct historical provider revision is appended to `stock_price_revisions` before the current row changes. Existing cache-refresh paths already own Yahoo ingestion, so this solution hardens the existing pipeline rather than duplicating it.

Trade-off: deployed databases require a normal full refresh before every historical row can be reported as reconciled. Until coverage is complete, API quality remains explicitly partial.

### 2. Add a Market Intelligence-only price ledger

This isolates change risk, but duplicates `stock_prices` ingestion for the same symbols and creates two competing price authorities. It was rejected because the directive explicitly asks to audit and harden the existing price model.

### 3. Fetch adjusted history on every API request

This avoids a migration but makes Yahoo a read-path dependency, destroys deterministic publication semantics, and cannot audit historical revisions. It was rejected.

## Corporate-Action Model

`StockPrice.open/high/low/close` remain raw provider OHLC. `StockPrice.adj_close` is the provider adjusted close. Analytical OHLC is derived with one factor:

`adjustment_factor = provider_adjusted_close / raw_close`

`analysis_adjusted_{open,high,low,close} = raw_{open,high,low,close} * adjustment_factor`

The v2 normalization contract is `canonical_price_adjustment_v2`. A v2 row is reconciled only when it has a positive finite factor, an explicit provider, content hash, source timestamp, and normalization version. `split_ratio` and `dividend_cash` preserve Yahoo action context. Volume remains provider volume and is never factor-adjusted.

Yahoo adjusted close includes split and distribution adjustment. Therefore the existing `return_*` fields are documented as provider-adjusted analytical/total-return proxies, not pure raw price returns. Pure price return remains derivable from raw closes but is not substituted into the existing API.

The bounded Phase 1 canonical bar receives the same split/dividend provenance and upgrades to `market_intelligence_adjusted_ohlcv_v2`. Old run audits and snapshots remain immutable and retain v1 normalization labels.

## Historical Revision Semantics

Each normalized row receives a deterministic content hash over symbol, date, raw OHLCV, adjusted close, factor, action fields, provider, and normalization version. Identical input is idempotent. A changed hash appends a new `stock_price_revisions` record and advances the current materialized row revision. If a legacy row is replaced for the first time, its previous state is first captured as `legacy_unversioned` revision zero.

No existing Market Intelligence snapshot is updated. A force refresh creates a new audited feature run; the stable pointer moves only after a complete `SUCCEEDED` publication.

## Provider Drift Boundary

The Yahoo adapter validates the complete batch contract before emitting rows: mapping shape, required OHLCV/Adj Close/action columns, numeric-compatible columns, non-empty symbol coverage, monotonic unique timestamps, and timezone-aware/normalizable daily indexes. A contract failure is categorized `PROVIDER_SCHEMA_DRIFT`; it is not counted as a missing symbol or bad row.

## Observability

The pipeline emits structured log records with stable fields rather than message interpolation. A run audit persists:

- stable `error_category` from the v2 taxonomy;
- `pipeline_version`;
- stage timings for provider fetch, normalization, validation, calculation, persistence, publication, and total;
- publication and reuse status.

Celery logs dispatch, start, stable-result reuse, force refresh, redelivery, failure, and completion with task/run identifiers. Database failures that cannot be persisted are still emitted as structured failures.

## Freshness and Health

Freshness is based on completed US sessions:

- `FRESH`: snapshot equals the latest completed session;
- `AGING`: exactly one completed session behind;
- `STALE`: at least two completed sessions behind;
- `UNAVAILABLE`: no usable snapshot or an inconsistent future date.

Weekends and holidays do not advance session age. Health returns latest attempt/success age, provider latency, failure category, consecutive failures, last successful trading date, threshold, and pipeline version from persisted audits. Existing `/livez` remains dependency-free. `/readyz` treats PostgreSQL/schema as required while Redis and an absent/stale Market Intelligence snapshot are degraded soft dependencies.

## Cache Architecture

Read responses use JSON Redis read-through keys that include cache contract version, endpoint, stable published run ID, trading date, metric version, and normalized parameters. This makes successful publication the invalidation event:

`Succeeded A -> key A; Partial/Failed B/C -> pointer and key A unchanged; Succeeded D -> key D`.

TTL is a safety bound, not the primary invalidation mechanism. Redis errors fall back to PostgreSQL. A small per-process keyed lock coalesces concurrent cache misses without adding a distributed-lock dependency. Health remains uncached so failed attempts are visible immediately.

## Performance Measurement and SLO

PostgreSQL CI measures overview, movers, ETFs, sectors latest/history/health with warm-up plus repeated samples. It records p50, p95, and worst observation. `EXPLAIN (ANALYZE, BUFFERS)` is captured for the slowest query before any index is added. The initial SLO is set only after the first real baseline and is enforced with a conservative CI ceiling documented in `docs/market-intelligence-slo.md`.

## UI Disclosure

Today, Movers, and ETF pages display: “Historical analytical returns use corporate-action-adjusted prices.” If any required row is legacy or lacks provenance, the banner instead reports partial reconciliation. No page claims a pure price return or actual money flow.

## Migration and Rollback

The migration is additive and nullable for existing `stock_prices`, creates the revision ledger, adds v2 canonical action fields, and adds run-audit observability columns. Upgrade does not rewrite historical rows. Normal price refresh progressively reconciles rows; a full refresh is the rollout/backfill action. Downgrade removes only v2 columns/table and leaves prior snapshots and current legacy price fields intact.

## Testing

Tests cover 2-for-1, 3-for-1, reverse split, cash-dividend adjustment, revision append/idempotency, v1 snapshot compatibility, provider schema drift, structured fields, stage timing persistence, weekend/holiday freshness, cache transition A/B/C/D, Redis fallback, cache-miss coalescing, PostgreSQL query plans, API latency, UI disclosure, migrations, Celery, and opt-in Yahoo canary behavior.

## Acceptance

The design is complete when all master-directive final verification checks pass on the final SHA, both GitHub workflows are green, Draft PR #1 is updated but not merged, and the production-hardening report records measured evidence and remaining limitations.
