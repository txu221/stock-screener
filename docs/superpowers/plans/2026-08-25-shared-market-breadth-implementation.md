# Shared Market Breadth Calculation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every live, historical, attribution, and static breadth formula with one canonical engine, rebuild existing breadth history as revision 2, and present StockBee and context metrics in one backward-compatible interface.

**Architecture:** A pure `app.services.breadth` package receives date-specific common-stock universes, OHLCV frames, and historical FX observations and returns typed daily results. Existing services become dependency-loading adapters; one persistence mapper writes the flat `MarketBreadth` contract, and shared React components render the live and static pages.

**Tech Stack:** Python 3, pandas, SQLAlchemy, Alembic, FastAPI/Pydantic, PostgreSQL, Redis/Celery, React, Material UI, TanStack Query, Vitest, React Testing Library.

**Spec:** `docs/superpowers/specs/2026-08-25-shared-market-breadth-design.md`

## Global Constraints

- The previous point-to-point formulas are replaced, not retained or exposed as versions.
- Only `calculation_revision = 2` rows are served after cutover.
- External breadth API fields remain flat and backward-compatible; additions are optional fields.
- StockBee signal formulas are shared across every breadth-enabled market.
- Dollar liquidity and the monthly price floor use historical USD-normalized raw prices.
- Signal returns, extrema, moving averages, ATR, and 52-week highs/lows use adjusted prices.
- Daily StockBee movers require ADTV20 at least USD 250,000, volume at least 100,000 shares, and volume greater than the prior session.
- Metric families use their own history eligibility and persist their eligible denominators.
- T2108 uses the broad common-stock universe and SMA40; the screenshot's above-50DMA metric is not implemented.
- New highs/lows use strict 52-week records; the screenshot's one-day high/low comparison is not implemented.
- The 10x ATR metric uses Wilder ATR14 and is labeled Screenshot-derived.
- A missing acceptable historical FX observation fails the whole market/date calculation.
- Live, backfill, attribution, and static paths may load data differently but may not duplicate predicates.
- Existing unrelated workspace files and application data remain untouched.

---

### Task 1: Establish typed breadth results and pure price formulas

**Files:**
- Create: `backend/app/services/breadth/__init__.py`
- Create: `backend/app/services/breadth/types.py`
- Create: `backend/app/services/breadth/formulas.py`
- Create: `backend/tests/unit/test_breadth_formulas.py`

**Interfaces:**
- Produces: `BreadthFormulaPolicy`, `BreadthUniverseMember`, `BreadthUniverseSnapshot`, `BreadthEligibilityCounts`, `BreadthIndicatorValues`, `BreadthDailyResult`.
- Produces: `prepare_feature_frame(prices: pd.DataFrame, fx_to_usd: pd.Series) -> pd.DataFrame`.
- Produces: `signal_flags_at(feature_frame: pd.DataFrame, calculation_date: date, policy: BreadthFormulaPolicy) -> SymbolBreadthSignals`.
- Depends only on pandas and Python standard-library types.

- [ ] **Step 1: Write failing boundary tests for adjusted price preparation**

Add fixtures with raw OHLC, adjusted close, volume, and FX. Assert that adjusted OHLC uses `Adj Close / Close`, while raw USD price and dollar volume remain based on raw close:

```python
def test_prepare_feature_frame_separates_adjusted_signals_from_raw_liquidity():
    index = pd.to_datetime(["2026-08-20", "2026-08-21"])
    prices = pd.DataFrame(
        {
            "Open": [99.0, 49.5],
            "High": [102.0, 51.0],
            "Low": [98.0, 49.0],
            "Close": [100.0, 50.0],
            "Adj Close": [50.0, 50.0],
            "Volume": [200_000, 220_000],
        },
        index=index,
    )
    fx = pd.Series([0.8, 0.8], index=prices.index)

    result = prepare_feature_frame(prices, fx)

    assert result.iloc[0].adjusted_high == pytest.approx(51.0)
    assert result.iloc[0].raw_close_usd == pytest.approx(80.0)
    assert result.iloc[0].dollar_volume_usd == pytest.approx(16_000_000.0)
```

- [ ] **Step 2: Run the new formula test and confirm it fails**

Run: `cd backend && ./venv/bin/pytest tests/unit/test_breadth_formulas.py -v`

Expected: FAIL because `app.services.breadth.formulas` and the typed results do not exist.

- [ ] **Step 3: Add immutable domain types and formula constants**

Define the policy without environment-dependent defaults:

```python
@dataclass(frozen=True, slots=True)
class BreadthFormulaPolicy:
    calculation_revision: int = 2
    min_adtv_usd: float = 250_000.0
    min_daily_volume: int = 100_000
    min_month_reference_price_usd: float = 5.0
    atr_period: int = 14
    atr_extension_threshold: float = 10.0
    fx_max_age_days: int = 7
```

Define all persisted values explicitly in `BreadthIndicatorValues` and `BreadthEligibilityCounts`; do not use an untyped metrics dictionary inside the engine.

- [ ] **Step 4: Implement adjusted features and Wilder ATR**

Implement:

```python
adjustment_factor = adj_close / raw_close
adjusted_open = raw_open * adjustment_factor
adjusted_high = raw_high * adjustment_factor
adjusted_low = raw_low * adjustment_factor
raw_close_usd = raw_close * fx_to_usd
dollar_volume_usd = raw_close_usd * volume
adtv20_usd = dollar_volume_usd.rolling(20, min_periods=20).mean()
```

Wilder ATR initialization is the mean of the first 14 true ranges; subsequent values use `(prior_atr * 13 + current_tr) / 14`. Preserve `NaN` until the required window exists.

- [ ] **Step 5: Add parameterized signal-boundary tests**

Cover exact and just-outside boundaries for ±4%, ±13%, ±25%, ±50%, 99,999/100,000 shares, equal/greater prior volume, ADTV USD 250,000, the USD 5 monthly reference, SMA40 equality, ATR ratio 10, strict 52-week comparisons, invalid prices, and non-finite ATR.

Use explicit assertions such as:

```python
assert signals.up_4pct is True
assert signals.down_4pct is False
assert signals.t2108_above is False  # close == SMA40 is not above
assert signals.new_high_52week is False  # repeated equal high is not new
```

- [ ] **Step 6: Run formula tests**

Run: `cd backend && ./venv/bin/pytest tests/unit/test_breadth_formulas.py -v`

Expected: PASS.

- [ ] **Step 7: Commit the pure formula layer**

```bash
git add backend/app/services/breadth backend/tests/unit/test_breadth_formulas.py
git commit -m "feat: add canonical breadth formula primitives"
```

---

### Task 2: Implement date-specific universe and historical FX eligibility

**Files:**
- Create: `backend/app/services/breadth/universe.py`
- Modify: `backend/app/services/point_in_time_universe_service.py`
- Modify: `backend/app/services/fx_service.py`
- Create: `backend/tests/unit/test_breadth_universe.py`
- Modify: `backend/tests/unit/test_fx_service.py`

**Interfaces:**
- Consumes: `BreadthFormulaPolicy`, `BreadthUniverseMember`, `BreadthUniverseSnapshot` from Task 1.
- Produces: `resolve_historical_fx_series(currency, calculation_dates, observations, max_age_days) -> pd.Series`.
- Produces: `MissingHistoricalFXError(currency: str, calculation_date: date)`.
- Produces: `build_breadth_universe_snapshots(db, market, dates) -> Mapping[date, BreadthUniverseSnapshot]`.
- Produces: `classify_metric_eligibility(member, features, policy) -> SymbolMetricEligibility`.

- [ ] **Step 1: Write failing historical-FX as-of tests**

Assert exact date wins, a prior observation at seven calendar days is accepted, eight days is rejected, future observations never backfill earlier dates, and USD produces identity rates:

```python
def test_historical_fx_never_uses_future_quote():
    observations = {date(2026, 8, 22): 0.128}
    with pytest.raises(MissingHistoricalFXError):
        resolve_historical_fx_series(
            "HKD", (date(2026, 8, 21),), observations, max_age_days=7
        )
```

- [ ] **Step 2: Write failing broad/StockBee universe tests**

Use a point-in-time snapshot containing one common stock, one inactive stock, and one excluded instrument. Assert the broad signature covers only active common stocks and the StockBee signature covers only broad members whose ADTV20 meets the threshold.

- [ ] **Step 3: Run focused tests and confirm failure**

Run: `cd backend && ./venv/bin/pytest tests/unit/test_breadth_universe.py tests/unit/test_fx_service.py -v`

Expected: FAIL because historical as-of FX and breadth snapshot adapters do not exist.

- [ ] **Step 4: Add a historical-only FX lookup that bypasses live Redis/fetch fallback**

Keep `FXService.get_usd_rate()` unchanged for current fundamentals. Add an explicit database method:

```python
def get_historical_usd_rates(
    self,
    currencies: Collection[str],
    dates: Collection[date],
    *,
    max_age_days: int = 7,
) -> Mapping[str, pd.Series]:
    requested = tuple(sorted(set(dates)))
    resolved: dict[str, pd.Series] = {}
    with self._session_factory() as db:
        for currency in sorted({value.upper() for value in currencies}):
            if currency == "USD":
                resolved[currency] = pd.Series(1.0, index=pd.to_datetime(requested))
                continue
            rows = (
                db.query(FXRate)
                .filter(
                    FXRate.from_currency == currency,
                    FXRate.to_currency == "USD",
                    FXRate.as_of_date <= requested[-1],
                )
                .order_by(FXRate.as_of_date.asc(), FXRate.created_at.asc())
                .all()
            )
            observations = {row.as_of_date: float(row.rate) for row in rows}
            resolved[currency] = resolve_historical_fx_series(
                currency, requested, observations, max_age_days=max_age_days
            )
    return resolved
```

Query `FXRate` rows at or before the maximum requested date, resolve each date with a backward-only as-of join, and raise `MissingHistoricalFXError(currency, date)` rather than fetching a current quote.

- [ ] **Step 5: Adapt point-in-time membership into explicit breadth members**

Return members with `symbol`, `currency`, and `is_common_stock`. Use the existing official/date-specific universe policy as the authoritative source; excluded or unclassified instruments do not enter `BreadthUniverseSnapshot.members`. Derive the existing broad signature from the canonical sorted symbol tuple.

