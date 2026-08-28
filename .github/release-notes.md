# Stock Scanner v1.5.0

Stock Scanner v1.5.0 delivers a shared revision-2 market-breadth engine, balanced relative-strength parity across live and static applications, stronger multi-market calendar and publishing controls, and more resilient background data refreshes.

## Highlights

### One canonical market-breadth calculation layer

- Replaces duplicated live, static, backfill, and rebuild formulas with one revision-2 breadth engine for every breadth-enabled market.
- Adds StockBee primary and secondary breadth metrics, today-inclusive 5-day and 10-day ratios, advance/decline and new-high/new-low context, T2108, ATR extension, and per-formula eligibility counts.
- Uses point-in-time common-stock universes and historical FX conversion for non-US liquidity tests while keeping the existing flat breadth API backward compatible.
- Adds a validated shadow rebuild, atomic cutover tooling, rollback guidance, and revision-aware consumers so old and new breadth data cannot be silently mixed.
- Redesigns the live and static breadth views with health bars, significance-based color intensity, compact Primary/Secondary/Context history sections, formula-source tooltips, benchmark overlays, and US industry-group attribution.

### Relative strength and group analytics

- Standardizes balanced market relative strength across live and static workflows and activates it correctly during fresh bootstrap.
- Hydrates benchmark anchors and RRG history before validation, preventing empty or incomplete first-run relative-strength views.
- Repairs static and live group-ranking history, backfill ordering, point-in-time universes, and RRG startup behavior.
- Adds short-horizon group relative-strength columns and populates live group history and RRG data during bootstrap.

### Static-site and multi-market reliability

- Adds provider-aware exchange calendars, audited session invariants, and packaged calendar data for Docker deployments.
- Aligns static coverage gates, breadth history refreshes, market exposure, benchmark anchors, and stale-artifact rejection across supported markets.
- Reduces memory usage in snapshot and market-RS bootstrap workflows and makes enabled-market Celery Beat scheduling explicit.
- Recovers data-fetch work after worker loss and strengthens cache-only execution while market refresh guards are active.

### Screening and operator workflows

- Adds guided grouped filters for scan results.
- Adds the correction-survivor action-state workflow and supporting scan metadata.
- Improves Windows deployment guidance and clarifies Celery Beat timezone configuration.

## Deployment

Release images are published to GHCR under the `v1.5.0` tag:

- `ghcr.io/<owner>/stockscreenclaude-backend:v1.5.0`
- `ghcr.io/<owner>/stockscreenclaude-frontend:v1.5.0`

Set `APP_IMAGE_TAG=v1.5.0` in the deployment environment, pull the images, and recreate the application services using the normal Docker Compose deployment command.

## Upgrade notes

- Apply the included database migrations through revision `0032` using the normal deployment migration process.
- Existing breadth rows are not silently rewritten. Follow `docs/runbooks/market-breadth-revision-2-cutover.md` to rebuild, validate, activate, monitor, and, if necessary, roll back revision-2 breadth data.
- Regenerate static market artifacts after the breadth cutover so static pages do not retain revision-1 breadth snapshots.
- Non-US breadth liquidity calculations now require aligned historical FX coverage. Missing FX data is treated as an explicit calculation failure rather than silently falling back.
- Packaged calendar manifests are now authoritative inputs for supported markets; custom deployments should retain the bundled `backend/data/market_calendars` data.

## What's Changed

