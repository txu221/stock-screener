# Shared Market Breadth Calculation Design

**Date:** 2026-08-25

## Objective

Replace the current point-to-point breadth implementation with one canonical,
shared calculation layer. The corrected layer implements StockBee breadth
formulas, adds selected market-context indicators, rebuilds all derived breadth
history as a step change, and presents the results in one integrated live/static
interface.

The previous formulas are treated as incorrect implementation details rather
than supported methodologies. They are not retained as selectable versions.

## Source Classification

Every displayed metric has an explicit origin:

- **StockBee** identifies formulas published or directly clarified by StockBee.
- **Screenshot-derived** identifies a metric inferred from the referenced Market
  Pulse interface whose backend formula is not public.
- **Context** identifies broad-market participation data that does not drive the
  StockBee signal counts or ratios.

The screenshot supplies the visual **Primary** and **Secondary** column
grouping. That grouping is not attributed to StockBee: StockBee's published
notes describe the 25%-quarter indicator itself as primary. In the application,
group headers identify the screenshot-derived layout while each column tooltip
identifies its StockBee formula origin.

Primary sources:

- <https://stockbee.blogspot.com/2011/08/how-to-use-market-breadth-to-avoid.html>
- <https://stockbee.blogspot.com/p/mm.html>

## Product Decisions

- Use the same StockBee signal methodology in every breadth-enabled market.
- Normalize price and dollar-liquidity eligibility to USD with historical FX.
- Apply the StockBee daily volume confirmation: at least 100,000 shares and
  current volume greater than the previous session's volume.
- Use metric-specific history eligibility rather than one 65-session universe.
- Maintain two universe families inside the shared engine:
  - broad active common stocks for context metrics;
  - USD-liquidity-filtered common stocks for StockBee indicators.
- Use 20-session average dollar volume of at least USD 250,000.
- Require a raw price 20 sessions ago of at least USD 5 for the monthly StockBee
  indicators.
- Use T2108, percentage above the 40-day moving average, instead of the
  screenshot's percentage above the 50-day moving average.
- Use classic 52-week new highs and lows instead of one-day high/low breaks.
- Use Wilder ATR14 for the screenshot-derived 10x ATR extension metric.
- Preserve existing external API field names and add new fields without nesting
  or breaking current clients.
- Rebuild history as a step change during a short breadth maintenance window.
- Persist `calculation_revision = 2` as a stale-row guard, not as a formula
  selector. Only revision 2 is served after cutover.
- Use the integrated UI layout with visible grouping and formula-origin labels.
- Include downstream breadth-consumer validation and evidence-based threshold
  recalibration in scope.

## Selected Architecture

Create a focused calculation package under `backend/app/services/breadth/`:

```text
backend/app/services/breadth/
    __init__.py
    types.py
    universe.py
    formulas.py
    ratios.py
    engine.py
    persistence.py
```

### Responsibilities

- `types.py` defines immutable calculation requests, universe inputs,
  per-symbol features, metric eligibility, coverage, and daily results.
- `universe.py` classifies broad common-stock membership and date-specific
  StockBee USD liquidity eligibility.
- `formulas.py` owns adjusted returns, rolling extrema, moving averages, Wilder
  ATR, 52-week highs/lows, and every signal predicate.
- `ratios.py` owns inclusive five- and ten-session ratio calculation.
- `engine.py` vectorizes features across symbols and dates and produces canonical
  daily results without querying or writing the database.
- `persistence.py` maps canonical results to `MarketBreadth`, enforces the active
  calculation revision, and owns transactional row writes.

The engine receives universe snapshots, OHLCV histories, historical FX, and
requested trading dates. It never calls a provider, reads a cache, queries the
database, or commits a transaction.

Existing workflows become adapters:

- `BreadthCalculatorService` loads live dependencies and delegates calculation.
- `BreadthBackfillExecutor` plans date ranges and delegates batch calculation.
- `StaticBreadthSectionBuilder` delegates its cache-only calculations instead of
  reimplementing formulas.
