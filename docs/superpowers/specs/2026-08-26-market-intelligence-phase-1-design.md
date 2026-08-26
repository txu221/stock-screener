# Market Intelligence Engine Phase 1 Design

Date: 2026-08-26 (America/New_York)

Status: Approved architecture, awaiting written-spec review

## 1. Goal

Build one complete, deterministic daily sector-intelligence vertical slice over exactly SPY and the 11 US Select Sector SPDR ETFs. The slice must prove provider ingestion, strict validation, canonical lineage, rejection audit, session-aware metrics, deterministic ranks and rank history, idempotent snapshots, atomic publication, API reads, and Data Health without expanding the product universe.

The governing metric version is `market_intelligence_v1`. The governing normalization version is `market_intelligence_adjusted_ohlcv_v1`.

## 2. Scope and non-goals

The fixed universe is:

- benchmark: `SPY`;
- sectors: `XLC`, `XLY`, `XLP`, `XLE`, `XLF`, `XLV`, `XLI`, `XLB`, `XLRE`, `XLK`, `XLU`.

Phase 1 includes only daily completed-session OHLCV ingestion, the metrics in this document, sector ranks, previous-published-session rank changes, durable audit/snapshots, atomic publication, three read-only API endpoints, and deterministic tests.

Phase 1 does not include a frontend, static JSON publication, additional symbols, stocks, themes, industries, news, LLM/AI output, options or institutional flow, measured fund flow, Net Dollar Flow, composite opportunity scores, signals, recommendations, backtests, alerts, authentication work, dependency upgrades, or infrastructure installation.

## 3. Architectural decision

Use the approved hybrid extension:

- reuse `FeatureRun` for the run lifecycle;
- reuse `FeatureRunPointer` with a dedicated key such as `latest_market_intelligence_sectors_us`;
- reuse the existing Unit of Work and SQLAlchemy transaction boundary;
- reuse `SqlFeatureRunRepository.publish_atomically()` for the final pointer move;
- add focused Market Intelligence domain types, repository ports, ORM tables, provider adapter, calculation modules, use case, API schemas, and routes;
- do not reuse the existing broad Market RS algorithm or the existing permissive price-row normalization;
- do not create another run lifecycle, pointer table, publication service, FastAPI application, or static artifact path.

The existing `StockPrice` table is not the canonical Phase 1 store because it cannot retain the required raw/adjusted lineage and its shared ingestion path coerces some volume values to zero. Phase 1 canonical rows are isolated in typed tables linked to the existing `FeatureRun`.

## 4. Components and responsibilities

### 4.1 Universe configuration

A single domain module owns the 12-symbol tuple, benchmark identity, sector names, asset types, expected count, and stable universe hash. Callers cannot add symbols dynamically.

### 4.2 Provider adapter

The primary provider is the existing Yahoo/yfinance bulk path, which calls `yf.download(..., auto_adjust=False, actions=True)`. The adapter requests a six-month daily window, sufficient to receive approximately 90 or more completed trading sessions under normal conditions.

There is no fallback provider in Phase 1. A future fallback requires a separate design, source-basis compatibility proof, and provider policy.

The adapter returns a typed batch result containing:

- provider name and provider response timestamp;
- frames received by provider symbol;
- per-symbol provider failures when the request envelope succeeded;
- one request-level failure when the entire Yahoo request was unavailable, timed out, unauthenticated, malformed, or otherwise unusable as a request.

A request-level failure is recorded once in run/provider audit. It must not manufacture 12 missing-row rejections.

### 4.3 Session reference

Metric calculation consumes an explicit ordered sequence of completed US trading sessions. Production wiring uses the existing market-calendar capability. SPY is the benchmark series checked against that session sequence; provider row presence alone never defines the calendar.

The input contract requires at least 90 completed reference sessions ending at the requested `as_of` session. Every published symbol must have canonical bars on the required reference sessions. Ordering, duplicates, missing required sessions, and current-session alignment are validated before metrics.

