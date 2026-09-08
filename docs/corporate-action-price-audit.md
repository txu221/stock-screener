# Corporate-Action Price Audit

Date: 2026-08-28
Scope: current `feat/market-intelligence-engine` at `6d75e8a4`

## Executive Finding

The repository already made Yahoo fetch parameters explicit (`auto_adjust=False`, `actions=True`) and the Phase 1 sector canonical layer preserves raw OHLC, provider adjusted close, a derived adjustment factor, adjusted OHLC, provider volume, provider, timestamps, and normalization version. The remaining production gap is the shared `stock_prices` table used by Today, Movers, ETF metrics, charts, scanners, and several relative-strength services: it stores raw-looking OHLC plus `adj_close`, but no provider/action/factor/revision provenance. Historical corrections are not audit-preserving.

## Current Price Paths

| Path | Current basis | Provenance/revision state | Risk |
|---|---|---|---|
| Phase 1 sector canonical bars | Raw Yahoo OHLC + `Adj Close`; adjusted OHLC derived with `Adj Close / Close` | Provider, source/ingestion timestamps, factor, v1 normalization stored per run | Split/dividend event fields absent; provider revision produces a new run but action cause is not explicit |
| Phase 1 returns/RS/CMF | Adjusted close/OHLC from canonical factor; provider volume | Snapshot and run carry metric/normalization versions | Yahoo adjusted close distribution semantics were not disclosed as total-return proxy |
| Today Market Pulse | `stock_prices.adj_close` | No row-level factor/provider/version/revision | Cannot prove corporate-action reconciliation |
| Movers | `stock_prices.adj_close` and provider volume | Same gap | Split can look like a large move if `adj_close` was aliased or stale |
| ETF Center | `stock_prices.adj_close` | Same gap | Momentum, RS, drawdown, and score can share an unknown historical basis |
| Existing price-history API/charts | Primarily cached `stock_prices` OHLC/Adj Close | No action or revision provenance | UI may combine raw OHLC display with analytical adjusted values without a declared contract |
| Market/industry RS and breadth helpers | Mostly `StockPrice.adj_close`; some feature details contain separate derived returns | No row-level v2 gate | Historical calculations inherit the cache contract weakness |
| Daily bundle import/export | Carries OHLCV and Adj Close | Does not currently carry factor, actions, content hash, or revision | Static transport can erase provenance |

## Raw Close Users

Raw `StockPrice.close` is used in chart/display, last-price, and some scanner/cache paths. Raw OHLC is appropriate for candlesticks and point-in-time quoted prices, but it must not be mixed with adjusted historical anchors in one return formula.

## Adjusted Close Users

Market Intelligence MVP calculations in `app/domain/market_intelligence/mvp.py` correctly use only ordered `adj_close` anchors for 1/5/20/60-session returns and drawdown. Market RS input services also query `StockPrice.adj_close`. Correct formulas do not remove the provenance problem: the database cannot currently prove whether a given value was provider adjusted, an alias of close, imported from a legacy bundle, or revised later.

## Existing Adjustment Normalization

`app/domain/market_intelligence/validation.py` derives one factor from Yahoo raw close and adjusted close, then applies it consistently to raw O/H/L/C. This is internally consistent and is the basis for v2. The general `stock_price_row_from_ohlcv` helper currently falls back to raw close when Adj Close is absent, which makes the semantic basis ambiguous and must not be labeled reconciled.

## Split Handling Gap

Yahoo fetches request action columns, but neither `stock_prices` nor Phase 1 canonical bars persist `Stock Splits`. A correctly adjusted series may avoid a false -50% return, yet the local database cannot explain why the factor changed or distinguish a split from a provider historical revision.

## Dividend Handling Gap

Yahoo Adj Close generally reflects cash distributions as well as splits. Existing Market Intelligence return fields therefore behave as provider-adjusted total-return proxies, but docs and API names did not explicitly separate this from raw price return.

## Historical Revision Gap

`persist_stock_price_mappings` updates the latest row or repairs invalid rows. It does not compare and audit every historical provider revision. The Phase 1 run model is immutable, but the shared price cache has no append-only revision evidence.

## Required v2 Contract

- Preserve raw OHLCV and provider adjusted close.
- Derive and persist a positive finite adjustment factor.
- Persist split ratio, cash dividend, provider, source time, normalization version, content hash, reconciliation timestamp, and current revision.
- Append every distinct historical version to a revision ledger; never silently overwrite a changed historical value.
- Treat legacy/fallback-close rows as unreconciled until refreshed.
- Use only one analytical adjusted basis per calculation chain.
- Expose provider-adjusted analytical return semantics and actual coverage quality.
- Keep old snapshots immutable and identifiable by v1 normalization.

## Audit Conclusion

Repository-selection and Phase 1 architecture remain valid. The minimum safe hardening is an additive extension of the existing price persistence boundary, not a parallel Market Intelligence price store and not provider calls from API reads.

## Hardening v2 remediation status

The audit gaps above describe the pre-hardening base at `6d75e8a4`. Hardening
v2 additively implemented the v2 `stock_prices` provenance fields and immutable
revision ledger. Batch refreshes now preserve per-symbol Yahoo provenance,
provider-less data cannot downgrade a reconciled row, and delete/re-ingest
continues the retained revision sequence. Yahoo batches with negative action
values or an extreme adjustment-factor discontinuity lacking adjacent
split/dividend evidence are rejected before Redis or PostgreSQL writes, with
stable category `CORPORATE_ACTION_RECONCILIATION_FAILURE`; the previous
reconciled current row remains available. Legacy rows remain explicitly partial
until proven by a v2 refresh.