- `BreadthAttributionService` reuses the canonical daily signal predicates so
  group totals reconcile with market totals.

No breadth predicate may remain duplicated in a live, backfill, attribution, or
static-export path.

### Alternatives Rejected

Refactoring only private methods inside the existing calculator was rejected
because it would retain database, cache, formula, ratio, and persistence
coupling in an already large service. A per-symbol database feature warehouse
was rejected because it introduces storage and pipeline scope not required to
correct breadth.

## Price and Corporate-Action Policy

Signal formulas use an adjusted price series:

- adjusted close is the persisted provider-adjusted close;
- adjusted open, high, and low are raw OHLC multiplied by
  `adjusted_close / raw_close` for that session;
- returns, extrema, moving averages, ATR, and 52-week highs/lows use adjusted
  values.

Tradability formulas use actual market values:

- the USD 5 price cutoff uses raw close converted with historical FX;
- dollar volume uses raw close times volume, converted with historical FX;
- share-volume predicates use persisted volume directly.

Prices, FX, and derived features must be finite. Raw and adjusted prices used as
divisors must be greater than zero. Invalid values exclude the symbol from the
affected metric; they never become zero returns.

## Universe Policy

### Broad universe

The broad universe contains date-active common equities for the selected market.
It excludes ETFs, ETNs, mutual and closed-end funds, preferred shares, warrants,
rights, debt instruments, indices, benchmarks, and inactive listings.

The engine consumes the repository's date-specific universe snapshots. Security
classification must be explicit at this boundary; breadth calculation must not
assume upstream ingest happened to remove every non-common-stock instrument.
`stock_universe.is_common_stock` records that classification. Authoritative
equity ingestion sets it true; manual rows migrate and default to false until
an operator explicitly confirms they are common equities.

### StockBee universe

For session `t`:

```text
dollar_volume_i = raw_close_i * fx_to_usd_i * volume_i
adtv20_t = mean(dollar_volume over t-19 through t)

stockbee_liquidity_eligible_t = adtv20_t >= 250_000 USD
```

The StockBee universe is the intersection of the broad universe and the
date-specific liquidity condition. Monthly indicators additionally require the
raw close 20 sessions ago, converted with that date's FX, to be at least USD 5.

Signal conditions such as daily volume expansion do not change the universe
signature. They determine whether an otherwise eligible stock contributes to a
signal count.

### Metric-specific eligibility

| Metric family | Minimum usable history and data |
|---|---|
| Advance/decline | Two adjusted closes |
| Daily StockBee 4% | Two adjusted closes, two volumes, and ADTV20 |
| Monthly StockBee | Twenty-session reference close, ADTV20, and USD 5 reference price |
| 34-day StockBee | 34 adjusted closes and ADTV20 |
| Quarter StockBee | 65 adjusted closes and ADTV20 |
| T2108 | 40 adjusted closes |
| 10x ATR extension | 50 adjusted OHLC sessions and valid ATR14 |
| 52-week high/low | 252 adjusted OHLC sessions |

Each family persists its eligible count. A zero eligible count is distinct from
a zero signal count. Existing non-null signal columns may store zero when the
eligible count is zero; API/UI serialization displays an em dash when the
corresponding denominator is zero.

## Canonical Formulas

All thresholds are evaluated at full precision. Rounding is applied only when a
ratio or percentage is serialized.

Let `AC_t` be adjusted close, `RC_t` raw close, and `V_t` volume.

### StockBee primary indicators

```text
daily_return_t = (AC_t / AC_t-1) - 1

stocks_up_4pct:
    StockBee daily eligible
    and daily_return_t >= 0.04
    and V_t >= 100_000
    and V_t > V_t-1

stocks_down_4pct:
    StockBee daily eligible
    and daily_return_t <= -0.04
    and V_t >= 100_000
    and V_t > V_t-1
```

The ratio window includes the current session:

```text
ratio_Nday_t =
    sum(stocks_up_4pct over t-(N-1) through t)
    /
    sum(stocks_down_4pct over t-(N-1) through t)
```