### 4.4 Strict canonical validator

The validator consumes raw-like provider rows directly. It never calls the existing permissive `_volume_or_zero` path.

Validation is deterministic and preserves accepted and rejected evidence. A row cannot be repaired, clamped, forward-filled, or silently dropped. Duplicate `(symbol, trading_date)` groups reject every member of the duplicate group so row selection is never arbitrary.

Stable rejection codes are:

- `UNEXPECTED_SYMBOL`;
- `MISSING_REQUIRED_FIELD`;
- `INVALID_TRADING_DATE`;
- `NON_FINITE_VALUE`;
- `NON_POSITIVE_PRICE`;
- `INVALID_ADJUSTED_CLOSE`;
- `INVALID_ADJUSTMENT_FACTOR`;
- `INVALID_OHLC_RELATION`;
- `NEGATIVE_VOLUME`;
- `DUPLICATE_BAR`.

Every raw row must satisfy:

- symbol belongs to the fixed universe;
- trading date parses to a valid completed reference session;
- raw Open, High, Low, Close, Adj Close, and Volume are present and finite;
- raw Open, High, Low, Close, and Adj Close are greater than zero;
- Volume is greater than or equal to zero;
- High is greater than or equal to Low, Open, and Close;
- Low is less than or equal to Open and Close;
- `adjustment_factor = Adj Close / Close` is finite and greater than zero;
- the symbol/date pair is unique.

Negative, fractional, or otherwise malformed provider volume is not altered. The validator accepts a finite non-negative numeric provider volume and persists its provider-reported value; it does not split-adjust volume.

### 4.5 Adjustment lineage

For an accepted row:

```text
adjustment_factor = raw_adjusted_close / raw_close
adjusted_open  = raw_open  * adjustment_factor
adjusted_high  = raw_high  * adjustment_factor
adjusted_low   = raw_low   * adjustment_factor
adjusted_close = raw_close * adjustment_factor
```

The canonical record retains provider, provider symbol, raw trading date, raw OHLC, provider-reported adjusted close, adjustment factor, adjusted OHLC, provider-reported volume, provider source/as-of timestamp when available, ingestion timestamp, price basis, normalization version, and run identifier.

The canonical price basis is `yahoo_adjusted_ohlc_provider_volume`. No metric may combine raw High/Low with adjusted Close. No metric adjusts volume.

## 5. Data model

All new tables use additive Alembic migration(s) and reference `feature_runs.id` with `ON DELETE CASCADE`.

### 5.1 `market_intelligence_run_audits`

One row per Market Intelligence `FeatureRun`, keyed by `run_id`. It stores:

- unique deterministic `idempotency_key`;
- `ingestion_status`: `SUCCEEDED`, `PARTIAL`, or `FAILED`;
- provider and provider status;
- request failure code/message, nullable;
- metric and normalization versions;
- price basis;
- expected, received, valid, missing, and usable-symbol counts;
- accepted-bar and rejected-row counts;
- duplicate, invalid-volume, invalid-OHLC, and other rejection aggregates;
- missing symbols and per-symbol provider failures as structured JSON;
- target session, provider response timestamp, source freshness, calculation timestamp, and ingestion timestamp.

This table is the source of truth for Data Health. UI or API code must not recompute counters from sector rows.

### 5.2 `market_intelligence_canonical_bars`

Primary key: `(run_id, symbol, trading_date)`. It stores all raw evidence, adjustment lineage, adjusted OHLC, provider volume, timestamps, basis, and normalization version described above.

### 5.3 `market_intelligence_rejections`

One row per rejected raw observation. It stores run, provider, provider symbol, normalized symbol if available, trading date if known, stable rejection code, human-readable reason, ingestion timestamp, and JSON-safe raw evidence. Request-level failures are not stored here.

### 5.4 `market_intelligence_sector_snapshots`

Primary key: `(run_id, symbol)`. It stores:

- trading date, symbol, asset type, and sector name;
- 1D, 5D, 20D, and 60D returns;
- 1D, 5D, 20D, and 60D relative returns versus SPY for sectors, null for SPY;
- RVOL20;
- `flow_pressure_1d_proxy`, `cmf_5d_proxy`, `cmf_20d_proxy`, and `cmf_60d_proxy`;
- typed JSON maps for current ranks, previous ranks, rank changes, and rank directions;
- provider, source freshness, price basis, metric version, calculation timestamp, and data-quality status.

Candidate rows may exist for PARTIAL runs for audit purposes. They are never visible through `/latest` or published history. Ranks are calculated only when the complete 11-sector ranking universe and every source metric are available.

## 6. Run status and publication semantics

The Market Intelligence ingestion status is separate from the existing `FeatureRun.status` lifecycle:

| Ingestion status | Definition | FeatureRun final lifecycle | Pointer move |
|---|---|---|---|
| `SUCCEEDED` | request succeeded; all 12 symbols have the required canonical window, metrics, and complete snapshot | `published` | yes |
| `PARTIAL` | request succeeded and at least one symbol has a usable local-metric candidate, but any symbol, history, metric, relative metric, or universe requirement is incomplete | `quarantined` | no |
| `FAILED` | request-level failure, completely unusable response, zero usable symbols, or no valid candidate snapshot | `failed` | no |

These states are mutually exclusive and exhaustive. A provider response containing zero usable symbols is `FAILED`, even when the HTTP/request envelope itself succeeded.

A `SUCCEEDED` run also requires zero rejected rows in the requested completed-session dataset. In particular, a negative-volume or invalid-OHLC row cannot be hidden merely because enough other sessions remain to calculate a number. A response may contain valid sessions outside the minimum 90-session calculation window; those rows remain canonical evidence, but every returned completed-session row is still validated.

`/latest` resolves only the existing named published pointer. `/health` resolves the newest attempted Market Intelligence run independently. Therefore a PARTIAL or FAILED attempt does not erase or hide the last complete published snapshot.

## 7. Transaction boundaries and invariants

Provider I/O and symbol-local pure calculation occur before durable mutation. Predecessor rank lookup and rank-change construction occur inside the final Unit of Work so they observe the publication state used by the commit. The transaction performs:

```text
create FeatureRun in RUNNING state
create run audit
persist accepted canonical bars
persist row rejections
persist candidate sector snapshots
set final ingestion audit status and counters
transition FeatureRun to COMPLETED then:
  SUCCEEDED -> publish_atomically(pointer_key)
  PARTIAL   -> QUARANTINED
or transition FeatureRun to FAILED
commit once
```

For a request-level failure, the same final transaction creates the run and audit, records one provider failure, transitions the run to `failed`, and commits without canonical rows, row rejections, snapshots, or pointer movement.

Consequences:

- a pointer cannot reference an uncommitted snapshot;
- published snapshot and audit rows commit together;
- canonical rows cannot commit without their final run/audit state;
- a PARTIAL or FAILED transaction cannot move the pointer;
- rollback leaves the previously published pointer and rows unchanged.

## 8. Idempotency

The input hash is calculated from the normalized raw evidence and ordered reference sessions. The idempotency key is a SHA-256 digest of pipeline identity, target session, fixed universe hash, provider, metric version, normalization version, and input hash.

`market_intelligence_run_audits.idempotency_key` is unique. Before mutation, the use case checks for an existing exact run. Repeating the same successful provider response, including an identical row-level-invalid response, returns the existing logical result and does not create another FeatureRun, canonical set, rejection set, or snapshot set. A database uniqueness constraint protects concurrent duplicates.

Request-level failures have no market-data input hash and therefore receive an attempt-specific key. They remain independently auditable and do not suppress a later retry after a transient outage.

A corrected provider payload has a different input hash and may create a new candidate run for the same session. Published history resolves the newest successfully published run for each `(trading_date, metric_version)`; partial attempts never become rank predecessors.

