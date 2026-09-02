# MARKET INTELLIGENCE PRODUCTION HARDENING v2 COMPLETE

Status: implementation and closeout verification complete. The implementation
evidence is recorded below; the later documentation-only head and its checks are
reported in the final delivery response to avoid a self-referential commit SHA.

## 1. Final SHA

The final implementation SHA is `7306ed23d908b5bdbcb282d3a0ac205ea05ebe1f`.
The later closeout commit changes documentation/evidence only; the exact final
branch head is reported by `git status` and the final delivery response.

## 2. Draft PR state

Draft PR [txu221/stock-screener#1](https://github.com/txu221/stock-screener/pull/1)
remains open on `feat/market-intelligence-engine`. It is not merged. No commit
was pushed to `xang1234/stock-screener`, and no force push was used.

## 3. Corporate-action model

Migration `20260828_0034` additively extends current `stock_prices`, adds action
evidence to bounded canonical bars, and creates append-only
`stock_price_revisions`. Current rows and every revision can retain raw OHLCV,
provider adjusted close, adjustment factor, split ratio, cash dividend,
provider, source timestamp, normalization version, price basis, content hash,
revision number, and reconciliation timestamp. Existing legacy rows remain
valid with nullable v2 fields.

## 4. Price basis

Raw OHLC is retained for quoted-price/candlestick display. Analytical history
uses Yahoo provider adjusted close only when the row proves the complete v2
provenance contract. The reconciled basis is
`yahoo_adjusted_close_provider_volume`; unverified input is
`raw_ohlcv_unreconciled`. Raw close is never silently substituted and labeled
adjusted.

## 5. Split handling

The normalization boundary persists provider `Stock Splits` evidence and uses
`adjustment_factor = Adj Close / Close`. Deterministic fixtures cover 2-for-1,
3-for-1, and reverse splits and prove the mechanical split does not become a
false analytical return.

## 6. Dividend semantics

Provider `Dividends` evidence is stored separately. Because Yahoo adjusted close
can reflect cash distributions, analytical returns are described as
provider-adjusted total-return proxies, not guaranteed pure price return. The
system does not infer missing dividends or claim an independently reconciled
total-return index.

## 7. Historical revision semantics

Every distinct provider row is content-addressed. A changed historical download
appends a numbered revision and updates the current row within one transaction;
only evidence identical to the current materialized row is idempotent. A
provider sequence A-to-B-to-A therefore records revision 2 and restores A as
current instead of confusing historical recurrence with replay. ORM guards and
a PostgreSQL trigger reject revision UPDATE/DELETE. The ledger has no parent
foreign key, so deleting a current price row cannot cascade-delete historical
evidence. Re-ingesting evidence equal to the retained latest revision rebuilds
the missing current materialization without duplicating the ledger; a retained
older revision reappearing after a newer one receives the next revision number.
An unproven provider-less/native refresh cannot downgrade a reconciled current
row.

## 8. Normalization version

The new stock-price normalization version is
`canonical_price_adjustment_v2`. It is distinct from Phase 1
`market_intelligence_adjusted_ohlcv_v1`; no historical semantic change is hidden
under the old identifier.

## 9. Old snapshot compatibility

Existing snapshots remain immutable and retain their original metric and
normalization versions. Legacy stock rows are not mass-rewritten. Any deliberate
recalculation must create a new audited run/revision and pass the same complete,
atomic publication policy.

## 10. Structured logging

Pipeline/task logs use structured fields for run ID, task ID, trading date,
metric/normalization/pipeline versions, provider, stage, duration, expected,
received, valid and rejected coverage, publication state, retry/reuse state,
force refresh, and broker redelivery lifecycle. They do not depend on parsing a
large message string.
Completion events always include explicit `normalization_version`,
`expected_symbols`, `received_symbols`, `valid_symbols`, and
`rejected_symbols` fields.

## 11. Error taxonomy

Stable categories are `PROVIDER_FAILURE`, `PROVIDER_SCHEMA_DRIFT`,
`INVALID_MARKET_DATA`, `DATABASE_FAILURE`, `LOCK_TIMEOUT`,
`PUBLICATION_FAILURE`, `CELERY_DELIVERY_FAILURE`, `STALE_DATA`,
`INSUFFICIENT_HISTORY`, and
`CORPORATE_ACTION_RECONCILIATION_FAILURE`. Categories are selected by the failed
stage and typed condition; request-level failures remain distinct from row
rejections.

## 12. Pipeline timings

Persisted timing keys are `provider_fetch_ms`, `normalization_ms`,
`validation_ms`, `calculation_ms`, `persistence_ms`, `publication_ms`, and
`total_ms`. Invalid negative/non-finite timing evidence is rejected. Final audit
and lifecycle writes remain inside the correct persistence/publication
transaction boundary.
Persisted `total_ms` is the pre-commit envelope that can be atomically stored
with the run. The completion log separately records `persisted_duration_ms`,
`commit_ms`, and a committed `duration_ms` measured after commit returns.

## 13. Health improvements

`/api/v1/market-intelligence/sectors/health` now includes latest success and
attempt ages, provider latency, stable failure category, consecutive failures,
last successful trading date, completed-session stale threshold, pipeline
version, stage timings, and publication/retry/reuse state. One aggregate query
prevents a response from mixing different `READ COMMITTED` snapshots. `/livez`
does not call dependencies; readiness distinguishes fatal PostgreSQL failure
from degraded Redis or missing stable-snapshot state and never calls Yahoo.
Only a snapshot classified `FRESH` by completed US sessions is ready;
`AGING`, `STALE`, and `UNAVAILABLE` are soft-degraded warnings.

## 14. Freshness states

Freshness uses completed US sessions: latest session is `FRESH`, one session
behind is `AGING`, two or more is `STALE`, and missing/inconsistent evidence is
`UNAVAILABLE`. Weekends and holidays do not cause wall-clock false staleness.

## 15. PostgreSQL baseline

The dedicated evidence job used PostgreSQL 16.15, database
`market_intelligence_slo`, real Alembic head `20260829_0035`, 500 equities, 528
price symbols, 47,520 price rows, and 60 complete sector sessions with 12 symbols
per session. Redis was bypassed for this uncached database/read-path baseline.
The closeout fixture now seeds full v2 factor/action/provider/source/hash/
revision/reconciliation evidence for every price row so the final run measures
the current provenance-validation path instead of short-circuiting on legacy
rows.

## 16. API p50/p95

GitHub Actions run `33567003218` excluded one warm-up and measured 20 requests
per family against PostgreSQL 16.15 with full valid v2 provenance on every
synthetic price row:

| Family | p50 ms | p95 ms | Worst ms |
| --- | ---: | ---: | ---: |
| Overview | 43.391 | 44.384 | 44.661 |
| Movers | 444.842 | 799.767 | 802.126 |
| ETFs | 98.201 | 101.175 | 107.270 |
| Sectors latest | 8.067 | 8.378 | 8.803 |
| Sectors history | 182.192 | 183.733 | 185.859 |
| Sectors health | 21.129 | 21.630 | 21.636 |

These are comparison-only in-process production-router/PostgreSQL measurements. They include
routing, dependency overrides, ORM materialization, application logic, SQL, and
serialization, but exclude network/TLS/Uvicorn and real authentication.

## 17. SLO

All six read families enforce a common unrounded p95 below 1000 ms in the
dedicated PostgreSQL 16 job. The threshold was selected after the first
20-sample baseline and still leaves 200.233 ms (25.0%) above the final
full-provenance maximum p95 without hiding a full-second regression. Run
`33567003218` completed with enforcement enabled and is the authoritative
implementation evidence.

## 18. Query optimizations

The slowest observed query was the Movers price load. The reader now projects
only the 18 fields required by metrics/provenance and does not materialize
`StockPrice` ORM entities. Its RVOL20 input is bounded to 42 calendar days,
returning 15,500 rows/31 sessions per equity instead of 21,500 rows while still
retaining ten sessions beyond the required current plus 20 prior sessions.
`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` used `ix_stock_prices_date`, completed
in 21.500 ms, hit 1,114 shared buffers, and used no shared reads. Its explicit
ordering performed a 3,928 kB external merge sort (491 temporary blocks read,
492 written), but SQL was only 14.0% of Movers API time. Evidence did not justify
another write-amplifying index for a query selecting 500 of 528 symbols, so none
was added. Sector history's 182 SELECTs/request remains a documented N+1
follow-up; it meets the initial SLO but should be batched in a separate change.

## 19. Cache strategy

Redis read-through keys remain enabled for sector latest/history, whose complete
source generation is immutable. They use cache format `v2` and bind endpoint,
canonicalized parameters, metric version, and immutable published generation.
TTL is bounded from 60 seconds through seven days. Same-key sector misses share
an in-process single-flight computation; unrelated keys proceed independently.

Overview, Movers, and ETF reads also depend on mutable `stock_prices`, so they
bypass Redis response caching until an immutable price-generation token exists.
A same-feature-pointer provider revision is therefore visible immediately.

## 20. Cache correctness

Only a `SUCCEEDED` atomically published generation can become a stable sector
cache source. Partial/failed attempts leave the prior generation untouched.
Transaction-serialized, monotonically increasing publication timestamps prevent
same-session, A-to-D-to-A, and late lower-ID publication ABA reuse. Cached JSON
is strict and rejects scalar, legacy, malformed, NaN, or infinity payloads.

## 21. Redis-down behavior

Redis is optional acceleration. Connection, read, decode, schema-validation, or
write failures fall back to PostgreSQL without converting a valid read into HTTP
500. Readiness reports Redis as degraded rather than killing application
liveness.

## 22. Provider schema drift protection

Yahoo validation covers required columns, dtypes, fixed symbol coverage, ordered
dates, duplicate timestamps, timezone normalization, adjusted close, volume,
action columns, and adjustment consistency. Schema drift becomes a typed
request-level failure rather than fake missing-symbol or bad-row counts.
Negative action evidence and unexplained extreme factor discontinuities become
`CORPORATE_ACTION_RECONCILIATION_FAILURE` before any batch cache/database write;
the last reconciled materialization is preserved.

## 23. Live Yahoo canary

`.github/workflows/market-intelligence-yahoo-canary.yml` runs once after the US
close on weekdays and supports manual dispatch. It has read-only repository
permission, persists no application data, starts no database/Redis/Celery
service, runs no migration/deployment, and executes only the live Yahoo contract
test. The heavier Yahoo plus real Celery integration remains explicit manual
opt-in so normal pushes do not duplicate provider pressure. Manual run
`33591441325` passed the fixed 12-symbol Yahoo contract and the real
broker-worker/idempotent-rerun test on implementation commit `7306ed23`.
The validator uses exact completed-session anchors: a provider history gap
produces unavailable metrics/PARTIAL evidence rather than a `KeyError`, a
forward-fill, or a compressed-session calculation.

## 24. Data-quality UI disclosure

Overview, Movers, and ETF API quality is derived from every loaded row's actual
provider, source timestamp, v2 normalization, price basis, recomputed content
hash, non-negative revision (including valid first revision 0), reconciliation
timestamp, adjusted close, and adjustment factor. The reader also requires the
factor to match `adjusted_close / raw_close` within a strict floating-point
tolerance. Fully proven history is
`corporate_action_adjusted`; empty, legacy, mixed, or malformed provenance is
`partial_corporate_action_adjustment`. Full coverage shows exactly:
“Historical analytical returns use corporate-action-adjusted prices.” Partial
coverage warns that legacy or unverified rows may be included.
Hash results are memoized only by the complete hash-defining evidence tuple in a
bounded process-local cache. Any source/action/value/version change produces a
new key, and a changed stored hash is still compared with the correct expected
hash; this optimization cannot hide a same-pointer provider revision.

## 25. Backend tests

Task-focused deterministic suites cover migrations, append-only revisions,
splits/reverse splits/dividends/provider revisions, Yahoo schema drift,
normalization versioning, observability, error taxonomy and timings,
completed-session freshness, weekend/holiday handling, health/readiness, cache
generation/invalidation/fallback/stampede behavior, performance harness
contracts, and UI/API quality labels. The final comprehensive Windows closeout
selection passed 458 tests before the final read-path changes. The final
expanded hardening selection passed 525 tests and deselected 16 explicitly
service-backed/live cases. It includes the hash memo, lightweight price-row
projection, bounded metric-window quality, retry provider contracts,
A-to-B-to-A price revisions, deleted-current restoration, and exact-session
live-validation gaps.
The complete Linux backend suite is split across four PR jobs and is the final
no-regression authority. The Windows closeout used an in-process `resource`
module shim because that Unix-only stdlib module is absent on Windows; no
production code, skip, or xfail was added.

## 26. Frontend tests

The final Windows closeout run passed all 37 tests in the 11 Market Intelligence
frontend test files. ESLint completed with zero errors and four unrelated
existing warnings. A pre-existing Static Scan test had read a render spy before
the corresponding component was guaranteed ready under CI contention; a
one-line `waitFor` now makes that synchronization explicit, and the file passed
three consecutive local runs (12/12 tests). The native-Windows full run passed
650 and still reported the unrelated legacy `D:\\D:\\...` fixture-path defect
and contended `App.static` timeouts; the latter file passed 9/9 alone. Linux PR
CI is the final complete frontend and Playwright-smoke authority.

## 27. Integration tests

Run `33567003218` passed PostgreSQL migration/publication/concurrency/API tests,
real Redis connectivity, the deterministic Market Intelligence suite, all 53
currently selected service-independent integration tests, the enforced
full-provenance PostgreSQL SLO, frontend tests, lint, and production build. The
real Celery worker/idempotent rerun and live Yahoo contract both passed in
manual opt-in run `33591441325`.

## 28. GitHub Actions

The Market Intelligence workflow supplies PostgreSQL 16, Redis 7, real Alembic
migrations, transaction/concurrency tests, deterministic tests, a dedicated
measured SLO job, optional real Celery/Yahoo validation, frontend checks, and
failure-safe artifacts. Final implementation run `33567003218` is green on
commit `7306ed23`; manual run `33591441325` supplies the opt-in Yahoo/Celery
evidence without adding provider pressure to normal pushes.

## 29. PR CI

General PR CI validates assistant compose, four complete backend-unit shards,
backend quality gates, frontend lint/test, and Playwright smoke. A stale Yahoo
test was updated earlier to supply the now-required explicit provider
provenance, and the final Static Scan render-spy test now waits for component
readiness rather than racing it. PR CI run `33567008342` is fully green on
commit `7306ed23`.

## 30. Production build

The final Windows build compiled 2,515 modules successfully in 1 minute 32
seconds. Linux run `33567003218` also passed the production build, and PR run
`33567008342` passed frontend lint, tests, and Playwright smoke.

## 31. Security

No credential, token, device identifier, or machine-local state is included.
The final local scan examined 86 changed/untracked files and found zero files
matching high-confidence private-key, AWS access-key, GitHub token, Slack token,
or JWT patterns. `git diff --check` reported no whitespace error. No production
permission is granted to the Yahoo canary, and checkout credentials are not
persisted there.

## 32. Dependency assessment

`npm audit --json` reports 22 package nodes: 1 critical, 17 high, 3 moderate,
and 1 low. This is one additional `browserslist` advisory compared with the
earlier evidence, despite an unchanged lockfile, so it is an advisory-feed
change rather than a hardening dependency change. The critical Vitest issue is dev-only;
`npm audit --omit=dev` reports zero critical, six high, two moderate. Direct
runtime follow-ups are Axios and React Router; remaining production-graph
findings include Node-only Axios transitive paths and Recharts/Lodash. Full
reachability and isolated upgrade recommendations are in
`docs/dependency-security-assessment.md`. No `npm audit fix` was run. `pip check`
reports no broken requirements, and this hardening range changes no dependency
manifest or lockfile.

## 33. Files changed

Relative to hardening base `6d75e8a4`, the implementation range through
`7306ed23` changes 86 files with 11,140 insertions and 514 deletions. Scope is
limited to additive
migrations/models, existing Market Intelligence provider,
pipeline/read/cache/API/task seams, targeted React disclosure, CI workflows,
fixtures/tests, and documentation. It adds no new application, authentication,
market universe, page, or external dependency.

## 34. Commits

The 32-commit implementation series begins at `07fed279` and uses small design,
schema, normalization, provider, observability, health, cache, SLO, regression,
UI-quality, review-fix, and closeout-documentation commits. The exact ordered
list remains available from `git log --oneline 6d75e8a4..HEAD`.

## 35. Known limitations

- Yahoo remains the sole Phase 1 market-data provider; the canary detects but
  does not eliminate provider outage or policy risk.
- Yahoo adjusted close is a provider-adjusted total-return proxy, not an
  independently reconciled pure-price or official total-return index.
- Sector history performs 182 SELECTs/request and should be batched even though
  it meets the initial SLO.
- The SLO excludes network, TLS, reverse proxy, Uvicorn scheduling, and real auth
  latency.
- The existing Windows fixture-path and contended full-frontend-suite failures
  remain platform baseline items; Linux CI is authoritative for final
  no-regression evidence.
- The 22 npm advisories remain recorded technical debt; this phase intentionally
  did not mix dependency upgrades with correctness changes.
- Universe coverage was not expanded, and no AI, news, options, institutional
  flow, prediction, alert, recommendation, or backtest feature was added.

## 36. Recommended next milestone

Run a short production-like operational burn-in over the weekday canary, health
states, cache generations, and enforced SLO. In separate small changes, batch
the sector-history N+1 reads and remediate direct runtime/dev-tool dependency
advisories. Only after that evidence remains stable should the team decide
whether to expand Market Intelligence coverage. This report does not authorize
or begin that next milestone.
