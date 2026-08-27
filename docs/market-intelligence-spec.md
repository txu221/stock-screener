# Market Intelligence / Money Flow Architecture Specification

Status: Phase 1 implemented and verified

Date: 2026-08-26 (America/New_York)

Selected base: `xang1234/stock-screener` at `e65d1fc67db4b468471376aa29741fdce3759ffc`

## 1. Purpose and non-goals

This document defines how the selected upstream evolves into a US-equity Market Intelligence platform while preserving its working architecture. Sections 1–15 retain the Phase 0 architecture contract; sections 16–17 record the implemented Phase 1 vertical slice.

The product should answer four questions from one reproducible daily state:

1. What is the broad market doing?
2. Where is price/volume pressure strengthening or weakening across sectors and ETFs?
3. Which liquid stocks have unusual, technically meaningful changes?
4. What changed since the previous completed US trading session, and can every number be traced to its inputs?

The platform must not claim that OHLCV-derived indicators measure institutional purchases, signed trade flow, order-book imbalance, dark-pool activity, or fund creation/redemption flow. Those require different source data. Until such sources are contracted and validated, the product term **Money Flow** means **estimated price/volume pressure proxies** and the UI/API must say so.

Phase 0 explicitly excluded new metrics, new pages, a UI rewrite, provider integration, schema migration, and production deployment. Phase 1 implements only the approved backend sector vertical slice described below.

## 2. Architectural decision

Extend the base along its existing domain/use-case/infrastructure seams. Do not build a second analytics application beside it and do not replace the live/static dual delivery paths.

The target dependency direction is:

```text
Provider adapters
    -> immutable raw observations
    -> canonical normalized observations + quarantine
    -> point-in-time universe + completed-session snapshots
    -> versioned deterministic metrics
    -> signals and evidence
    -> cross-sectional ranks + change detection
    -> daily intelligence read model
    -> FastAPI and static JSON
    -> existing React shell
```

Redis remains an expendable cache/lock/broker. PostgreSQL remains the system of record. Celery remains the asynchronous execution boundary. Readers see only an atomically published, complete run. A provider outage may make a section unavailable or stale; it must never silently substitute fabricated zeros or partially publish a new snapshot.

## 3. Mapping the selected base to the target pipeline

| Target stage | Existing base to retain/extend | Gap to close incrementally |
|---|---|---|
| Provider ingestion | `data_source_service`, `provider_routing_policy`, `provider_circuit_breaker`, `rate_budget_policy`, market-specific adapters, `ProviderSnapshotRun/Row/Pointer` | Add a US price-observation adapter contract with explicit source timestamps, request identity, license/entitlement metadata, and structured failure codes |
| Normalization | `price_row_normalization`, `stock_price_persistence`, canonical symbol and market services | Preserve raw row lineage; enforce OHLC relationships, non-negative volume, session/calendar, duplicate, timezone, split/adjustment, and future-date rules; quarantine invalid rows |
| Durable history | PostgreSQL `StockPrice`, fundamentals, security master/universe tables | Price rows currently lack provider, source timestamp, adjustment provenance, and ingestion-run identity; add lineage without breaking existing readers |
| Point-in-time universe | `point_in_time_universe_service`, `bounded_history_universe`, security master and universe ingestion | Define the initial US equity/ETF eligibility policy, membership effective dates, and delisting/survivorship behavior |
| Daily snapshots | feature-store `FeatureRun`, run universe, feature values, `FeatureRunPointer`; `daily_snapshot_service`; provider snapshot atomic pointers | Add Market Intelligence run type and a single US session anchor; never mix dates across sections; retain formula/input/universe hashes |
| Quant metrics | technical calculator, RS services, breadth calculator, group ranking calculators | Add a metric registry, explicit formula versions, warm-up/undefined semantics, evidence fields, and money-flow proxy labels |
| Signals | scan domain, detector/setup-engine contracts, opportunity-state evidence | Add pure signal evaluators over materialized metrics; separate facts, thresholds, severity, and human-readable explanation |
| Ranking/change | canonical group rankings, historical group ranks, RRG, market RS snapshots | Add explicit 1D/5D/20D/60D sector/ETF factor ranks, rank delta, score delta, velocity, and acceleration based only on published prior sessions |
| Read model/API | `daily_snapshot_service`, thin FastAPI routers, ETag/cache, static artifact builders | Introduce a versioned intelligence snapshot envelope shared by server and static modes; preserve existing endpoints during migration |
| UI | React/Vite shell, Daily Snapshot, Breadth, Groups, Watchlists, Operations, static mode | Add sections gradually behind capability flags using existing visual language; no navigation or design-system rewrite |
| Data Health | validation service/UI, operations jobs/alerts, freshness gates, runtime activity | Surface row rejects, coverage, source lag, last successful run, formula version, degraded sections, and publish decision in one contract |