- [ ] **Step 6: Implement metric eligibility**

Eligibility must be independent per family:

```python
daily = has_2_closes and has_2_volumes and has_adtv20 and adtv20 >= 250_000
month = has_close_20 and daily_liquidity and raw_close_20_usd >= 5
day_34 = has_34_adjusted_closes and daily_liquidity
quarter = has_65_adjusted_closes and daily_liquidity
t2108 = has_40_adjusted_closes
atr_extension = has_50_adjusted_ohlc and finite_positive_atr14
high_low_52week = has_252_adjusted_ohlc
```

- [ ] **Step 7: Run universe and FX tests**

Run: `cd backend && ./venv/bin/pytest tests/unit/test_breadth_universe.py tests/unit/test_fx_service.py -v`

Expected: PASS.

- [ ] **Step 8: Commit universe and FX policy**

```bash
git add backend/app/services/breadth/universe.py backend/app/services/point_in_time_universe_service.py backend/app/services/fx_service.py backend/tests/unit/test_breadth_universe.py backend/tests/unit/test_fx_service.py
git commit -m "feat: add breadth universe and historical FX policy"
```

---

### Task 3: Build the canonical range engine and inclusive ratios

**Files:**
- Create: `backend/app/services/breadth/ratios.py`
- Create: `backend/app/services/breadth/engine.py`
- Create: `backend/tests/unit/test_breadth_engine.py`
- Create: `backend/tests/unit/test_breadth_ratios.py`

**Interfaces:**
- Consumes: types, features, signals, universe snapshots, and FX series from Tasks 1-2.
- Produces: `BreadthEngine.calculate(request: BreadthEngineRequest) -> Mapping[date, BreadthDailyResult]`.
- Produces: `calculate_inclusive_ratios(counts, seed_counts=()) -> Mapping[date, BreadthRatios]`.
- `BreadthEngineRequest` contains `market`, ordered `dates`, `universes_by_date`, `prices_by_symbol`, and `fx_by_currency`.

- [ ] **Step 1: Write a failing multi-symbol engine fixture**

Build symbols with different history lengths and assert that a recent IPO contributes to advance/decline but not quarter breadth. Assert each eligible denominator independently and assert `total_stocks_scanned == broad_universe_count` through `BreadthDailyResult.to_record_mapping()`.

- [ ] **Step 2: Write failing inclusive ratio tests**

Use the screenshot arithmetic and assert today is included:

```python
def test_five_and_ten_day_ratios_include_current_row():
    pairs = [
        (204, 225), (158, 124), (192, 102), (246, 116), (148, 87),
        (114, 159), (74, 343), (395, 177), (78, 274), (228, 25),
    ]
    dates = pd.bdate_range("2026-08-10", periods=10).date
    counts = [
        BreadthDailyCount(date=day, stocks_up_4pct=up, stocks_down_4pct=down)
        for day, (up, down) in zip(dates, pairs, strict=True)
    ]
    result = calculate_inclusive_ratios(counts)
    assert result[dates[-1]].ratio_5day == pytest.approx(0.91)
    assert result[dates[-1]].ratio_10day == pytest.approx(1.13)
```

Also assert fewer than five rows returns both ratios null, five through nine rows return only the five-day ratio, and a zero down-count sum returns null.

- [ ] **Step 3: Run engine tests and confirm failure**

Run: `cd backend && ./venv/bin/pytest tests/unit/test_breadth_engine.py tests/unit/test_breadth_ratios.py -v`

Expected: FAIL because the engine and ratio module do not exist.

- [ ] **Step 4: Implement one feature pass per symbol**

For each symbol, prepare its feature frame once, align exact target dates, and aggregate `SymbolBreadthSignals`. Do not replace missing dates with the prior bar. Record per-family eligibility even when the symbol produces no signal.

- [ ] **Step 5: Aggregate every canonical value**

Populate all existing signal counts plus advancing/declining/unchanged, 52-week high/low, T2108 count/percentage, 10x ATR count, broad universe, per-family denominators, signatures, and revision 2.

Before constructing a result, enforce:

```python
assert advancing + declining + unchanged == advance_decline_eligible_count
assert 0 <= t2108_count <= t2108_eligible_count
assert calculation_revision == 2
```

- [ ] **Step 6: Implement rolling ratios over canonical rows**

Append the current daily count before slicing the last five or ten rows. Seed rows must already be revision 2 and market-matched; otherwise raise `IncompatibleBreadthSeedError`.

- [ ] **Step 7: Run engine and ratio tests**

Run: `cd backend && ./venv/bin/pytest tests/unit/test_breadth_engine.py tests/unit/test_breadth_ratios.py -v`

Expected: PASS.

- [ ] **Step 8: Commit the canonical engine**

```bash
git add backend/app/services/breadth/engine.py backend/app/services/breadth/ratios.py backend/tests/unit/test_breadth_engine.py backend/tests/unit/test_breadth_ratios.py
git commit -m "feat: add canonical breadth range engine"
```

---

### Task 4: Add revision-2 schema and backward-compatible API fields