`N` is five or ten canonical trading rows. Fewer than `N` rows or a zero
denominator returns null.

### StockBee secondary indicators

Quarter windows include the current session:

```text
quarter_low_t = min(AC over trailing 65 sessions)
quarter_high_t = max(AC over trailing 65 sessions)

stocks_up_25pct_quarter:
    (AC_t / quarter_low_t) - 1 >= 0.25

stocks_down_25pct_quarter:
    (AC_t / quarter_high_t) - 1 <= -0.25
```

The obsolete StockBee `+0.01` TC2000 workaround is not used because invalid or
non-positive prices are rejected.

Monthly signals use the adjusted close exactly 20 sessions ago:

```text
month_return_t = (AC_t / AC_t-20) - 1

stocks_up_25pct_month: month_return_t >= 0.25
stocks_down_25pct_month: month_return_t <= -0.25
stocks_up_50pct_month: month_return_t >= 0.50
stocks_down_50pct_month: month_return_t <= -0.50
```

The 34/13 signals use rolling extremes, including the current session:

```text
low_34_t = min(AC over trailing 34 sessions)
high_34_t = max(AC over trailing 34 sessions)

stocks_up_13pct_34days:
    (AC_t / low_34_t) - 1 >= 0.13

stocks_down_13pct_34days:
    (AC_t / high_34_t) - 1 <= -0.13
```

### Broad context indicators

```text
advancing: AC_t > AC_t-1
declining: AC_t < AC_t-1
unchanged: AC_t == AC_t-1 at persisted provider precision
```

The three counts must reconcile to their eligible denominator.

### StockBee/classic 52-week highs and lows

Let `AH_t` and `AL_t` be adjusted high and adjusted low:

```text
new_high_52week:
    AH_t > max(AH over the preceding 251 sessions)

new_low_52week:
    AL_t < min(AL over the preceding 251 sessions)
```

Strict comparison means a repeated equal high or low is not counted as a new
record.

### StockBee T2108

```text
sma40_t = mean(AC over trailing 40 sessions, including t)
t2108_count_t = count(AC_t > sma40_t)
t2108_pct_t = 100 * t2108_count_t / t2108_eligible_count_t
```

T2108 uses the broad common-stock universe. The screenshot's above-50DMA metric
is not implemented.

### Screenshot-derived 10x ATR extension

Adjusted true range is:

```text
TR_t = max(
    AH_t - AL_t,
    abs(AH_t - AC_t-1),
    abs(AL_t - AC_t-1)
)
```

`ATR14` uses Wilder smoothing. `SMA50` includes the current adjusted close:

```text
gain_from_sma50_pct = 100 * (AC_t - SMA50_t) / SMA50_t
atr_pct = 100 * ATR14_t / AC_t
extension_ratio = gain_from_sma50_pct / atr_pct

atr_10x_extension:
    gain_from_sma50_pct > 0
    and ATR14_t > 0
    and extension_ratio >= 10
```

The UI labels this metric Screenshot-derived because the referenced service does
not publish its backend formula.

## Historical FX Policy

For non-USD markets, use the exact-date FX observation when available. Otherwise
use the latest earlier observation within seven calendar days. Never use a
future FX rate. USD instruments use a factor of `1.0`.

If no acceptable FX observation exists, the entire market/date calculation
fails. It must not partially commit or silently change liquidity membership.

## Persistence and API Contract

Existing fields retain their names and receive corrected values:

```text
stocks_up_4pct
stocks_down_4pct
ratio_5day
ratio_10day
stocks_up_25pct_quarter
stocks_down_25pct_quarter
stocks_up_25pct_month
stocks_down_25pct_month
stocks_up_50pct_month
stocks_down_50pct_month
stocks_up_13pct_34days
stocks_down_13pct_34days
total_stocks_scanned
```

`total_stocks_scanned` remains for compatibility and becomes a deprecated alias
of `broad_universe_count`.

Add these nullable columns through Alembic:

```text
advancing_count
declining_count
unchanged_count
new_high_52week_count
new_low_52week_count
t2108_count
t2108_pct
atr_10x_extension_count
broad_universe_count
advance_decline_eligible_count
stockbee_daily_eligible_count
stockbee_month_eligible_count
stockbee_34day_eligible_count
stockbee_quarter_eligible_count
t2108_eligible_count
high_low_52week_eligible_count
atr_extension_eligible_count
stockbee_eligibility_signature
calculation_revision
```

`eligibility_signature` continues to identify the broad input universe.
`stockbee_eligibility_signature` identifies the exact date-specific common-stock
set passing the USD liquidity policy. `calculation_revision` is an internal
integer guard. It does not create coexisting formula versions.

The breadth response remains flat and additive. Existing clients can ignore new
fields. Updated consumers use explicit denominator fields rather than inferring
coverage from `total_stocks_scanned`.

## Migration and Cutover

The migration uses a short breadth maintenance window and a temporary shadow
dataset.

1. Apply additive Alembic migrations for the new nullable breadth columns and
   explicit common-stock classification. Existing manual rows migrate
   fail-closed until reviewed.
2. Build a deployment image containing the shared engine and rebuild command
   without activating revision-2 production writers.
3. Create a temporary `market_breadth_rebuild` table matching the target schema.
4. Recalculate every supported market/date from authoritative universe, OHLCV,
   and FX inputs with at least 252 sessions of warm-up data.
5. Recalculate five- and ten-session ratios only from corrected daily counts.
6. Persist the exact requested market/date manifest and validate formulas, exact
   row coverage, denominators, signatures, FX provenance, and downstream state
   distributions.
7. Pause every breadth, exposure, digest, snapshot, and static-export writer.
8. Take a database backup for operational rollback.
9. In one transaction, delete existing `market_breadth` rows and insert the
   validated revision-2 rebuild rows.
10. Rebuild persisted `market_exposure` history affected by breadth inputs.
11. Regenerate UI snapshots and static market artifacts.
12. Clear Redis breadth, digest, regime, and related snapshot caches.
13. Deploy the revision-2 backend and all Celery writers together.
14. Deploy the updated frontend, resume jobs, and monitor diagnostics.
15. Remove the temporary rebuild table and rollback snapshot after the agreed
    operational retention window.

Old and new breadth writers must never run concurrently after cutover. The API
serves only `calculation_revision = 2` rows. A failed cutover restores the backup
and previous application release; legacy calculations are not preserved as a
product option.

## UI Design

Use one integrated live/static presentation.

### Header and context strip

The header shows market, latest trading date, broad universe, StockBee daily
eligible count, and a small source legend.

The current context strip shows:

- advancing and declining counts and percentages of
  `advance_decline_eligible_count` — Context;
- new 52-week highs and lows — StockBee;
- T2108 and its eligible denominator — StockBee;
- 10x ATR extension count and denominator — Screenshot-derived;
- broad common-stock universe and coverage.

### Chart and grouped table

Retain the existing 4% up/down chart and market benchmark overlay. On desktop,
use the selected integrated chart/table layout. On narrow screens, stack the
chart above the horizontally scrollable table.

The historical table has sticky date and grouped headers. The Primary and
Secondary grouping is labeled as screenshot-derived; the individual formulas
are labeled StockBee:

```text
Primary Breadth · Screenshot grouping
    StockBee formulas: Up 4%, Down 4%, 5-day ratio, 10-day ratio

Secondary Breadth · Screenshot grouping
    StockBee formulas: Quarter +25%, Quarter -25%
    Month +25%, Month -25%
    Month +50%, Month -50%
    34-day +13%, 34-day -13%

Context
    10x ATR, T2108, Broad universe
```

Advancing/declining and 52-week high/low remain in the current context strip to
avoid four additional table columns. Their history remains available through the
API.

Every metric heading exposes an accessible tooltip containing origin,
plain-language formula, required history, eligible denominator, and applicable
USD liquidity rule.