The existing legacy service layer and newer domain/use-case layer coexist. New work belongs in the newer boundaries where practical, but Phase 1 must not begin a broad migration. Adapters may bridge existing tables until a narrowly scoped migration is justified.

## 4. Canonical data contracts

### 4.1 Instrument identity

`Instrument` is stable internal identity, not a ticker string.

Required fields:

- `instrument_id`: immutable internal UUID/integer;
- `instrument_type`: `common_stock | adr | etf | index | other`;
- `primary_exchange`, `mic`, `currency`, `country`;
- `symbol` plus effective start/end dates in a separate symbol history;
- `active_from`, `active_to`, `delisted_at` where known;
- sector/industry taxonomy plus taxonomy name and version;
- ETF category and benchmark relationships where applicable;
- source and effective-date lineage for every membership/classification assertion.

All calculations use `instrument_id`. API responses may include the effective symbol for display. This avoids merging different securities that reused a ticker and preserves historical symbol changes.

### 4.2 Raw market observation

Raw payloads are immutable evidence and should be retained according to provider terms and storage policy.

`RawObservation` fields:

- `ingestion_run_id`, `provider`, `provider_dataset`, `provider_request_id`;
- provider symbol and resolved `instrument_id` (nullable while unresolved);
- `observed_at` or market session date, `received_at`, provider timezone;
- raw payload or content-addressed object reference and SHA-256;
- response status, entitlement/delay classification, adjustment mode;
- parser name/version and legal retention class.

Secrets, cookies, and authorization headers are never persisted.

### 4.3 Canonical daily bar

`DailyBar` is keyed by `(instrument_id, session_date, adjustment_basis, source_revision)` during staging. The published canonical view selects exactly one accepted revision according to provider policy.

Required values and metadata:

- open, high, low, close, volume, optional adjusted close;
- session date, exchange calendar, currency;
- raw observation/run identity, provider, provider timestamp, received timestamp;
- adjustment basis (`raw`, `split_adjusted`, `total_return_adjusted`) and corporate-action revision;
- normalization status, validator version, quality flags;
- published revision and superseded revision links.

Do not coerce missing or invalid volume to zero. `NULL` means unknown; `0` means the provider explicitly reported zero and passed validation.

### 4.4 Validation and quarantine

Validation runs before canonical publication and produces stable machine-readable reason codes.

Hard rejects include:

- non-finite or non-numeric OHLCV;
- negative price or volume;
- `high < max(open, close, low)` or `low > min(open, close, high)`;
- duplicate canonical key with conflicting values and no revision identity;
- session outside the instrument's exchange calendar;
- observation in the future relative to the run's knowledge cutoff;
- unresolved/ambiguous instrument identity;
- impossible timezone or adjustment metadata.

Soft flags include extreme returns/volume, stale repeats, unexpected zero volume, large vendor divergence, late corrections, and incomplete warm-up history. Soft flags remain visible to downstream metric eligibility rules; they are not silently discarded.

`QuarantinedObservation` retains the raw reference, parser/validator version, reason codes, first/last seen timestamps, and resolution state. Data Health reports counts and affected instruments by reason.

### 4.5 Fundamental, filing, and event observations

Fundamentals and filings are bitemporal: store both the effective/reporting period and the time at which the platform could have known the value. A later restatement creates a new revision; it must not overwrite historical knowledge for backtests.

Delayed filings/news are a later phase. Until a licensed source and temporal contract exist, **What Changed** is derived only from internal market snapshots. News language must never be generated as causal fact from price action alone.