**Files:**
- Create: `backend/alembic/versions/20260825_0031_add_shared_breadth_metrics.py`
- Modify: `backend/app/models/market_breadth.py`
- Modify: `backend/app/schemas/breadth.py`
- Modify: `backend/app/api/v1/breadth.py`
- Create: `backend/tests/integration/test_shared_breadth_metrics_migration.py`
- Modify: `backend/tests/unit/test_breadth_eligibility_schema.py`
- Modify: `backend/tests/unit/test_breadth_endpoints.py`

**Interfaces:**
- Consumes: exact persisted fields from `BreadthDailyResult.to_record_mapping()`.
- Produces: nullable additive database columns and optional Pydantic response fields.
- Produces: `CURRENT_BREADTH_CALCULATION_REVISION = 2` in the breadth package.

- [ ] **Step 1: Write failing migration-schema assertions**

Assert upgrade creates every field from the spec with correct integer/float/string types, preserves `uix_breadth_date_market`, and downgrade removes only the new fields.

- [ ] **Step 2: Write failing API compatibility assertions**

Construct a legacy-shaped row plus a complete revision-2 row. Assert old fields retain their names and types and new fields serialize flat rather than under nested `stockbee` or `context` objects.

- [ ] **Step 3: Run focused migration and endpoint tests**

Run: `cd backend && ./venv/bin/pytest tests/integration/test_shared_breadth_metrics_migration.py tests/unit/test_breadth_eligibility_schema.py tests/unit/test_breadth_endpoints.py -v`

Expected: FAIL because revision-2 columns are absent.

- [ ] **Step 4: Add the Alembic migration after head `20260823_0030`**

Add nullable columns exactly as specified, including
`advance_decline_eligible_count`. Use `Integer` for counts/revision, `Float` for
`t2108_pct`, and `String(64)` for `stockbee_eligibility_signature`. Do not update
or delete existing data in the schema migration.

- [ ] **Step 5: Update SQLAlchemy and Pydantic models**

Keep existing columns unchanged. Add optional response fields with clear descriptions, including the origin where useful. Mark `total_stocks_scanned` as the compatibility alias for `broad_universe_count` in comments and API documentation.

- [ ] **Step 6: Extend the trend endpoint allowlist**

Add new scalar indicators such as `t2108_pct`, `advancing_count`, `declining_count`, `new_high_52week_count`, `new_low_52week_count`, and `atr_10x_extension_count`. Denominator fields may also be requested for coverage charts.

- [ ] **Step 7: Run migration and endpoint tests**

Run: `cd backend && ./venv/bin/pytest tests/integration/test_shared_breadth_metrics_migration.py tests/unit/test_breadth_eligibility_schema.py tests/unit/test_breadth_endpoints.py -v`

Expected: PASS.

- [ ] **Step 8: Commit schema and API additions**

```bash
git add backend/alembic/versions/20260825_0031_add_shared_breadth_metrics.py backend/app/models/market_breadth.py backend/app/schemas/breadth.py backend/app/api/v1/breadth.py backend/tests/integration/test_shared_breadth_metrics_migration.py backend/tests/unit/test_breadth_eligibility_schema.py backend/tests/unit/test_breadth_endpoints.py
git commit -m "feat: add revision 2 breadth schema"
```

---

### Task 5: Route live and backfill persistence through the shared engine

**Files:**
- Create: `backend/app/services/breadth/persistence.py`
- Modify: `backend/app/services/breadth_calculator_service.py`
- Modify: `backend/app/services/breadth_backfill.py`
- Modify: `backend/app/services/breadth_coverage.py`
- Modify: `backend/app/services/daily_breadth_runner.py`
- Modify: `backend/app/tasks/breadth_tasks.py`
- Modify: `backend/tests/unit/test_breadth_calculator_service.py`
- Modify: `backend/tests/unit/test_breadth_backfill.py`
- Modify: `backend/tests/unit/test_daily_breadth_runner.py`
- Modify: `backend/tests/unit/test_breadth_tasks.py`

**Interfaces:**
- Consumes: `BreadthEngine.calculate()` and `BreadthDailyResult.to_record_mapping()`.
- Produces: `BreadthPersistence.upsert_daily(result, duration_seconds)` and `upsert_many(results)`.
- Produces: revision-aware seed loading for ratios.

- [ ] **Step 1: Replace legacy-behavior tests with failing canonical expectations**

Change tests that currently expect 21/34/63-day point-to-point returns or ratios excluding today. Add assertions that live and backfill return the same corrected counts for the same fixture.

- [ ] **Step 2: Run focused service tests and confirm failure**

Run: `cd backend && ./venv/bin/pytest tests/unit/test_breadth_calculator_service.py tests/unit/test_breadth_backfill.py tests/unit/test_daily_breadth_runner.py tests/unit/test_breadth_tasks.py -v`

Expected: FAIL against the existing private formula methods.

- [ ] **Step 3: Implement one persistence mapping**

`BreadthPersistence` must assign every existing and new column from the typed result. It sets:

```python
record.total_stocks_scanned = result.eligibility.broad_universe_count
record.calculation_revision = CURRENT_BREADTH_CALCULATION_REVISION
```

Commit once per daily operation and once per batch operation; never commit partially within a market/date.

- [ ] **Step 4: Turn `BreadthCalculatorService` into an adapter**