- Add guided grouped scan-result filters by @xang1234 in https://github.com/xang1234/stock-screener/pull/302
- Make refresh-guarded breadth and group rankings cache-only by @xang1234 in https://github.com/xang1234/stock-screener/pull/303
- Standardize balanced RS across live and static apps by @xang1234 in https://github.com/xang1234/stock-screener/pull/307
- Align bootstrap derived-data readiness by @xang1234 in https://github.com/xang1234/stock-screener/pull/308
- Fix CA static-site fallback threshold by @xang1234 in https://github.com/xang1234/stock-screener/pull/309
- Fix bootstrap reset after premature daily pipeline by @xang1234 in https://github.com/xang1234/stock-screener/pull/310
- Fix bootstrap reference seeding and Finviz ticker parsing by @xang1234 in https://github.com/xang1234/stock-screener/pull/311
- Fix Finviz provider snapshot ticker parsing by @xang1234 in https://github.com/xang1234/stock-screener/pull/313
- Fix CN weekly-reference NaN-volume crash by @xang1234 in https://github.com/xang1234/stock-screener/pull/314
- Fix Asia static-site publish blockers by @xang1234 in https://github.com/xang1234/stock-screener/pull/315
- Reject failed optional-market exports by @xang1234 in https://github.com/xang1234/stock-screener/pull/316
- Hydrate static RRG startup price history by @xang1234 in https://github.com/xang1234/stock-screener/pull/317
- Clarify Celery Beat timezone configuration by @kjpou1 in https://github.com/xang1234/stock-screener/pull/298
- Bootstrap static RRG history with the current universe by @xang1234 in https://github.com/xang1234/stock-screener/pull/319
- Hydrate static RS benchmark anchors before validation by @xang1234 in https://github.com/xang1234/stock-screener/pull/320
- Fix static group rankings and historical rank data by @xang1234 in https://github.com/xang1234/stock-screener/pull/321
- Fix static group-rank history backfill ordering by @xang1234 in https://github.com/xang1234/stock-screener/pull/322
- Fix static group-history universe bootstrap by @xang1234 in https://github.com/xang1234/stock-screener/pull/323
- Populate live group history and RRG during bootstrap by @xang1234 in https://github.com/xang1234/stock-screener/pull/324
- Harden static-site no-current-artifact policy by @xang1234 in https://github.com/xang1234/stock-screener/pull/326
- Manage Beat scheduling for enabled markets by @xang1234 in https://github.com/xang1234/stock-screener/pull/327
- Activate balanced market RS on fresh bootstrap by @xang1234 in https://github.com/xang1234/stock-screener/pull/328
- Fix fresh-bootstrap RS activation by @xang1234 in https://github.com/xang1234/stock-screener/pull/329
- Fix fresh-bootstrap classification after seed imports by @xang1234 in https://github.com/xang1234/stock-screener/pull/330
- Reduce memory usage during daily snapshot builds by @xang1234 in https://github.com/xang1234/stock-screener/pull/331
- Add provider-aware market calendars and session invariants by @xang1234 in https://github.com/xang1234/stock-screener/pull/332
- Reduce market-RS bootstrap memory use by @xang1234 in https://github.com/xang1234/stock-screener/pull/333
- Fix static exposure/breadth parity by @xang1234 in https://github.com/xang1234/stock-screener/pull/334
- Expand static price refresh to cover breadth history by @xang1234 in https://github.com/xang1234/stock-screener/pull/335
- Keep static market exports from falling back to stale data by @xang1234 in https://github.com/xang1234/stock-screener/pull/336
- Add short-horizon group RS columns by @xang1234 in https://github.com/xang1234/stock-screener/pull/337
- Align static coverage gates across static market workflows by @xang1234 in https://github.com/xang1234/stock-screener/pull/338
- Fix non-US static sites with verified calendars and historical breadth by @xang1234 in https://github.com/xang1234/stock-screener/pull/340
- Fix Docker market-calendar packaging by @xang1234 in https://github.com/xang1234/stock-screener/pull/341
- Update Windows deployment guidance by @xang1234 in https://github.com/xang1234/stock-screener/pull/343
- Add the correction-survivor action-state workflow by @xang1234 in https://github.com/xang1234/stock-screener/pull/345
- Recover data-fetch tasks after worker loss by @xang1234 in https://github.com/xang1234/stock-screener/pull/346
- Unify market-breadth calculations and migration by @xang1234 in https://github.com/xang1234/stock-screener/pull/347

**Full changelog:** https://github.com/xang1234/stock-screener/compare/v1.4.0...v1.5.0