## 5. Daily run, lineage, and publication

### 5.1 Trading-session anchor

Every Market Intelligence snapshot is anchored to one completed XNYS/Nasdaq-compatible US session date. The market calendar service determines the expected session and close. Holidays and early closes are explicit. Calendar date subtraction is forbidden for return windows and comparisons.

A run may begin only when the configured price completeness threshold for that session is met or when an operator explicitly publishes a degraded snapshot. Degraded publication records its missing sections and reason; it never reuses old data while labeling it current.

### 5.2 Run identity

Extend the existing feature-run pattern. A run records:

- immutable `run_id`, `run_type`, `market`, `as_of_session`, status;
- knowledge cutoff and published timestamp;
- code commit, metric-registry version, config hash;
- provider observation revisions and source cutoff;
- universe definition/version/hash and exact member set;
- input hash, output hash, row counts, quarantine counts;
- warnings, degraded capabilities, quality-gate results;
- predecessor run and recomputation reason.

The deterministic run key is the hash of market, session, universe, accepted input revisions, metric versions, and configuration. Equal run keys must produce equal outputs. Reprocessing corrected vendor data produces a new revision, never an invisible mutation.

### 5.3 Atomic publication

Build and validate all sections under an unpublished run. Publish in one transaction by advancing the existing pointer pattern. APIs and static export resolve the pointer first and then read only rows belonging to that run. A failed build leaves the previous pointer intact and Data Health reports the failure/staleness.

The daily read model includes section-level `as_of`, `status`, `coverage`, and lineage. Cross-section ranks must never mix candidate rows from different run IDs.

## 6. Metric registry and deterministic semantics

Every metric is registered with:

- stable metric key and formula version;
- input series and adjustment basis;
- exact trading-session lookback and minimum observations;
- null/zero/denominator behavior;
- clipping/winsorization and cross-sectional universe rules;
- output units/range and rounding policy;
- point-in-time and quality eligibility policy;
- golden fixture and property/invariant tests;
- owner, changelog, and effective run version.

Store full-precision numeric outputs; round only in presentation. Missing or insufficient data yields `NULL` plus an evidence reason, never `999`, `0`, or an arbitrary neutral score.

### 6.1 Returns

For a `k`-session close-to-close return:

`return_k = close_t / close_(t-k) - 1`

Use a declared adjustment basis and exact sessions from the calendar. A 52-week high uses the previous/current 252 eligible sessions as explicitly configured; the breakout rule states whether today's bar is included.

### 6.2 Relative volume

Daily RVOL for completed session `t`:

`rvol_N = volume_t / mean(volume_(t-N) ... volume_(t-1))`

The current observation is excluded from the baseline. Require a configurable minimum count and a positive denominator. Intraday RVOL must be a different metric based on same-time-of-day historical cumulative volume; daily RVOL must not be presented as intraday-normalized.

### 6.3 Chaikin Money Flow proxy

For valid bars with `high != low`:

`mfm_t = ((close_t - low_t) - (high_t - close_t)) / (high_t - low_t)`

`mfv_t = mfm_t * volume_t`

`cmf_N = sum(mfv, N) / sum(volume, N)`

If `high == low`, the bar multiplier is explicitly `0` and the evidence records the flat-range count. Require positive window volume. Output is normally `[-1, 1]`; multiplying by 100 is a display convention only. Label: **CMF price/volume pressure proxy**, not net capital flow.

### 6.4 Money Flow Index proxy

Use typical price `(high + low + close) / 3`, raw money flow `typical_price * volume`, and classify positive/negative flow by the sign of the typical-price change. Define unchanged-price handling and zero-negative-flow behavior explicitly. Output is `[0, 100]`. Label it an OHLCV momentum/volume proxy.

### 6.5 On-Balance Volume

OBV is a cumulative path-dependent series:

- add volume when close rises;
- subtract volume when close falls;
- unchanged close adds zero.

Raw OBV level is not cross-sectionally comparable and must not be converted to a percentage of an arbitrary origin. Expose only declared transforms such as `OBV slope_N`, `OBV z-score_N`, or divergence versus price, with minimum history and regression method specified.

