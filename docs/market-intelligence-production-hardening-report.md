# MARKET INTELLIGENCE PRODUCTION HARDENING v2 COMPLETE

Status: local closeout verification complete; final GitHub Actions evidence pending

## 1. Final SHA

The final branch SHA will be recorded after the closeout documentation commit
and final GitHub Actions run.

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
identical evidence is idempotent. ORM guards and a PostgreSQL trigger reject
revision UPDATE/DELETE. The ledger has no parent foreign key, so deleting a
current price row cannot cascade-delete historical evidence.
Re-ingesting after an independently deleted current row continues at the
retained ledger's maximum revision plus one, avoiding a revision-zero collision.
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

Historical GitHub Actions run `33449989745` excluded one warm-up and measured 20
requests per family before the closeout fixture exercised full v2 hash
verification:

| Family | p50 ms | p95 ms | Worst ms |
| --- | ---: | ---: | ---: |
| Overview | 39.183 | 39.816 | 40.115 |
| Movers | 668.927 | 681.450 | 686.844 |
| ETFs | 67.638 | 70.996 | 75.763 |
| Sectors latest | 8.162 | 8.580 | 8.840 |
| Sectors history | 183.102 | 202.283 | 530.799 |
| Sectors health | 21.065 | 21.712 | 21.789 |

These are comparison-only in-process production-router/PostgreSQL measurements. They include
routing, dependency overrides, ORM materialization, application logic, SQL, and
serialization, but exclude network/TLS/Uvicorn and real authentication.

## 17. SLO

All six read families enforce a common unrounded p95 below 1000 ms in the
dedicated PostgreSQL 16 job. The threshold was selected after the first
20-sample baseline and leaves conservative shared-runner headroom without hiding
a full-second regression. Run `33449989745` passed the earlier path; final-head
full-provenance evidence is pending the closeout push and is authoritative.

## 18. Query optimizations

The slowest observed query was the Movers price load. `EXPLAIN (ANALYZE,
BUFFERS, FORMAT JSON)` used existing index `uix_symbol_date`, returned 21,500
rows, completed in 7.428 ms in the enforced run, hit 1,601 shared buffers, and
used no shared reads or temporary I/O. Evidence did not justify another index,
so none was added. Sector history's 182 SELECTs/request is a documented N+1
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
opt-in so normal pushes do not duplicate provider pressure.

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
selection passed 458 tests, including all 41 Yahoo ingestion tests and every
review-remediation path. It used an in-process `resource` module shim because
that Unix-only stdlib module is absent on Windows; no production code, skip, or
xfail was added.

## 26. Frontend tests

The final Windows closeout run passed all 37 tests in the 11 Market Intelligence
frontend test files. ESLint completed with zero errors and four unrelated
existing warnings. The native-Windows
full run passed 650 and reported nine unrelated failures: a deterministic legacy
`D:\\D:\\...` fixture-path defect and contended `App.static` timeouts; the latter
file passed 9/9 alone. Linux PR CI previously passed the complete frontend test
and Playwright smoke jobs. Final PR evidence will be recorded after the closeout
push.

## 27. Integration tests

Run `33449989745` passed 11 PostgreSQL migration/publication/concurrency/API
tests, real Redis connectivity, 52 service-independent tests, the real Celery
worker/idempotent rerun, the live Yahoo contract, the enforced PostgreSQL SLO,
frontend tests, lint, and production build. The final Windows closeout run also
passed all 53 currently selected service-independent integration tests (16
service-backed/live tests deselected by marker). Final Task 8/9 hosted evidence
will be recorded after the closeout push.

## 28. GitHub Actions

The Market Intelligence workflow supplies PostgreSQL 16, Redis 7, real Alembic
migrations, transaction/concurrency tests, deterministic tests, a dedicated
measured SLO job, optional real Celery/Yahoo validation, frontend checks, and
failure-safe artifacts. Run `33449989745` is fully green. Final workflow IDs will
be recorded after closeout.

## 29. PR CI

General PR CI at `f98e4a03` passed quality gates, frontend lint/test/Playwright,
assistant compose smoke, and three of four backend shards. The one failure was a
stale Yahoo test that omitted the now-required explicit provider provenance; no
production defect existed. Commit `b46da482` updates that test and proves
append-only legacy plus Yahoo revisions. Final all-green PR CI evidence is
pending the closeout push.

## 30. Production build

The final Windows build compiled 2,515 modules successfully in 1 minute 32
seconds. Final Linux PR build evidence will be recorded after closeout.

## 31. Security

No credential, token, device identifier, or machine-local state is included.
The final local scan examined 81 changed/untracked files and found zero files
matching high-confidence private-key, AWS access-key, GitHub token, Slack token,
or JWT patterns. `git diff --check` reported no whitespace error. No production
permission is granted to the Yahoo canary, and checkout credentials are not
persisted there.

## 32. Dependency assessment

`npm audit --json` reports the unchanged baseline of 21 package nodes: 1
critical, 16 high, 3 moderate, and 1 low. The critical Vitest issue is dev-only;
`npm audit --omit=dev` reports zero critical, six high, two moderate. Direct
runtime follow-ups are Axios and React Router; remaining production-graph
findings include Node-only Axios transitive paths and Recharts/Lodash. Full
reachability and isolated upgrade recommendations are in
`docs/dependency-security-assessment.md`. No `npm audit fix` was run. `pip check`
reports no broken requirements, and this hardening range changes no dependency
manifest or lockfile.

## 33. Files changed

Relative to hardening base `6d75e8a4`, the closeout range changes 81 files with
10,769 insertions and 491 deletions. Scope is limited to additive
migrations/models, existing Market Intelligence provider,
pipeline/read/cache/API/task seams, targeted React disclosure, CI workflows,
fixtures/tests, and documentation. It adds no new application, authentication,
market universe, page, or external dependency.

## 34. Commits

The 26-commit hardening series begins at `07fed279` and uses small design,
schema, normalization, provider, observability, health, cache, SLO, regression,
UI-quality, review-fix, and closeout-documentation commits. The exact ordered
list remains available from `git log --oneline 6d75e8a4..HEAD`.

## 35. Known limitations

- Yahoo remains the sole Phase 1 market-data provider; the canary detects but
  does not eliminate provider outage or policy risk.
- Yahoo adjusted close is a provider-adjusted total-return proxy, not an
  independently reconciled pure-price or official total-return index.
- The scheduled read-only canary needs its first post-commit scheduled/manual
  execution.
- Sector history performs 182 SELECTs/request and should be batched even though
  it meets the initial SLO.
- The SLO excludes network, TLS, reverse proxy, Uvicorn scheduling, and real auth
  latency.
- The existing Windows fixture-path and contended full-frontend-suite failures
  remain platform baseline items; Linux CI is authoritative for final
  no-regression evidence.
- The 21 npm advisories remain recorded technical debt; this phase intentionally
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
