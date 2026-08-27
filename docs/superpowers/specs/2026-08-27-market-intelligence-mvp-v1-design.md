# Market Intelligence MVP v1 Design

## Status

Approved for continuous implementation by the 2026-08-27 Master Execution Directive. This specification records that approved direction; it does not add scope beyond the directive.

## Outcome

The existing stock-screener application gains one coherent Market Intelligence area that lets a desktop user identify broad-market direction, sector leadership and rotation, S&P 500 movers, liquid ETF relative strength, and the reliability of the displayed data within 30–60 seconds.

## Chosen Approach

Use a hybrid read-model extension of the existing architecture:

- Phase 1 sector snapshots remain authoritative for sector metrics, ranks, rank history, publication status, and Data Health.
- Movers use the latest published US feature-run pointer, active S&P 500 membership, existing company metadata, and existing daily prices.
- Market Pulse and ETF Radar use existing completed daily prices and never call a provider during an API request.
- A thin Python read service performs all financial calculations and returns typed API contracts.
- React provides presentation, filtering controls, and sorting only.

This avoids both a parallel ingestion pipeline and a large rewrite. A snapshot-only approach was rejected because the present Phase 1 snapshot covers sectors rather than the complete mover and ETF universes. A browser-side calculation approach was rejected because it would duplicate financial semantics and make API/UI reconciliation unreliable.

## Navigation and Pages

The existing application navigation gains one primary `Market Intelligence` entry. Its internal routes are:

- Today: Market Pulse plus concise sector leadership and data-freshness context.
- Movers: Top 20 gainers, losers, and unusual-volume stocks in the eligible S&P 500 universe, plus sector breadth counts.
- Sectors: the fixed 11 Sector SPDR ETFs, a 1D/5D/20D/60D period selector, and rotation/rank-change tables. SPY is benchmark-only and is never sector-ranked.
- ETFs: a configured unleveraged ETF universe grouped into Broad Market, Sector, Semiconductor, Software, Biotech, Defense, Energy, Metals, and Uranium.
- Data Health: latest attempt and latest stable published snapshot shown separately.

The UI extends the current Material UI dark-mode system. Dense tables remain readable through labels, arrows, and text in addition to color. Every page displays As of, Last updated, Provider, metric version, and stale/partial state when applicable.

## Read Consistency and Data Flow

The service resolves a completed, published data boundary before calculating a response. It does not mix an in-progress feature run with a published sector snapshot. Database reads are side-effect free:

```text
existing provider/ingestion
  -> existing canonical prices and feature snapshots
  -> published pointer / Phase 1 published sector bundle
  -> MarketIntelligenceReadService
  -> typed FastAPI responses
  -> React Query
  -> MUI pages
```

Missing history, absent symbols, and zero denominators produce `null`, not zero or infinity. Each response explicitly reports its `as_of`, publication timestamp where available, provider/source label, and metric version. Empty or partially available universes remain visible as transparent data-quality states rather than fabricated completeness.

## Market Pulse

The fixed pulse universe is SPY, QQQ, DIA, and IWM. VIX is omitted unless a reliable existing canonical series is found. Each available item exposes price and deterministic 1D/5D/20D/60D completed-session returns. MVP v1 does not invent a risk score or qualitative market-status classification; the page presents raw index performance and sector context.

## Movers Semantics

Eligibility requires active S&P 500 membership, adjusted close greater than $5, and sufficient price/volume history. RVOL20 is:

```text
today volume / mean(previous 20 completed-session volumes)
```

The current session is excluded. A zero prior-volume mean or insufficient history produces `null`. The service returns ordered and capped Top 20 gainers, Top 20 losers, and Top 20 unusual-volume items. Financial ordering occurs on the backend. Company, sector, industry, and market capitalization are returned only when existing metadata supplies them. Sector aggregation reports eligible advancers, decliners, and unchanged members.

## ETF Radar Semantics

The configured universe contains only the symbols approved in the directive and excludes leveraged/inverse products. Metrics use adjusted close on completed sessions:

```text
return_N = adjusted_close_today / adjusted_close_N_sessions_ago - 1
rs_N_vs_spy = etf_return_N - spy_return_N
drawdown_60d = adjusted_close_today / max(adjusted_close over 61 completed sessions) - 1
```

RVOL20 follows the Movers definition. Category and all-universe ranking are deterministic; ties are broken by symbol after equal metric values.

`ETF Strength Score` is descriptive, not predictive. Version `etf_strength_v1` maps each complete component to a deterministic percentile within the available configured universe and produces a 0–100 weighted score:

- 30% 20D relative strength versus SPY
- 25% 60D relative strength versus SPY
- 20% 20D return
- 15% RVOL20 confirmation
- 10% 60D drawdown, where a shallower drawdown is stronger

An item without every required component has a `null` score. The API exposes the formula/version and component values. UI copy must not use expected return, buy, strong buy, potential score, or recommendation language.

## Sector and Flow-Proxy Semantics

Sector formulas, dense ranking, previous ranks, and publication behavior remain the versioned Phase 1 definitions. UI flow terminology is limited to `Flow Pressure` or `Money Flow Proxy`, with this disclosure:

> OHLCV-derived pressure proxy. Not measured institutional or exchange net flow.

## API Boundary

The existing `/api/v1/market-intelligence` router adds:

- `GET /overview`
- `GET /movers`
- `GET /etfs`

Existing `/sectors/latest`, `/sectors/history`, and `/sectors/health` contracts remain compatible. New response schemas are typed and include explicit metadata and nullable financial fields. Query validation rejects unsupported categories, negative thresholds, or invalid limits.

## Failure and Freshness Behavior

- Request/database failure: return the existing API error shape; React shows an error state without stale values being described as current.
- No published US feature run: return a successful empty read model with explicit unavailable reason where appropriate.
- Missing price history: return the affected metric as `null`; do not forward-fill sessions.
- Latest Phase 1 attempt PARTIAL: sector and health pages identify the partial attempt while continuing to display the previous stable published snapshot.
- Stale data: the API carries dates/timestamps; the frontend banner labels the state and does not infer that cached data is current.

## Testing

Implementation is test-first. Backend tests cover fixed universes, published-run selection, adjusted-session returns, RVOL exclusion of today, zero/insufficient denominators, mover eligibility and ordering, sector aggregation, ETF metrics, score weights/version/ties, empty states, and API validation. Frontend tests cover routes, query parameters, loading/error/empty/stale/partial states, the 11-sector invariant, SPY exclusion, period switching, rank direction text, mover filters, ETF categories, score explanation, and freshness banners.

Final verification includes focused and full backend/frontend suites, lint, production build, the real PostgreSQL/Redis/Celery workflow, live Yahoo reconciliation for the requested symbols, dependency/security review, and an independent semantic/accessibility review. No live provider is used by deterministic unit or CI contract tests.

## Scope Guard

This milestone does not add AI, news, options flow, institutional or real ETF fund flow, theme rotation, alerts, portfolio trading, buy/sell recommendations, prediction models, backtesting strategies, or an OTC/full-market scanner.