### 6.6 Close-location and estimated pressure

Two distinct metrics avoid mislabeled formulas:

- `close_location_01 = (close - low) / (high - low)` in `[0,1]`;
- signed `close_location_value = 2 * close_location_01 - 1` in `[-1,1]`.

`close_location_value * volume` or `* typical_price` may be aggregated only under an **estimated pressure** label. It is not dollar inflow/outflow, because the sign of actual trades is unknown and multiplying by price does not create measured net flow.

### 6.7 Relative strength and breadth

Stock/ETF relative strength returns are computed versus an explicit benchmark using point-in-time data. Percentile ranks are over the complete eligible universe for the same run, with a declared tie method and minimum coverage.

Breadth records numerator, denominator, eligible universe hash, and coverage—not only a ratio. Initial reusable measures may include advance/decline participation, percentage above moving averages, new highs/lows, and existing threshold-mover counts. Delisted names and membership dates must be handled point-in-time to avoid survivorship bias.

## 7. Sector and ETF rotation

### 7.1 Initial universe

The first sector layer is the 11 Select Sector SPDR ETFs, with SPY as benchmark. The universe is configuration data with effective dates and provenance, not constants scattered through UI code. Industry/subsector ETFs can follow only after the sector path is stable.

### 7.2 Factor contract

For each sector/ETF and each published session, materialize transparent factor values and ranks for 1D, 5D, 20D, and 60D horizons. Candidate factors:

- absolute return;
- relative return versus SPY;
- volume anomaly (daily RVOL);
- CMF/MFI or OBV-transform pressure proxies;
- distance from trend baselines;
- breadth/participation of constituents where point-in-time membership is available.

No hidden score is permitted. Each composite response includes raw factor, transformed value, cross-sectional percentile, weight, weighted contribution, missing-data policy, and formula version.

### 7.3 Rank change and acceleration

For factor score `s_t` and rank `r_t` where rank 1 is best:

- `score_delta_1 = s_t - s_(t-1)`;
- `rank_improvement_1 = r_(t-1) - r_t`;
- `velocity_k = (s_t - s_(t-k)) / k`;
- `acceleration_k = velocity_k - velocity_k_at_(t-k)` or a separately versioned regression-slope change.

Never call a one-day rank jump acceleration. UI shows both score and rank deltas because ranks may change when peers move even if the instrument's score does not.

RRG is retained as a visualization/read model, not the sole source of the rotation score.

## 8. Transparent ETF score

The donor ETF score is not reusable as-is. The target score is a configuration/versioned weighted composite over normalized factors.

Example contract, to be calibrated rather than assumed:

`score = sum(weight_i * percentile_i) / sum(weights_available)`

Rules:

- weights sum to 1 for a complete row;
- available-factor reweighting is allowed only above a minimum coverage threshold and is disclosed;
- factor direction is explicit (higher-is-better or inverted);
- winsorization and percentile tie handling are fixed;
- score is bounded to `[0,100]`;
- response includes every contribution and a confidence/coverage grade;
- score changes create structured deltas, not narrative certainty;
- backtests use point-in-time universe, walk-forward thresholds, transaction costs, and no revised macro data.

The score is decision support, not an investment recommendation or a claim about fund flows.

## 9. Movers and anomaly engine

The Movers surface operates only on an eligible, liquid universe. A default policy should be proposed and validated in Phase 1, for example minimum price, market capitalization, 20-day median dollar volume, trading history, and acceptable data-quality coverage. Thresholds are configuration with versions.

Candidate facts/signals:

- 1D/5D/20D return and gap;
- daily RVOL and robust volume z-score using median/MAD;
- 52-week high/low breakout with configurable confirmation;
- price/volume pressure proxy extremes;
- sector-relative strength;
- breadth participation and regime context.

Each result contains eligibility facts, raw metrics, threshold comparisons, triggered signal IDs, data-quality flags, and run lineage. Robust statistics are preferred for heavy-tailed volume. Winsorization must not hide bad raw inputs; validation occurs first.

## 10. “What Changed” contract

The engine compares two atomically published snapshots for adjacent completed sessions (or an explicitly chosen interval). It emits typed deltas:

- instrument entered/exited a ranked set;
- sector/ETF rank or score changed beyond a configured threshold;
- signal activated, strengthened, weakened, or cleared;
- breadth regime crossed a declared boundary;
- source coverage/freshness changed;
- a prior session was recomputed due to corrected input.

Each change record contains `before_run_id`, `after_run_id`, metric/signal version, before/after values, delta, threshold, evidence, severity rule, and generated timestamp. Explanations use deterministic templates over those facts. An LLM may later summarize records, but cannot invent causes or replace the structured record.

News/filing correlation is a separate later capability. It must use publication timestamps, delayed/licensed-source labeling, and language such as “coincided with,” not unverified causation.

## 11. API and read-model boundaries

Use `/api/v1/market-intelligence` as a versioned namespace while preserving existing APIs.

Proposed endpoints:

- `GET /daily?market=US&as_of=latest` — one coherent Today read model;
- `GET /movers?as_of=&kind=&page=&sort=` — eligible movers with evidence;
- `GET /sectors?as_of=&horizon=` — 11-sector factor/rank table and changes;
- `GET /etfs?as_of=&category=&horizon=` — ETF ranks and score breakdowns;
- `GET /instruments/{id}/history?metrics=` — bars, versioned metrics, and signals;
- `GET /changes?from=&to=&scope=` — structured change records;
- `GET /watchlists/{id}/intelligence?as_of=` — existing watchlist membership joined to the same published run;
- `GET /data-health?as_of=` — ingestion, validation, coverage, freshness, lineage, and publish status;
- `GET /definitions/metrics` — formula/version/units/warm-up definitions.

All snapshot responses include:

```json
{
  "schema_version": 1,
  "run_id": "...",
  "market": "US",
  "as_of_session": "YYYY-MM-DD",
  "published_at": "...Z",
  "status": "complete|degraded",
  "freshness": {},
  "lineage": {},
  "capabilities": {},
  "data": {}
}
```

Use cursor or stable keyset pagination for large ranked sets. Sort keys include deterministic tie-breakers. ETags derive from published output hash. Server and static JSON use the same Pydantic/domain response contract so parity remains testable.

## 12. Data Health and graceful degradation

Data Health is a product surface, not only logs. For each provider, dataset, market, and stage expose:

- expected/latest session, last attempt, last success, source timestamp, and lag;
- raw/accepted/quarantined/updated row counts and rejection reasons;
- expected/eligible/computed universe counts and coverage percentage;
- duplicates, missing sessions, stale repeats, vendor divergence, and late revisions;
- active run, predecessor, formula/config/code versions, and hashes;
- cache age separately from source age;
- failed/degraded sections and the exact publication decision;
- legal/entitlement delay classification where relevant.

Rules:

- Redis failure degrades caching but not canonical correctness.
- One provider failure may fall back only through an explicit routing policy whose source is disclosed.
- Missing data remains missing. Never turn provider failure into zero activity.
- A section that cannot meet its coverage gate is unavailable with reason, while independent valid sections may publish as degraded.
- The previous run may remain active, but the UI must show its true as-of session and staleness.

## 13. Testing and reproducibility strategy

### Deterministic tests (network forbidden)

- pure formula unit tests with hand-calculated fixtures;
- property tests for bounds, monotonicity, nulls, zero denominators, and invariance;
- OHLCV validator tests including negative volume and relational bounds;
- calendar/session and point-in-time/no-lookahead tests;
- golden daily snapshots and factor breakdowns;
- universe/rank tests for ties, missing factors, entrants/exits, and survivorship;
- publication atomicity and stale-pointer failure tests;
- server/static API contract parity tests;
- change-record before/after fixtures;
- provider adapter contract tests using recorded, legally retainable fixtures.

### Integration tests (local services, network mocked)

- PostgreSQL migrations and transactions;
- Celery task idempotency/retry/failure behavior;
- Redis cache/lock outage degradation;
- end-to-end raw -> normalize -> snapshot -> publish -> API using fixed fixtures;
- recomputation from a corrected source revision;
- frontend rendering of complete, degraded, stale, and unavailable states.