Retain its public constructor and methods used by tasks. Remove `_get_price_change`, `_apply_stock_metrics`, `_calculate_ratios`, and duplicated metrics dictionaries after callers move. Load point-in-time universe members, required cached price histories, and historical FX, build a `BreadthEngineRequest`, invoke the engine, and return existing coverage/result wrappers.

- [ ] **Step 5: Simplify `BreadthBackfillExecutor`**

Keep its planning, cache-only/provider policy, unsupported-symbol handling, progress diagnostics, and legacy result dictionary. Replace its nested formula loop and `deque` ratio calculation with one range-engine call. Supply up to nine prior revision-2 count rows only for sparse requested ranges.

- [ ] **Step 6: Extend coverage without redefining scanned counts**

Preserve cache miss/error diagnostics. Add metric denominator mappings to the daily/backfill payload. Treat `total_stocks_scanned` as broad universe compatibility output rather than “symbols with 70 rows.”

- [ ] **Step 7: Run service tests**

Run: `cd backend && ./venv/bin/pytest tests/unit/test_breadth_calculator_service.py tests/unit/test_breadth_backfill.py tests/unit/test_daily_breadth_runner.py tests/unit/test_breadth_tasks.py -v`

Expected: PASS.

- [ ] **Step 8: Commit live/backfill integration**

```bash
git add backend/app/services/breadth/persistence.py backend/app/services/breadth_calculator_service.py backend/app/services/breadth_backfill.py backend/app/services/breadth_coverage.py backend/app/services/daily_breadth_runner.py backend/app/tasks/breadth_tasks.py backend/tests/unit/test_breadth_calculator_service.py backend/tests/unit/test_breadth_backfill.py backend/tests/unit/test_daily_breadth_runner.py backend/tests/unit/test_breadth_tasks.py
git commit -m "refactor: route breadth workflows through shared engine"
```

---

### Task 6: Remove static and attribution formula drift

**Files:**
- Modify: `backend/app/services/static_breadth_section_builder.py`
- Modify: `backend/app/services/static_site_export_service.py`
- Modify: `backend/app/services/breadth_attribution_service.py`
- Modify: `backend/app/services/ui_snapshot_service.py`
- Create: `backend/tests/unit/test_breadth_workflow_parity.py`
- Modify: `backend/tests/unit/test_breadth_attribution_service.py`
- Modify: `backend/tests/unit/test_static_export_market_exposure.py`
- Modify: `backend/tests/unit/test_ui_snapshot_service.py`

**Interfaces:**
- Consumes: `prepare_feature_frame()`, `signal_flags_at()`, and `BreadthEngine.calculate()`.
- Produces: identical applicable counts from live, backfill, static, and attribution fixtures.
- Static export dependencies gain an injected engine-input factory rather than direct formula knowledge.

- [ ] **Step 1: Add a failing four-path parity fixture**

Use one deterministic universe/price/FX fixture and run it through live calculation, historical backfill, static fallback calculation, and group attribution. Assert daily StockBee market counts are equal and attribution group totals reconcile to market totals including `No Group`.

- [ ] **Step 2: Run parity and static tests to confirm failure**

Run: `cd backend && ./venv/bin/pytest tests/unit/test_breadth_workflow_parity.py tests/unit/test_breadth_attribution_service.py tests/unit/test_static_export_market_exposure.py tests/unit/test_ui_snapshot_service.py -v`

Expected: FAIL because static and attribution still use close-only 4% predicates and old lookbacks.

- [ ] **Step 3: Replace static `_compute_breadth_metrics_by_date` internals**

Keep the method as a compatibility adapter if tests/callers require it, but construct a `BreadthEngineRequest` and serialize typed results. Inject universe/currency/FX input construction from `StaticSiteExportService`; do not query providers during cache-only export.

- [ ] **Step 4: Reuse canonical daily flags in attribution**

Replace `MOVER_THRESHOLD_PCT` and close-only percentage logic. Attribution accepts prepared symbol features or the inputs needed to call `signal_flags_at()`. Include adjusted return in stock details, but only classify movers when ADTV, 100,000-share, and expanding-volume conditions all pass.

- [ ] **Step 5: Serialize all new fields into UI snapshots**

Extend the existing breadth row serializer rather than hand-building a second response shape. Include revision 2 in the snapshot source revision so old snapshots become stale automatically.

- [ ] **Step 6: Run parity and static tests**

Run: `cd backend && ./venv/bin/pytest tests/unit/test_breadth_workflow_parity.py tests/unit/test_breadth_attribution_service.py tests/unit/test_static_export_market_exposure.py tests/unit/test_ui_snapshot_service.py -v`

Expected: PASS.

- [ ] **Step 7: Commit parity integration**

```bash
git add backend/app/services/static_breadth_section_builder.py backend/app/services/static_site_export_service.py backend/app/services/breadth_attribution_service.py backend/app/services/ui_snapshot_service.py backend/tests/unit/test_breadth_workflow_parity.py backend/tests/unit/test_breadth_attribution_service.py backend/tests/unit/test_static_export_market_exposure.py backend/tests/unit/test_ui_snapshot_service.py
git commit -m "refactor: unify static and attributed breadth formulas"
```

---

### Task 7: Guard and validate downstream breadth consumers