## 9. Metrics

All metrics operate on accepted adjusted OHLC and provider volume over explicit completed trading sessions. Missing input produces an unavailable metric, never zero.

### 9.1 Returns

```text
return_N = adjusted_close_today / adjusted_close_N_sessions_ago - 1
```

N is 1, 5, 20, or 60. A missing anchor session, duplicate date, non-finite value, or insufficient history makes the metric unavailable and prevents a `SUCCEEDED` run.

### 9.2 Relative return versus SPY

For each sector and N in 1, 5, 20, and 60:

```text
relative_return_vs_spy_N = sector_return_N - spy_return_N
```

This is descriptive relative performance and is intentionally separate from the repository's existing Market RS algorithm.

### 9.3 RVOL20

```text
rvol20 = volume_today / mean(volume over previous 20 completed sessions)
```

The current session is excluded from the denominator. Missing history or a zero historical mean returns unavailable rather than infinity and prevents complete publication.

### 9.4 Flow-pressure proxies

For each completed bar:

```text
MFM = (2 * adjusted_close - adjusted_high - adjusted_low)
      / (adjusted_high - adjusted_low)
```

When adjusted High equals adjusted Low, MFM is defined as zero. The 1D metric is `flow_pressure_1d_proxy = MFM_today`.

For N in 5, 20, and 60, including the current completed session:

```text
cmf_N_proxy = sum(MFM * provider_volume, N) / sum(provider_volume, N)
```

If the volume denominator is zero or history is missing, the metric is unavailable. The unscaled range is `[-1, 1]`. API metadata reports `metric_semantics = "ohlcv_derived_proxy"`. These metrics are not measured inflow, fund flow, institutional flow, or trade-classified money flow.

## 10. Ranking and rank history

SPY is excluded. The six Phase 1 sector ranking metrics are:

- `return_1d`;
- `relative_return_vs_spy_5d`;
- `relative_return_vs_spy_20d`;
- `relative_return_vs_spy_60d`;
- `rvol20`;
- `cmf_20d_proxy`.

Each metric is ranked descending across exactly 11 sectors using dense rank. Equal values receive the same financial rank. Symbol is used only to make serialized output order stable and never to break a tie.

Previous rank comes from the latest successfully published snapshot whose trading session is earlier than the current session and whose metric version matches. Attempts that are PARTIAL, FAILED, or on the same session are ignored.

```text
rank_change = previous_rank - current_rank
```

Positive means `IMPROVED`, negative means `DECLINED`, zero means `UNCHANGED`, and absent prior rank means `NOT_AVAILABLE`. Phase 1 adds no strengthening/weakening signal beyond this descriptive change.

## 11. API contract

Add one router to the existing `/api/v1` application:

- `GET /api/v1/market-intelligence/sectors/latest` returns the pointer-selected complete published snapshot;
- `GET /api/v1/market-intelligence/sectors/history` returns published history filtered by trading date, symbol, and metric version, selecting the latest published revision for each session/version;
- `GET /api/v1/market-intelligence/sectors/health` returns the latest attempt and the latest published identity plus audit-derived Data Health.

`latest` includes `as_of`, `published_at`, `run_id`, provider, metric version, price basis, fixed universe, benchmark, `SUCCEEDED` status, source freshness, metric semantics, and sector items. The benchmark object exposes SPY's symbol, returns, RVOL20, flow proxies, and freshness but no sector rank. Each sector item includes returns, relative returns versus SPY, RVOL20, flow proxies, current ranks, previous ranks, rank changes, rank directions, and freshness.

`history` never merges different metric versions into an indistinguishable series. `latest` returns 404 only when no complete snapshot has ever been published; a newer failed attempt does not change that result.

No Phase 1 write or task-control endpoint is exposed.

## 12. Data Health contract

Data Health is populated from durable run audit fields and reports:

- universe expected count and symbols;
- symbols received, valid, usable, and missing;
- accepted and rejected rows;
- duplicate rows, invalid volume rows, invalid OHLC rows, and other rejection counts;
- provider and provider status;
- request failure classification when applicable;
- latest attempted run/date/status;
- latest complete published run/date;
- current run timestamp and provider response timestamp;
- source freshness, price basis, normalization version, and metric version;
- whether publication occurred.

The response distinguishes row rejection from provider/request failure and candidate status from published status.

## 13. Execution path

A focused orchestration use case performs provider fetch, validation, session completeness, pure metrics, predecessor lookup, rank calculation, status classification, and one final Unit of Work commit. A thin Celery task may invoke that use case through existing runtime wiring and queues; deterministic tests invoke the use case directly with a fixture provider and injected clock/calendar. Phase 1 does not require Redis or PostgreSQL merely to test pure validation, metrics, or ranking.

## 14. Testing strategy

Implementation follows strict red-green-refactor cycles in this order:

1. strict canonical validation and adjustment-lineage fixtures;
2. return, relative-return, RVOL20, and flow-proxy calculations;
3. dense ranking, ties, previous-published-session lookup, and rank direction;
4. persistence, idempotency, status classification, and atomic pointer behavior;
5. API latest/history/health contracts;
6. focused regression and full baseline comparison.

The golden fixture passes raw-like rows through validation, canonicalization, metrics, ranking, and snapshot construction. It contains rising, falling, high-RVOL, zero-range, zero-volume, negative-volume, invalid-OHLC, duplicate, unexpected-symbol, missing-row, and insufficient-history variants.

Required end-to-end deterministic cases are:

- 12/12 valid -> `SUCCEEDED` and pointer moves;
- 11/12 valid -> `PARTIAL` and pointer stays;
- zero usable after a successful response -> `FAILED` and pointer stays;
- request failure -> `FAILED`, no fabricated row rejections, pointer stays;
- previous published snapshot plus new PARTIAL -> `/latest` still serves previous;
- Monday success, Tuesday partial, Wednesday success -> Wednesday compares ranks with Monday;
- identical input rerun -> identical existing logical result and no duplicate rows.

Tests never call live Yahoo or another internet provider. PostgreSQL/Redis/Celery-worker integrations that cannot run on this Windows host are reported as blocked rather than skipped into apparent success.

## 15. Migration and rollback

The migration is additive: create the four Market Intelligence tables, indexes, uniqueness/check constraints, and foreign keys. Existing price, scan, Market RS, feature-store row, and pointer schemas remain intact.

Rollback procedure:

1. stop invoking the Market Intelligence task;
2. leave the named pointer unchanged or delete only the dedicated pointer row if explicitly required;
3. downgrade the additive migration to drop only Market Intelligence tables;
4. revert API/router/task wiring;
5. retain all existing feature-store, scan, price, and Market RS behavior.

A failed or partial run requires no rollback because it never changes the published pointer.

## 16. Environment and dependency constraints

The host is native Windows without Docker, WSL, PostgreSQL, or Redis. Phase 1 does not install or reconfigure those services and does not rewrite production architecture around their absence. Pure modules and fixture-provider tests run locally; database behavior uses the repository's explicitly allowed SQLite test harness where faithful, while PostgreSQL-specific integration remains blocked and documented.

No new Python or npm dependency is planned. Existing pandas, SQLAlchemy, Pydantic, FastAPI, yfinance, pytest, and Celery capabilities are sufficient. Existing baseline failures and npm advisories remain known baseline issues and are not hidden, fixed opportunistically, or reclassified.

## 17. Acceptance criteria

Phase 1 is complete only when all user-specified criteria are met, every new Phase 1 test passes, no new baseline regression appears, no secret or local device state is tracked, dependency manifests remain unchanged unless separately justified, final code review finds no publication/lineage/status inconsistency, and the implementation report documents commands, blocked integrations, transaction boundaries, and remaining baseline failures.

Completion ends the work. Phase 2 does not start automatically.