### Live/provider checks (explicit opt-in)

Live checks are marked separately, rate-limited, never required for deterministic CI, and report provider drift rather than rewriting golden outputs. Credentials come only from environment/secret stores. Record provider terms and retention restrictions beside adapters.

Every metric release requires formula documentation, fixtures, golden output review, migration/recompute plan, and a new version when semantics change.

## 14. Security, licensing, and compliance boundaries

- Preserve the base's Apache-2.0 license, copyright/patent notices, and modification notices. Preserve donor MIT notices if code is later copied.
- A code license does not grant rights to redistribute market data. Review each provider's display, caching, derivative-data, and redistribution terms before implementation.
- Yahoo/Finviz/TradingView-style unofficial access is an operational and legal risk. Provider adapters must be replaceable and their datasets must carry entitlement/retention metadata.
- API keys and credentials never enter logs, snapshots, raw payload storage, static bundles, or the browser.
- User watchlists are private application data and must not leak into public static exports.
- Explanations must distinguish observed fact, deterministic inference, proxy metric, and unavailable data.

## 15. Incremental delivery plan

### Phase 1 — smallest vertical slice (recommended)

Build one fixture-first, US daily sector-pressure slice over SPY plus the 11 Select Sector SPDR ETFs:

1. introduce the canonical accepted-bar/lineage seam using an existing provider path;
2. reject/quarantine invalid OHLCV, especially negative volume and broken price ranges;
3. materialize one completed-session run with 1D/5D/20D/60D returns, daily RVOL, and CMF proxy;
4. produce transparent factor values/ranks and prior-session deltas;
5. publish atomically through the existing feature-run/pointer pattern;
6. expose versioned latest/history/health read endpoints and a durable Data Health payload;
7. keep Phase 1 backend-only; do not add a frontend or a second static publication path;
8. prove deterministic formulas, no-lookahead behavior, API contracts, idempotency, and degraded-source behavior with fixtures.

This slice is deliberately only 12 ETFs, completed daily bars, and three metric families. It validates the hardest contracts—lineage, time, formula determinism, rank/change semantics, publication, and degradation—before expanding the universe.

### Later increments

- Phase 2: broader ETF catalog and transparent composite score after walk-forward validation.
- Phase 3: liquid US stock movers, volume anomalies, and 52-week breakouts.
- Phase 4: watchlist intelligence and historical change views.
- Phase 5: point-in-time constituent breadth and industry/subsector rotation.
- Phase 6: licensed filings/news correlation and, only if separately sourced, measured flow datasets.

Each increment must leave existing scan, breadth, groups, watchlist, operations, live, and static paths working. Feature flags and additive migrations precede cutovers; obsolete paths are removed only after parity evidence and explicit approval.

## 16. Phase 1 decision record

The Phase 1 design was approved on 2026-08-26 with the following decisions:

- universe is fixed to SPY plus XLC, XLY, XLP, XLE, XLF, XLV, XLI, XLB, XLRE, XLK, and XLU;
- primary provider is the existing Yahoo/yfinance bulk path, with no Phase 1 fallback provider;
- canonical price basis is adjusted OHLC derived from `Adj Close / Close`, with provider-reported volume left unadjusted;
- raw OHLC, provider adjusted close, adjustment factor, adjusted OHLC, provider volume, provider identity, and timestamps remain traceable;
- returns use completed trading-session offsets of 1, 5, 20, and 60;
- Phase 1 relative performance is `sector_return_N - SPY_return_N` and is separate from existing Market RS;
- RVOL20 excludes the current session from its previous-20-session mean;
- flow metrics are unscaled `[-1, 1]` OHLCV-derived proxies named `flow_pressure_1d_proxy`, `cmf_5d_proxy`, `cmf_20d_proxy`, and `cmf_60d_proxy`;
- no Net Dollar Flow, measured flow claim, opportunity score, rotation prediction, or composite factor is implemented;
- sector metrics use descending dense rank over only the 11 sectors;
- previous rank comes only from the prior successfully published trading-session snapshot of the same metric version;
- `rank_change = previous_rank - current_rank`, with explicit direction metadata;
- `SUCCEEDED` requires 12/12 complete and may publish; `PARTIAL` requires at least one usable candidate but never publishes; `FAILED` covers request failure or zero usable candidates and never publishes;
- `FeatureRun`, `FeatureRunPointer`, Unit of Work, and the existing atomic pointer move remain the only publication lifecycle;
- `/latest` serves the last complete published snapshot while `/health` independently reports the latest attempt;
- metric version is `market_intelligence_v1` and normalization version is `market_intelligence_adjusted_ohlcv_v1`;
- Phase 1 exposes API output only and does not add frontend or static JSON publication;
- no infrastructure installation or new dependency is planned for the native-Windows implementation environment.