**Files:**
- Modify: `backend/app/api/v1/breadth.py`
- Modify: `backend/app/api/v1/stocks.py`
- Modify: `backend/app/services/digest_service.py`
- Modify: `backend/app/services/market_exposure_service.py`
- Modify: `backend/app/services/watchlist_stewardship_service.py`
- Modify: `backend/app/interfaces/mcp/market_copilot.py`
- Modify: `backend/tests/unit/test_breadth_endpoints.py`
- Modify: `backend/tests/unit/test_digest_service.py`
- Modify: `backend/tests/unit/test_market_exposure_service.py`
- Modify: `backend/tests/unit/test_watchlist_stewardship_service.py`
- Modify: `backend/tests/unit/test_mcp_market_copilot.py`

**Interfaces:**
- Consumes: flat revision-2 `MarketBreadth` rows.
- Produces: `current_breadth_query(market)` and historical query helpers that filter `calculation_revision == 2`.
- Preserves existing stance interfaces unless distribution evidence requires a threshold change.

- [ ] **Step 1: Write failing stale-row guard tests**

Insert a newer revision-null row and an older revision-2 row. Assert current breadth and each downstream consumer select the revision-2 row. Assert a market with only stale rows returns the existing “no current breadth” behavior rather than serving mixed data.

- [ ] **Step 2: Add corrected-history consumer fixtures**

Feed representative bullish, balanced, and bearish revision-2 rows into digest, exposure, stock regime, watchlist stewardship, and copilot. Assert their public labels and facts remain coherent and universe copy uses broad or explicit eligible counts.

- [ ] **Step 3: Run downstream tests and confirm failure**

Run: `cd backend && ./venv/bin/pytest tests/unit/test_breadth_endpoints.py tests/unit/test_digest_service.py tests/unit/test_market_exposure_service.py tests/unit/test_watchlist_stewardship_service.py tests/unit/test_mcp_market_copilot.py -v`

Expected: FAIL because queries do not enforce revision 2 and copy still assumes one scanned denominator.

- [ ] **Step 4: Centralize revision-aware breadth selection**

Use one query helper from breadth persistence/query code. Replace direct latest-row queries in stocks, digest, exposure, watchlist, and copilot. Do not duplicate the revision constant.

- [ ] **Step 5: Evaluate existing thresholds against rebuilt fixture distributions**

Generate a deterministic report containing frequency of each existing consumer state before any threshold change. Keep sign comparisons and ratio `1.0` balance thresholds unless corrected history demonstrates unreasonable behavior. If a threshold changes, encode the new value as a named constant and add the exact fixture that justifies it.

- [ ] **Step 6: Expose useful new context without changing scoring**

Copilot and digest may report T2108, 52-week high/low, or advancing/declining when present, but these fields do not add scoring points in this change. Preserve graceful behavior for optional fields.

- [ ] **Step 7: Run downstream tests**

Run: `cd backend && ./venv/bin/pytest tests/unit/test_breadth_endpoints.py tests/unit/test_digest_service.py tests/unit/test_market_exposure_service.py tests/unit/test_watchlist_stewardship_service.py tests/unit/test_mcp_market_copilot.py -v`

Expected: PASS.

- [ ] **Step 8: Commit downstream guards**

```bash
git add backend/app/api/v1/breadth.py backend/app/api/v1/stocks.py backend/app/services/digest_service.py backend/app/services/market_exposure_service.py backend/app/services/watchlist_stewardship_service.py backend/app/interfaces/mcp/market_copilot.py backend/tests/unit/test_breadth_endpoints.py backend/tests/unit/test_digest_service.py backend/tests/unit/test_market_exposure_service.py backend/tests/unit/test_watchlist_stewardship_service.py backend/tests/unit/test_mcp_market_copilot.py
git commit -m "fix: guard downstream consumers to corrected breadth"
```

---

### Task 8: Build shadow rebuild, validation, and atomic cutover tooling

**Files:**
- Create: `backend/app/scripts/rebuild_market_breadth.py`
- Create: `backend/app/services/breadth/rebuild.py`
- Create: `backend/tests/unit/test_breadth_rebuild.py`
- Create: `backend/tests/integration/test_breadth_revision_cutover.py`
- Create: `docs/runbooks/market-breadth-revision-2-cutover.md`

**Interfaces:**
- Consumes: canonical range engine, persistence mappings, supported breadth-market catalog, point-in-time universe, OHLCV cache/database, and historical FX.
- Produces CLI phases: `build`, `validate`, `activate`, `cleanup`.
- Produces exit constants `EXIT_CONFIRMATION_REQUIRED = 2` and
  `EXIT_VALIDATION_REQUIRED = 3`.
- `activate` requires `--confirm-replace` and refuses an invalid staging dataset.

- [ ] **Step 1: Write failing CLI phase tests**

Assert parser behavior and safety gates:

```python
assert main(["activate"]) == EXIT_CONFIRMATION_REQUIRED
assert main(["activate", "--confirm-replace"]) == EXIT_VALIDATION_REQUIRED
assert main(["build", "--market", "US", "--start-date", "2026-01-01"]) == 0
```

Mock external data loading but use the real engine.

- [ ] **Step 2: Write a failing PostgreSQL cutover integration test**