Up counts use green text and down counts red. Ratios above one receive a
restrained green tint and ratios below one a red tint. Secondary pairs tint the
larger side. T2108 and universe remain neutral until evidence-backed
interpretation bands exist. Missing or ineligible values display an em dash.
Color is never the only carrier of meaning.

Live and static pages share the context strip, grouped table, tooltip, and
formatting components. The static page retains its Overview and By Group
navigation.

## Downstream Consumers

Corrected breadth continues to feed market exposure, digest stance, stock-detail
regime, watchlist stewardship, market copilot, group attribution, snapshots, and
static exports.

Run each existing classification rule against corrected history. Preserve a
threshold only when rebuilt state frequencies remain reasonable. Any threshold
change requires fixture-backed tests and an explanation in the migration report.
This scope validates and recalibrates breadth inputs; it does not redesign the
consumer scoring systems.

Migration-day notifications remain paused until rebuilt data, exposure, and
snapshots are published. Recalculation must not appear as a real-time breadth
event.

## Error Handling

- Missing history excludes a stock only from affected metrics.
- Missing the exact target-date trading bar excludes the stock from all metrics
  for that date.
- Invalid adjusted prices do not become zero returns.
- Invalid or non-positive ATR excludes the stock from ATR eligibility.
- Missing acceptable FX fails the market/date.
- Zero ratio denominators return null.
- A market/date result commits atomically or not at all.
- Live failure preserves the last valid row and reports the failed target date
  through existing task and coverage diagnostics.
- Post-cutover API queries ignore any row not carrying revision 2.

## Verification

### Formula tests

Hand-calculated fixtures cover:

- exact positive and negative 4%, 13%, 25%, and 50% boundaries;
- 99,999 versus 100,000 shares;
- current volume equal to versus greater than prior volume;
- ADTV exactly at USD 250,000;
- monthly prior USD price exactly at USD 5;
- point-to-point versus rolling-extreme behavior;
- current-session inclusion in five- and ten-session ratios;
- zero ratio denominator;
- split-adjusted signals with raw-price liquidity;
- metric-specific recent-IPO eligibility;
- T2108 at exactly the SMA40;
- Wilder ATR initialization and smoothing;
- ATR extension exactly at and immediately below 10;
- strict 52-week boundaries and repeated equal highs/lows;
- exact-date and prior-date FX selection; and
- missing, zero, and non-finite values.

### Workflow parity

The same fixture dataset passes through live calculation, historical rebuild,
static calculation, and group attribution. Applicable counts and ratios must be
identical.

### Migration and interface tests

- Alembic upgrade and downgrade tests cover every new column.
- PostgreSQL rehearsal covers shadow rebuild and transactional replacement.
- Revision guards reject stale rows and snapshots.
- Cache invalidation and static regeneration are verified.
- Existing API fields remain present with compatible types.
- Live and static pages render the shared components and values.
- Responsive grouped tables, tooltips, origins, denominators, and missing values
  have frontend tests.
- Digest, exposure, stock regime, watchlist, and copilot behavior have corrected
  breadth regression fixtures.

## Completion Criteria

- One package owns every breadth predicate.
- No formula copy remains in live, backfill, attribution, or static workflows.
- All production breadth rows carry revision 2.
- Ratios contain only corrected inclusive daily counts.
- Broad and metric-specific denominators reconcile.
- Group attribution reconciles to market counts apart from explicitly reported
  unclassified stocks.
- Live and static UI values and origin labels match.
- A migration rehearsal succeeds against a copy of an existing database.
- Existing unrelated application data remains unchanged.

## Non-Goals

- Retaining or exposing the previous point-to-point formulas.
- Supporting user-selectable breadth methodologies.
- Implementing the screenshot's above-50DMA metric.
- Implementing the screenshot's one-day new-high/new-low metric.
- Creating a persistent per-symbol breadth feature warehouse.
- Redesigning market exposure, digest, or regime scoring beyond validated
  breadth threshold recalibration.
- Changing supported markets or market calendars.