The detailed component, data-model, transaction, idempotency, API, test, migration, and rollback contracts are fixed in `docs/superpowers/specs/2026-08-26-market-intelligence-phase-1-design.md`.

## 17. Phase 1 implemented contract

### 17.1 Universe, provider, and lineage

The universe is immutable for `market_intelligence_v1`: benchmark `SPY`; sector ETFs `XLC`, `XLY`, `XLP`, `XLE`, `XLF`, `XLV`, `XLI`, `XLB`, `XLRE`, `XLK`, and `XLU`. The primary provider is the existing Yahoo `BulkDataFetcher` path with a six-month request. There is no Phase 1 fallback; a request-level outage produces a `FAILED` attempt.

The canonical basis identifier is `yahoo_adjusted_ohlc_provider_volume`. For every accepted row:

```text
adjustment_factor = provider_adjusted_close / raw_close
adjusted_open  = raw_open  * adjustment_factor
adjusted_high  = raw_high  * adjustment_factor
adjusted_low   = raw_low   * adjustment_factor
adjusted_close = provider_adjusted_close
volume         = provider-reported volume (not split-adjusted or coerced)
```

Canonical evidence retains provider and provider symbol, raw trading date and OHLC, provider adjusted close, adjustment factor, adjusted OHLC, provider volume, source timestamp, ingestion timestamp, price basis, and normalization version. The bounded calendar adapter returns the full provider-validation window (at least 90 completed sessions); metrics use exact trailing session anchors. No OHLCV forward fill is performed.

### 17.2 Validation and quarantine

Every response row presented to the use case is validated before canonical persistence. The validator requires an expected symbol and completed session; all required values; finite numeric OHLCV and adjusted close; strictly positive raw prices and adjusted close; non-negative volume; a finite positive adjustment factor; `high >= low/open/close`; `low <= open/close`; and one row per `(symbol, trading_date)`. Stable rejection codes are `UNEXPECTED_SYMBOL`, `MISSING_REQUIRED_FIELD`, `INVALID_TRADING_DATE`, `NON_FINITE_VALUE`, `NON_POSITIVE_PRICE`, `INVALID_ADJUSTED_CLOSE`, `INVALID_ADJUSTMENT_FACTOR`, `INVALID_OHLC_RELATION`, `NEGATIVE_VOLUME`, and `DUPLICATE_BAR`.

Rows are never repaired with `abs`, clipping, high/low swaps, zero coercion, or silent drops. Rejections retain run, provider, provider symbol, symbol/date when known, code, reason, raw evidence, and ingestion timestamp. A request-level timeout/authentication/network/bad-response failure is stored in the run audit and creates no fabricated row rejections. Repeated request failures receive distinct attempt identities; successful and row-invalid responses remain content-addressed and idempotent.

### 17.3 Metrics

All prices below use adjusted close and exact completed trading-session offsets:

```text
return_N = adjusted_close_today / adjusted_close_N_sessions_ago - 1
relative_return_vs_spy_N = sector_return_N - SPY_return_N
RVOL20 = volume_today / mean(volume over previous 20 completed sessions)
MFM = (2 * adjusted_close - adjusted_high - adjusted_low)
      / (adjusted_high - adjusted_low)
CMF_N_proxy = sum(MFM * provider_volume over N sessions)
              / sum(provider_volume over N sessions)
```