Create legacy rows, build validated revision-2 staging rows, activate, and assert in one transaction that no legacy rows remain, the `(date, market)` key remains unique, and unrelated tables are unchanged.

- [ ] **Step 3: Run rebuild tests and confirm failure**

Run: `cd backend && ./venv/bin/pytest tests/unit/test_breadth_rebuild.py tests/integration/test_breadth_revision_cutover.py -v`

Expected: FAIL because rebuild tooling does not exist.

- [ ] **Step 4: Implement `build` with deterministic staging output**

Create `market_breadth_rebuild` explicitly from the target column contract. Rebuild supported markets in chronological batches with 252-session warm-up, write only revision-2 rows, checkpoint per market/date, and make reruns idempotent by `(market, date)`.

- [ ] **Step 5: Implement validation**

Validation fails on duplicate keys, missing supported-market dates after warm-up, non-revision-2 rows, invalid count/denominator relations, missing signatures, non-finite percentages, mixed ratio seeds, missing FX provenance, or consumer state-generation errors. Emit a JSON report with row counts, date ranges, coverage distributions, formula spot checks, and consumer-state frequencies.

- [ ] **Step 6: Implement transactional activation**

Within one PostgreSQL transaction:

```sql
LOCK TABLE market_breadth IN ACCESS EXCLUSIVE MODE;
DELETE FROM market_breadth;
INSERT INTO market_breadth (<explicit target columns>)
SELECT <explicit target columns>
FROM market_breadth_rebuild;
```

Verify inserted row count and revision before commit. Do not drop staging during activation.

- [ ] **Step 7: Implement cleanup and write the operator runbook**

The runbook gives exact commands for schema upgrade, backup, build, validation, writer pause, activation, exposure rebuild, cache/snapshot/static regeneration, service deployment, monitoring, rollback, and delayed staging cleanup. Name every Celery breadth/exposure writer that must be stopped.

- [ ] **Step 8: Run rebuild and cutover tests**

Run: `cd backend && ./venv/bin/pytest tests/unit/test_breadth_rebuild.py tests/integration/test_breadth_revision_cutover.py -v`

Expected: PASS.

- [ ] **Step 9: Commit rebuild tooling and runbook**

```bash
git add backend/app/scripts/rebuild_market_breadth.py backend/app/services/breadth/rebuild.py backend/tests/unit/test_breadth_rebuild.py backend/tests/integration/test_breadth_revision_cutover.py docs/runbooks/market-breadth-revision-2-cutover.md
git commit -m "feat: add breadth revision 2 cutover tooling"
```

---

### Task 9: Build the shared integrated live/static breadth UI

**Files:**
- Create: `frontend/src/components/Breadth/breadthMetricDefinitions.js`
- Create: `frontend/src/components/Breadth/BreadthMetricTooltip.jsx`
- Create: `frontend/src/components/Breadth/BreadthContextStrip.jsx`
- Create: `frontend/src/components/Breadth/BreadthHistoryTable.jsx`
- Create: `frontend/src/components/Breadth/BreadthContextStrip.test.jsx`
- Create: `frontend/src/components/Breadth/BreadthHistoryTable.test.jsx`
- Modify: `frontend/src/pages/BreadthPage.jsx`
- Modify: `frontend/src/pages/BreadthPage.test.jsx`
- Modify: `frontend/src/static/pages/StaticBreadthPage.jsx`
- Modify: `frontend/src/static/pages/StaticBreadthPage.test.jsx`
- Modify: `frontend/src/components/Charts/BreadthChart.jsx`

**Interfaces:**
- Consumes: flat `BreadthResponse` rows with optional revision-2 fields.
- Produces: shared context strip, metric/source tooltip, and grouped historical table.
- Preserves: existing market selection, benchmark mapping, live/bootstrap queries, time ranges, and static Overview/By Group navigation.

- [ ] **Step 1: Add failing metric-definition and tooltip tests**

Define expected metadata for every table/context field. Assert the Primary/Secondary group is marked Screenshot grouping while each underlying formula is marked StockBee. Assert 10x ATR is Screenshot-derived and T2108 is StockBee.

- [ ] **Step 2: Add failing context-strip tests**

Render a complete row and assert advancing/declining counts and percentages, 52-week counts, T2108, ATR extension, broad universe, eligible denominators, and em dashes for zero eligibility.

- [ ] **Step 3: Add failing grouped-table tests**

Assert grouped sticky headers, all primary/secondary columns, context columns `10x ATR`, `T2108`, and `Broad Universe`, ratio formatting, pair tint semantics, accessible tooltip triggers, and horizontal overflow behavior.

- [ ] **Step 4: Run new component tests and confirm failure**

Run: `cd frontend && npx vitest run src/components/Breadth/BreadthContextStrip.test.jsx src/components/Breadth/BreadthHistoryTable.test.jsx`

Expected: FAIL because the shared components do not exist.

- [ ] **Step 5: Implement centralized metric metadata**

Each definition contains `label`, `formulaOrigin`, `groupOrigin`, `description`, `requiredHistory`, `eligibleField`, and optional `liquidityRule`. Components must read this metadata rather than duplicating tooltip copy.

- [ ] **Step 6: Implement context strip and grouped table**

