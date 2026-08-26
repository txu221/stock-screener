# Upstream Repository Research

Audit date: 2026-08-25 (America/New_York)

## Decision

### PRIMARY BASE

**[`xang1234/stock-screener`](https://github.com/xang1234/stock-screener)** at commit `e65d1fc67db4b468471376aa29741fdce3759ffc`.

This changes the brief's provisional preference for `Mamun/stockIQ`. The decisive reason is architectural, not cosmetic: `stock-screener` already has the durable and testable substrate that this product needs—FastAPI/React boundaries, PostgreSQL historical state, Redis/Celery orchestration, daily feature snapshots, atomic publish pointers, breadth, group rankings/RRG, watchlists, freshness gates, provider routing, operational screens, and a large deterministic test suite. Building those capabilities on `stockIQ` would require replacing most of its single-process Streamlit/data layer and would be much closer to a new platform build.

The selected base is substantially more complex to operate. That cost is accepted because it avoids reimplementing the product's core data platform. Phase 1 must narrow it to a US-only vertical slice and extend existing boundaries instead of expanding its already broad surface.

### DONOR / REFERENCE PROJECTS

- **`Mamun/stockIQ`** — donor for compact technical-analysis UX, configuration patterns, ETF universe curation, and small pure indicator helpers. Its current ETF score must not be reused unchanged.
- **`mphinance/scanline`** — donor for a clean request/response screener contract, shared HTTP/MCP pipeline, explainable cross-sectional normalization, and strict separation of offline and live tests.
- **`brianbeals/sector-rotation-screener`** — donor for point-in-time sector scoring, ALFRED vintage handling, walk-forward validation, and transparent relative-strength/acceleration formulas.
- **`czcode0501/sector-flow`** — formula reference only for OHLCV-derived pressure proxies. Its labels and implementations require correction and tests before any reuse.
- **`bankrollhunter/market-breadth`** — design reference only for breadth heatmaps and universe-level MA participation.
- **`SamPom100/UnusualVolumeDetector`** — concept reference only for volume anomaly scanning; its implementation is not suitable for reuse.

No code has been copied from any donor during Phase 0.

## Research method and search scope

The audit did not stop at README files. Each deep-audited repository was cloned to an OS temporary directory and inspected for:

- repository structure and commit history;
- dependency and configuration manifests;
- provider implementations and external-service boundaries;
- cache and persistence strategy;
- deterministic calculations and ranking formulas;
- sector/ETF universes and screener filters;
- tests, CI workflows, and live/offline separation;
- API and frontend architecture;
- data models, provenance, freshness, and validation;
- license text and redistribution obligations;
- obvious technical debt and data-quality failure modes.

GitHub searches used the terms `stock dashboard`, `stock screener`, `unusual volume`, `sector rotation`, `ETF screener`, and `market breadth`. They surfaced, among others, `xang1234/stock-screener`, `bankrollhunter/market-breadth`, and `SamPom100/UnusualVolumeDetector`. Repositories with no declared license, a non-US primary market, or a narrow notebook/demo architecture were screened out before deep audit. Examples include otherwise interesting results such as `jeffreyrdcs/stock-vcpscreener` (no declared license) and multiple India/A-share-only screeners.

### Research-count deviation and selection saturation

The Phase 0 plan recorded an explicit deep-audit floor of five repositories; seven licensed, relevant candidates were ultimately compared. The later Phase 1 brief refers to an expected count of ten. The audit stopped at seven because additional search results repeatedly fell into already represented or disqualifying classes: unlicensed code, single-notebook demonstrations, non-US primary-market tools, thin yfinance dashboards without durable state, or narrow formula examples with no tests. At that point the base-selection decision had reached saturation: no remaining result exposed a platform substrate comparable to `stock-screener`, while the strongest smaller candidates had already been retained as donors for API, sector-rotation, and formula ideas.

This count variance does not weaken the PRIMARY BASE conclusion. The selection rests on directly inspected architecture, persistence, provider boundaries, atomic publication, test depth, and licensing rather than on a marginal score difference among similar dashboards. Three more lightweight dashboard audits would add names without changing the missing-platform-layer comparison. Phase 1 therefore records the deviation explicitly and proceeds without reopening the base decision or delaying the approved 12-symbol vertical slice.

## Comparable scorecard

Scores are 1 (poor) to 5 (strong) for this product, based on audited source rather than popularity.

| Repository | Function fit | Architecture | Code quality | Testing | Maintainability | Data foundation | License | Activity signal | Role |
|---|---:|---:|---:|---:|---:|---:|---|---|---|
| `xang1234/stock-screener` | 5 | 5 | 4 | 5 | 3 | 5 | Apache-2.0 | 2,251 commits; 913 in last 90 days; recent CI success | **PRIMARY BASE** |
| `Mamun/stockIQ` | 3 | 3 | 3 | 3 | 4 | 2 | MIT | 258 commits, but all 90 recent commits were nightly cache bot commits; last non-bot commit 2026-05-12 | Donor |
| `mphinance/scanline` | 3 | 4 | 4 | 4 | 4 | 2 | MIT | 60 commits in last 90 days; latest scheduled live-data runs failing | Donor |
| `brianbeals/sector-rotation-screener` | 4 | 3 | 4 | 4 | 4 | 3 | MIT | 85 total commits; 54 in last 90 days; weekly workflow passing | Donor |
| `czcode0501/sector-flow` | 3 | 2 | 2 | 1 | 3 | 1 | MIT | 5 total commits; no CI | Formula reference |
| `bankrollhunter/market-breadth` | 2 | 1 | 2 | 1 | 1 | 2 | MIT | Last code activity 2020-11-08 | Design reference |
| `SamPom100/UnusualVolumeDetector` | 2 | 1 | 1 | 1 | 1 | 1 | MIT | 567 historical commits; no meaningful code activity since 2024 | Concept reference |

Repository size and test inventory observed at the audited commits:

| Repository | Audited SHA | Files | Test inventory observed |
|---|---|---:|---|
| `xang1234/stock-screener` | `e65d1fc67db4b468471376aa29741fdce3759ffc` | 1,832 | 462 backend unit modules, 13 integration modules, 89 frontend test modules, plus parity/performance/load/golden tests |
| `Mamun/stockIQ` | `092f2feab1e3ce4e5a46633a44a0498cb2f7734b` | 148 | 5 substantive pytest modules |
| `mphinance/scanline` | `aaf6ca7b3818cf838b9219a6e462a03b20204b59` | 78 | 2 Python suites and 2 Node test modules; live tests marked separately |
| `brianbeals/sector-rotation-screener` | `9428d7a38493efbaae9cf656c94cea9ebd6a0f3c` | 102 | 6 pytest modules |
| `czcode0501/sector-flow` | `79ebdc718c91d374fbd00fbdfee12c68efe367a3` | 13 | None |
| `bankrollhunter/market-breadth` | `70df4e75b17c2c462cdb2cb06b50e482083fc275` | 30 | None |
| `SamPom100/UnusualVolumeDetector` | `b240d5bf441ccb769e810a086954fcd8da69493d` | 17 | None |

The file/test counts describe source inventory, not Phase 0 baseline results. Baseline execution is recorded separately in `docs/baseline-audit.md` after importing the selected base.

## Candidate audits

### 1. xang1234/stock-screener — PRIMARY BASE

**Functional fit.** The repository already covers stock screening, breadth, daily snapshots, group rankings, RRG views, themes, watchlists, stock details, operational health, and static publishing. These overlap directly with the desired Today, Movers, Sectors, Stocks, Watchlist, History, and Data Health foundations. ETF/money-flow-specific intelligence is incomplete, but the hard platform work exists.

**Architecture quality.** React/Vite frontend, thin FastAPI routes, use cases, framework-free domain ports, infrastructure adapters, PostgreSQL, Redis, and Celery are explicitly separated. `RuntimeServices` centralizes wiring. Daily feature runs use a pointer-based atomic publish pattern so readers never see a partially computed snapshot. Provider snapshot runs retain raw and normalized payloads and publish via an atomic pointer.

**Code quality.** Strongest of the candidates. It uses typed schemas, explicit market/provider routing, canonical symbol resolution, failure classification, circuit breakers, transactional persistence, and point-in-time/temporal-integrity checks. The codebase also contains legacy service paths alongside newer domain/use-case paths; the architecture documentation acknowledges that migration is incomplete.

**Testing.** By far the deepest suite: deterministic unit tests, integration tests, parity tests, performance tests, golden fixtures, frontend Vitest suites, and Playwright smoke coverage. CI shards unit tests and separately gates detector correctness, temporal integrity, integration behavior, performance, golden regressions, and frontend quality.

**Maintainability.** Good module boundaries but a high operational and cognitive load: 1,832 files, many Celery queues, multi-market compatibility, static/live dual paths, and both legacy and newer architecture layers. This is the principal cost of choosing it. Phase 1 must avoid touching unrelated multi-market/theme/assistant code.

**Data sources.** Yahoo/yfinance is the primary price path. Fundamentals route through Finviz, yfinance, Alpha Vantage, and market-specific adapters (including pykrx/OpenDART, AkShare, and BaoStock for non-US markets). SEC and event/calendar helpers exist. The platform uses provider and GitHub release snapshots to seed cold starts.

**Caching and persistence.** PostgreSQL is durable state; Redis is cache, broker, locking, circuit-breaker, and telemetry state. Celery performs refreshes and snapshot builds. Feature runs and provider snapshots are durable and atomically published.

**Data models and quality.** `FeatureRun` stores code version, input/universe hashes, config, stats, and warnings. Fundamentals include data-source timestamps and field-level provenance. Freshness checks compare each market with its last completed trading day and can fail closed. However, `StockPrice` itself has no provider, source timestamp, ingestion run, or raw-row lineage columns. `price_row_normalization._volume_or_zero` converts missing/non-finite volume to zero and does not reject negative volume. OHLC rows are checked for finite values but not for relational invariants such as `high >= max(open, close)`, `low <= min(open, close)`, or non-negative volume. These are concrete Phase 1 data-contract gaps.

**License.** Apache License 2.0, copyright 2026 xang1234. Modification and redistribution are permitted, including commercial use, provided the Apache license, copyright/patent notices, modification notices, and any upstream NOTICE file (if later added) are preserved. The audited repository has a license file and the frontend manifest also declares Apache-2.0.

**Recent activity.** Commit `e65d1fc...` was pushed on 2026-08-25. The repository had 913 commits in the preceding 90 days and recent CI runs were successful. This pace also raises churn/rebase risk, so upstream updates need deliberate review rather than automatic merging.

**Modules worth reusing/extending.** Existing domain/provider ports, price cache and persistence, market calendars, feature-store snapshots, publish pointers, breadth services, group rank/RRG services, scan orchestration, freshness gates, operations UI, and frontend data-fetch patterns.

**Weaknesses/risks.** Heavy deployment footprint; legacy/new path duplication; multi-market scope beyond this product; provider terms/reliability risks around unofficial Yahoo/Finviz access; incomplete row-level lineage; inadequate negative-volume/OHLC range rejection; assistant/theme features can distract from the Phase 1 vertical slice.

### 2. Mamun/stockIQ — default candidate, downgraded to donor

**Functional fit.** Good individual-stock technical analysis, S&P/Nasdaq screeners, ETF scanner, premarket movers, and an existing Streamlit UI. It does not provide persistent daily market snapshots, breadth, sector rotation, change detection, durable data health, or a canonical provider-neutral market model.

**Architecture quality.** Clean for a small app: `src/stockiq/backend` and `frontend` packages, services, data modules, YAML configuration, and a Streamlit navigation shell. It is still a single-process Streamlit application with no stable HTTP API boundary or durable database.

**Code quality.** Indicator helpers are reasonably focused. Provider calls frequently catch broad exceptions and return empty data, losing structured failure attribution. `fetch_ohlcv` drops rows missing Close but does not perform schema/range/timestamp/duplicate/volume validation.

**Testing.** Pytest covers core indicators, signals, a few data paths, and chart helpers. CI tests Python 3.11/3.12, runs Ruff, and performs import smoke checks. The ETF score itself has no dedicated unit tests.

**Maintainability.** Small and approachable, with useful YAML config and a cache abstraction. The hand-maintained ticker lists and duplicated ETF entries are maintenance risks.

**Data sources.** yfinance/Yahoo for most prices and fundamentals, plus a CBOE delayed SPY options/quote endpoint and optional LLM providers. Cached screener fundamentals are committed as JSON via a nightly GitHub Action.

**Caching.** In-process TTL caches, Streamlit caching, a local JSON OHLC cache, and committed screener JSON caches. There is no durable canonical history or atomic snapshot publish boundary.

**ETF implementation.** The universe includes duplicate tickers in multiple categories; metadata is keyed only by ticker, so later category entries overwrite earlier ones. The score begins at 50 and adds 1-month return, relative return vs SPY, RSI zone, MA20/MA50 state, and a 5-day/20-day volume ratio. Negative return contributions are not symmetrically bounded, the total is not normalized/clamped, oversold RSI is rewarded without an explicit risk model, and the formula has no historical validation or score-breakdown contract.

**License.** MIT, copyright 2025 Mamun. Modification and redistribution are permitted if the copyright and permission notice are preserved.

**Recent activity.** The audited HEAD is `092f2fea...`. The 90 commits in the prior 90 days were all nightly cache bot commits; the last non-bot commit found was 2026-05-12. HEAD freshness therefore overstates engineering activity.

**Worth reusing.** Streamlit UX ideas, configuration split, cache interface, pure indicator helpers after verification, ETF category curation after deduplication, and compact score explanation UI patterns.

**Weaknesses.** Choosing it as the base would require adding nearly every target data-platform layer. It is therefore a donor, not the base.

### 3. mphinance/scanline — DONOR

**Functional fit.** Strong live market screener with filters, computed columns, cross-sectional statistics, multi-factor scoring, multiple markets, gap tooling, and more than 1,000 TradingView fields. It lacks durable OHLC history, daily snapshots, breadth history, and provider-neutral ingestion.

**Architecture quality.** Excellent small-system boundary: Pydantic request/response models feed one pure `run_screen` pipeline used by both FastAPI and MCP. The static vanilla-JS frontend has a small store and modular feature registration. HTTP and MCP add caching without duplicating screen logic.

**Code quality.** Sandboxed AST expressions, clean error responses, stable request hashing, and pure analytics helpers are well implemented. Cross-sectional stats are computed only over returned rows after upstream limit/offset, so ranks and percentile scores are sample-relative rather than necessarily universe-relative.

**Testing.** Offline Python and Node suites are separated from tests marked `live`. CI intentionally excludes live tests. Recent scheduled live-data workflows were failing at audit time, demonstrating provider fragility even though the deterministic core is healthy.

**Maintainability.** Compact (78 files), documented, and packaged. The dependency list is duplicated manually between `pyproject.toml` and `requirements.txt`.

**Data source and cache.** `tradingview-screener` queries TradingView's scanner endpoint. A 20-second in-memory TTL cache is keyed by request hash. No canonical database exists.

**License.** MIT, copyright 2026 Michael Hanko; reusable with notice preservation. TradingView data access and terms remain a separate operational/legal concern not resolved by the code's MIT license.

**Recent activity.** SHA `aaf6ca7b...`; 60 commits in the previous 90 days. Latest scheduled live workflows were repeatedly failing.

**Worth reusing.** Request/response schema design, shared pipeline pattern, computed/stat/factor explanation semantics, graceful provider error envelopes, and offline/live test markers.

**Weaknesses.** No durable history, provider dependence, sample-relative rankings, in-memory-only cache, and no source/freshness/formula lineage.

### 4. brianbeals/sector-rotation-screener — DONOR

**Functional fit.** Direct match for the first 11-sector layer. It implements sector/subsector ETF universes, multi-window relative strength, RS inflection, seasonality, macro-cycle fit, entry timing, walk-forward backtests, and weekly reports.

**Architecture quality.** Focused Python modules for data, cycle classification, scoring, reporting, drill-down, and backtesting. It is a batch/report application rather than a service or reusable data platform.

**Code quality.** Scoring functions are pure and accept an `asof` cutoff. ALFRED vintages are used to avoid macro revision lookahead. Formula weights and thresholds are centralized in `config.py`. One inconsistency needs correction before reuse: the backtest documentation says an empty selection sits in cash, while implementation parks it in SPY but labels holdings as `CASH`. Transaction-cost timing also needs review because turnover for the next holdings is subtracted from the just-completed period.

**Testing.** Six pytest modules cover scoring boundaries, point-in-time cutoffs, cycle logic, reports, backtests, and vintage data. CI installs dev dependencies and runs pytest.

**Maintainability.** Good for its scope. Generated reports/history are committed, which inflates repository file/commit activity. Yahoo is called directly with a browser user agent as a workaround, adding provider fragility.

**Data sources and cache.** Direct Yahoo chart API for prices and FRED/ALFRED for macro data. Vintage FRED data is pickled to a disk cache and empty pulls are explicitly refused to prevent cache poisoning.

**License.** MIT, copyright 2026 Brian Beals; reusable with notice preservation.

**Recent activity.** SHA `9428d7a3...`; weekly workflow was passing at audit time.

**Worth reusing.** Transparent RS formulas, RS inflection, `asof` contracts, ALFRED point-in-time handling, thin-sample flags, factor normalization, and walk-forward test strategy.

**Weaknesses.** No API/canonical database, hard-coded universes, FRED key required for full macro path, no holdings breadth, and the backtest inconsistencies above.

### 5. czcode0501/sector-flow — FORMULA REFERENCE ONLY

**Functional fit.** Directly implements 11 SPDR sector ETFs and constituent baskets with CMF, an estimated dollar-flow measure, flow ratio, up/down volume, RVOL, close location, MFI, and OBV change.

**Architecture quality.** Thirteen files centered on `app.py`, `data.py`, and `sectors.py`. Streamlit decorators are embedded in the data layer, so calculation, provider, cache, and UI boundaries are coupled.

**Code quality and formula audit.** See the dedicated formula section below. Several formulas are useful starting points, but naming and edge handling are not production safe.

**Testing.** No tests or CI.

**Maintainability.** Small, but sector constituents are hard-coded and will drift. Exceptions are broadly swallowed and provider failures become missing rows without durable diagnostics.

**Data sources and cache.** Finnhub quotes/profiles when an API key exists; yfinance for three months of daily OHLCV. Streamlit cache TTLs are 15 seconds for quotes, one day for profiles, and one hour for metrics.

**License.** MIT, copyright 2026 czcode0501; reusable with notice preservation.

**Recent activity.** SHA `79ebdc71...`; only five commits total.

**Worth reusing.** Only test vectors/formula intent and the initial 11-sector/constituent mapping after independent validation. Do not copy labels or calculations into production unchanged.

**Weaknesses.** No validation, no persistence/history, no tests, misleading `net_flow` naming, unstable OBV percentage change, hard-coded baskets, and no provider lineage.

### 6. bankrollhunter/market-breadth — DESIGN REFERENCE ONLY

**Functional fit.** Computes MA participation, volume ratios, gap/crossover flags, sector aggregation, and breadth heatmaps for US and China universes.

**Architecture/code quality.** Script-and-database workflow built on pandas, TA-Lib, SQLAlchemy, and image rendering. It truncates and rebuilds daily tables. Imports and task package paths are brittle; it contains a hard-coded local proxy helper and broad retry loops.

**Testing.** None.

**Maintainability.** Dependencies target 2020-era pandas/numpy/yfinance and include an obsolete `git://` dependency. Reproduction on a modern runtime is unlikely without changes.

**Data sources.** yfinance plus scraped Wikipedia/Slickcharts universes and an external OpenData package.

**License.** MIT, copyright 2020 Bankroll Hunter.

**Recent activity.** SHA `70df4e75...`; last code activity 2020-11-08.

**Worth reusing.** Breadth definitions and heatmap layout ideas only.

**Weaknesses.** Obsolete dependency stack, no tests/provenance, destructive refresh pattern, brittle scraping, and no API/application boundary.

### 7. SamPom100/UnusualVolumeDetector — CONCEPT REFERENCE ONLY

**Functional fit.** Scans Nasdaq-listed equities for recent volume observations above mean plus a configurable number of standard deviations, with minimum price and minimum volume filters.

**Architecture/code quality.** A multiprocessing script plus a generated Flask/static site. It suppresses stdout globally, uses broad `except` blocks, mutates global lists, and makes per-ticker Yahoo requests with random sleeps. The anomaly observation is included in the mean/std sample that defines its own threshold, and the normal mean/std estimator is fragile for heavy-tailed volume.

**Testing.** None.

**Maintainability.** Dependency file declares two incompatible `joblib` pins and retains the discontinued Quandl WIKI path. Universe parsing is tied to Nasdaq FTP column positions.

**Data source and cache.** yfinance per symbol, Nasdaq FTP symbol files, and a dead/legacy Quandl path. No durable canonical cache.

**License.** MIT, copyright 2020 Sam Pomerantz.

**Recent activity.** SHA `b240d5bf...`; last repository change in 2025 removed a CNAME, with no meaningful recent engine development.

**Worth reusing.** Only the requirement that anomaly rankings apply price/liquidity filters and compare the latest observation with a trailing baseline.

**Weaknesses.** Non-robust statistics, no RVOL session adjustment, weak liquidity filters, provider/rate-limit fragility, dead dependency paths, and no tests.

## sector-flow formula audit

All values below are derived from daily OHLCV. They are **not measured exchange/order-flow data** and cannot support claims about real institutional net buying.

### CMF

Implemented as:

`MFM = ((Close - Low) - (High - Close)) / (High - Low)`

`CMF_N = 100 * sum(MFM * Volume, N) / sum(Volume, N)`

This is the standard Chaikin Money Flow structure, scaled to `[-100, 100]` instead of `[-1, 1]`. Zero-range bars are assigned zero contribution. It is acceptable as an **OHLCV-derived close-location/volume pressure proxy** after tests, explicit scale documentation, and input validation.

### `compute_net_dollar_flow`

Implemented as:

`sum(MFM * Volume * TypicalPrice, N)`, where `TypicalPrice = (High + Low + Close) / 3`.

This is a signed dollar-volume pressure estimate. It is not trade-classified buy-minus-sell flow, not fund flow, and not institutional flow. Production name should be `estimated_dollar_pressure` or `ohlcv_dollar_pressure_proxy`, with `metric_kind=derived_proxy`. UI must never call it actual inflow/outflow.

### Flow ratio

Implemented as the above signed proxy divided by total typical-price dollar volume. It is essentially a bounded close-location-weighted participation ratio and largely overlaps CMF semantics. It should be named `estimated_pressure_pct` and documented as a proxy.

### Up/down volume ratio

Volume is classified by whether Close rose or fell from the prior Close. Unchanged days contribute to neither side; zero down-volume produces sentinel `999`. The sentinel is unsuitable for ranking and should become a typed infinite/undefined condition or a capped display value with raw numerator/denominator retained. Period `1` is requested by `compute_all_metrics` but the function rejects periods below `2`, producing a silent missing value.

### RVOL

Latest daily volume divided by the average of the prior N completed daily volumes. The exclusion of the latest day from the baseline is correct. Intraday use would be misleading without same-time-of-day or projected-volume adjustment. It also needs minimum-history, positive-volume, split/calendar, and partial-session rules.

### Close location

Implemented as `(Close - Low) / (High - Low)`, averaged over N, giving `[0, 1]`. This is not the signed Chaikin Close Location Value `((Close-Low)-(High-Close))/(High-Low)`, which ranges `[-1, 1]`. The UI and formula registry must distinguish the two.

### OBV percentage change

OBV itself is `cumsum(sign(delta Close) * Volume)`. The repository then computes percentage change relative to the cumulative OBV value N days ago. Because OBV's absolute level depends on the arbitrary start of the available history and may cross zero, this percentage can be unstable or undefined. Prefer OBV slope, standardized OBV change, or OBV relative to rolling volume, with a fixed formula/version and tests.

### MFI

Typical-price dollar volume is assigned positive/negative according to typical-price change, then the standard 0–100 money-ratio transform is applied. Edge handling returns 100 for positive-only flow and 50 for no positive/negative flow. This is a valid OHLCV-derived Money Flow Index calculation after validation and reference-vector tests; it is still not measured money flow.

## Reuse rules

### Safe to reuse or extend from the PRIMARY BASE

- Apache-2.0-licensed domain and infrastructure boundaries.
- PostgreSQL/Redis/Celery deployment and health surfaces.
- Feature-run and provider-snapshot atomic publishing patterns.
- Market calendars, freshness gates, universe identity, and scan orchestration.
- Breadth, group ranking/RRG, watchlist, stock-detail, and operations foundations.
- Existing deterministic test/golden/parity structure.

### Donor code that may be ported only after independent tests and attribution

- `scanline` request/error envelope and robust normalization ideas.
- `sector-rotation-screener` pure RS/inflection and point-in-time test patterns.
- Small `stockIQ` indicator/configuration helpers where they improve rather than duplicate the base.

Any copied MIT code must retain its copyright and license notice in an appropriate third-party notices file and in substantial copied source where required.

### Reference only; do not copy directly

- `sector-flow` metric labels/formulas until renamed, validated, and covered by independent reference tests.
- `market-breadth` implementation because of its obsolete stack and brittle ingestion.
- `UnusualVolumeDetector` implementation because of statistical and maintenance problems.
- Any repository found in search without a clear license.

## Why stockIQ is not the base

`stockIQ` is easier to start, but base selection should minimize the amount of platform architecture that must be invented, not minimize today's install command. The required product depends on durable cross-day comparisons, snapshot lineage, breadth/group histories, provider health, and deterministic ranking. `stock-screener` already contains these concepts and tests; `stockIQ` does not. Choosing `stockIQ` would force a new database, ingestion pipeline, job runner, API boundary, snapshot system, operations model, and most history/change-detection primitives. That conflicts with the instruction not to start over.

## Base-selection risks to carry into the baseline audit

1. **Operational weight:** PostgreSQL, Redis, Celery, backend, and frontend are required for the full app.
2. **Scope control:** the base supports many non-US markets plus AI/theme features that Phase 1 must not expand.
3. **Provider risk:** Yahoo/yfinance and Finviz access are not exchange-grade feeds; provider terms, throttling, gaps, and schema drift require defensive ingestion.
4. **Lineage gap:** `StockPrice` lacks row-level source/provider timestamp/ingestion-run fields.
5. **Validation gap:** current price normalization does not reject negative volume and does not enforce OHLC relational bounds.
6. **Terminology gap:** the base has no explicit measured-flow versus derived-proxy taxonomy.
7. **Upstream velocity:** hundreds of recent commits make future upstream merges high-risk without a controlled adoption process.
8. **Baseline reproducibility:** the full startup depends on Docker availability, image pulls, ports, and host resources; these must be measured rather than assumed.