Implemented return and relative-return horizons are 1, 5, 20, and 60 sessions. RVOL excludes today; zero historical mean returns unavailable. Zero-range MFM is defined as `0`; zero CMF volume denominator returns unavailable. Insufficient/misaligned/duplicate history and non-finite results return unavailable, never zero. Proxy fields are `flow_pressure_1d_proxy`, `cmf_5d_proxy`, `cmf_20d_proxy`, and `cmf_60d_proxy`, with API metadata `metric_type=derived_proxy` and `metric_semantics=ohlcv_derived_proxy`. Phase 1 has no Net Dollar Flow or measured/institutional-flow claim.

### 17.4 Ranking and rank change

Only the 11 sector ETFs enter ranking; SPY is excluded. Descending dense rank is applied independently to `return_1d`, `relative_return_vs_spy_5d`, `relative_return_vs_spy_20d`, `relative_return_vs_spy_60d`, `rvol20`, and `cmf_20d_proxy`. Equal values share a rank; symbol ordering only stabilizes output and never breaks a financial tie.

Previous ranks come from the prior successfully published trading-session snapshot with the same metric version, excluding calendar gaps and `PARTIAL`/`FAILED` attempts. `rank_change = previous_rank - current_rank`: positive is `IMPROVED`, negative is `DECLINED`, zero is `UNCHANGED`, and missing history is `NOT_AVAILABLE`. No rotation prediction is generated.

### 17.5 Run and publication semantics

Statuses are mutually exclusive and exhaustive:

- `SUCCEEDED`: all 12 symbols have valid canonical history, all required metrics, a complete 12-row snapshot, and all six 11-sector ranks;
- `PARTIAL`: the request succeeded and at least one symbol is usable, but missing/rejected/provider-failed/insufficient/metric-incomplete data prevents completeness;
- `FAILED`: request-level failure, zero usable symbols, or no usable candidate snapshot.

Only `SUCCEEDED` transitions to published. `PARTIAL` is audit-only/quarantined and `FAILED` is failed; neither moves the pointer. The implementation reuses `FeatureRun`, `FeatureRunPointer`, `SqlUnitOfWork`, and the existing feature-run lifecycle. Canonical bars, rejections, audit, snapshots, final run status, and eligible pointer change commit in one UoW. PostgreSQL uses a stable transaction advisory lock before the pointer row lock, including when the pointer does not yet exist. The pointer only moves to an equal or newer `as_of` session; same-session revisions use a lock-ordered publication timestamp consistent with history ordering. An older successful backfill remains historical but cannot move `/latest` backward. If commit fails, none of those writes becomes visible.

Idempotency is content-addressed by pipeline, trading date, fixed-universe hash, input hash, normalization version, and metric version. Identical non-request-failure input reuses its logical run; a unique-key race reads the committed winner. Logical snapshot identity is `(run_id, symbol)`, while the audit key prevents duplicate logical runs for the same content.

### 17.6 Persistence, Data Health, and API

Migration `20260826_0031` adds `market_intelligence_run_audits`, `market_intelligence_canonical_bars`, `market_intelligence_rejections`, and `market_intelligence_sector_snapshots`, all tied to existing `feature_runs`. Derived snapshots record trading date, identity, every metric, current/previous/change/direction ranks, provider/freshness, price basis, `market_intelligence_v1`, calculation time, and data-quality status. Normalization is versioned as `market_intelligence_adjusted_ohlcv_v1`.

Data Health reads persisted audit truth, including expected/received/usable symbols, valid/rejected bars, missing symbols, duplicate rows, invalid volume/OHLC, provider status/failures, request failure, latest attempted run, latest published run, publication occurrence, freshness, timestamps, price basis, and metric/normalization versions.

The stable read contract is:

- `GET /api/v1/market-intelligence/sectors/latest`: the pointer-selected last complete published snapshot, even after a newer partial/failed attempt;
- `GET /api/v1/market-intelligence/sectors/history`: published history filterable by date, symbol, and metric version, using the newest published revision per session;
- `GET /api/v1/market-intelligence/sectors/health`: latest attempt plus independent latest published state and audit counters.

Phase 1 adds no frontend and no second static JSON publisher. PostgreSQL migration execution, true concurrent pointer/unique-conflict behavior, Redis, and a real Celery worker remain integration checks blocked by the approved native-Windows environment.