Use Material UI semantic table elements. Keep date sticky on the left, group headers sticky at the top, and wrap the table in `TableContainer` for narrow screens. Compute percentages only when the matching eligible denominator is positive.

- [ ] **Step 7: Replace the live page's tabbed latest-data panel**

Keep all query/bootstrap logic. Render header/context strip, the existing chart, and the shared history table in the approved integrated arrangement. Stack chart and table below the desktop breakpoint. Remove obsolete Quarterly/Monthly/Explosive/34-Day tab state and labels such as `63d` and `21d`.

- [ ] **Step 8: Reuse the same components on the static Overview**

Preserve static `Overview` and `By Group` tabs and fallback behavior. Pass the static payload through the same context strip and history table used by the live page.

- [ ] **Step 9: Run frontend breadth tests**

Run: `cd frontend && npx vitest run src/components/Breadth/BreadthContextStrip.test.jsx src/components/Breadth/BreadthHistoryTable.test.jsx src/pages/BreadthPage.test.jsx src/static/pages/StaticBreadthPage.test.jsx`

Expected: PASS.

- [ ] **Step 10: Run frontend lint and build**

Run: `cd frontend && npm run lint && npm run build`

Expected: both commands exit 0.

- [ ] **Step 11: Commit the integrated UI**

```bash
git add frontend/src/components/Breadth frontend/src/pages/BreadthPage.jsx frontend/src/pages/BreadthPage.test.jsx frontend/src/static/pages/StaticBreadthPage.jsx frontend/src/static/pages/StaticBreadthPage.test.jsx frontend/src/components/Charts/BreadthChart.jsx
git commit -m "feat: add integrated breadth monitor UI"
```

---

### Task 10: Remove legacy formula code and run the complete verification gate

**Files:**
- Modify: `backend/app/services/breadth_calculator_service.py`
- Modify: `backend/app/services/static_breadth_section_builder.py`
- Modify: `backend/app/services/breadth_attribution_service.py`
- Modify: `backend/tests/unit/test_pct_change_policy.py`
- Modify: `docs/superpowers/specs/2026-08-25-shared-market-breadth-design.md` only if implementation discovered a factual contract correction

**Interfaces:**
- Verifies the complete spec and removes superseded private helpers after all callers have migrated.
- Does not change formula semantics established by Tasks 1-9.

- [ ] **Step 1: Add a source-boundary guard test**

Assert legacy formula tokens are absent from production adapters and only the shared package owns thresholds/lookbacks:

```python
for path in LEGACY_ADAPTER_PATHS:
    source = path.read_text()
    assert "pct_change(periods=21" not in source
    assert "pct_change(periods=63" not in source
    assert "MOVER_THRESHOLD_PCT" not in source
```

Prefer behavioral parity tests for formulas; this source guard exists only to prevent known duplicate implementations.

- [ ] **Step 2: Delete superseded calculation helpers and comments**

Remove references to 21-day months, 63-day quarters, 34-day point-to-point returns, 70-row all-or-nothing eligibility, and ratios that exclude today. Keep public facades required by callers.

- [ ] **Step 3: Run the focused backend breadth suite**

Run:

```bash
cd backend
./venv/bin/pytest \
  tests/unit/test_breadth_formulas.py \
  tests/unit/test_breadth_universe.py \
  tests/unit/test_breadth_engine.py \
  tests/unit/test_breadth_ratios.py \
  tests/unit/test_breadth_calculator_service.py \
  tests/unit/test_breadth_backfill.py \
  tests/unit/test_breadth_workflow_parity.py \
  tests/unit/test_breadth_attribution_service.py \
  tests/unit/test_breadth_endpoints.py \
  tests/unit/test_breadth_rebuild.py -v
```

Expected: PASS.

- [ ] **Step 4: Run migration and cutover integration tests**

Run:

```bash
cd backend
./venv/bin/pytest \
  tests/integration/test_shared_breadth_metrics_migration.py \
  tests/integration/test_breadth_revision_cutover.py -v
```

Expected: PASS against PostgreSQL.

- [ ] **Step 5: Run all backend unit tests**

Run: `cd backend && ./venv/bin/pytest tests/unit/`

Expected: PASS.

- [ ] **Step 6: Run the complete frontend test suite, lint, and build**

Run:

```bash
cd frontend
npm run test:run
npm run lint
npm run build
```

Expected: all commands exit 0.

- [ ] **Step 7: Rehearse revision-2 migration on an existing-database copy**

Follow `docs/runbooks/market-breadth-revision-2-cutover.md` through schema upgrade, shadow build, validation, activation, exposure rebuild, cache/snapshot/static regeneration, and rollback. Save the validation JSON outside the repository or under the established operational artifact location; do not commit production data.

- [ ] **Step 8: Review the final diff against completion criteria**

Confirm only revision-2 queries remain, every new field is serialized live and static, group attribution reconciles, no formula copy remains, and no unrelated files are staged.

- [ ] **Step 9: Commit cleanup and verification guards**

```bash
git add backend/app/services/breadth_calculator_service.py backend/app/services/static_breadth_section_builder.py backend/app/services/breadth_attribution_service.py backend/tests/unit/test_pct_change_policy.py
git commit -m "test: enforce canonical breadth calculation boundary"
```
