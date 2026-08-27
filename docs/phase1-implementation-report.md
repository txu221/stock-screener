# Market Intelligence Engine — Phase 1 Implementation Report

Status: implementation complete; final verification recorded below

Implementation date: 2026-08-26; final verification: 2026-08-27 (America/New_York)

Baseline: `e65d1fc67db4b468471376aa29741fdce3759ffc`

Branch: `feat/market-intelligence-engine`

## 1. Preflight and Git state

The work started from the requested Phase 0 baseline on the existing feature branch. Preflight checked `git status`, unstaged/staged diffs, recent log, and remotes. Phase 0 documents were present and committed before production implementation. `docs/upstream-research.md` now explicitly records why research stopped at seven repositories: the candidates had reached selection saturation across the relevant architecture/provider/licensing patterns, and the deviation did not change the `xang1234/stock-screener` primary-base conclusion.

`backend/.local/state/gh/device-id` was inspected as an untracked 36-byte GitHub CLI machine/device-state artifact. It was never added to the index or a commit and was removed from the working tree after generated test runs. No credential, token, device identifier, or other machine-local state is part of Phase 1 history. No commit was pushed; no PR or merge was created.

## 2. Existing architecture assessment

Phase 1 used the approved hybrid extension:

- the existing daily-snapshot concept can carry the orchestration role, while typed Market Intelligence tables carry the new lineage and read model;
- the legacy price row is not widened because it lacks the strict, explainable adjustment evidence required here; an additive canonical table is tied to the same `FeatureRun` lifecycle instead;
- Data Health reuses durable run/audit and Operations-style status semantics rather than calculating health in the UI;
- publication reuses `FeatureRun`, `FeatureRunPointer`, `SqlUnitOfWork`, run transitions, and a named atomic latest pointer;
- the existing Celery app and US daily/bootstrap pipeline now dispatch the sector computation; no second scheduler or run lifecycle was added;
- the existing FastAPI v1 router was extended; no separate application was created;
- the existing non-atomic static writer was deliberately not used, so Phase 1 has one API publication contract and one pointer semantics;
- existing Market RS/RRG algorithms were left unchanged because Phase 1 relative return has a different definition.

## 3. Scope delivered

The vertical slice is fixed to 12 ETFs:

```text
Benchmark: SPY
Sectors:   XLC XLY XLP XLE XLF XLV XLI XLB XLRE XLK XLU
```

It delivers provider ingestion, strict canonical validation, raw/adjustment lineage, durable quarantine, a completed-session historical window, deterministic metrics, dense ranks, prior-published rank change, a daily candidate/read model, idempotent persistence, atomic monotonic publication, API endpoints, and Data Health. It does not add stocks, themes, industries, news, AI, options/fund/institutional flow, an opportunity score, frontend work, or a static JSON pipeline.

## 4. Provider and price basis

Primary provider: existing Yahoo `BulkDataFetcher`, requested once for the fixed universe with `period="6mo"`.

Fallback policy: none in Phase 1. Request timeout, authentication, network, malformed batch, or unusable response becomes one request-level `FAILED` attempt; it is not expanded into 12 fake row rejections.

Price basis: `yahoo_adjusted_ohlc_provider_volume`.

```text
adjustment_factor = Adj Close / raw Close
adjusted OHLC     = raw OHLC * adjustment_factor
adjusted Close    = provider Adj Close
volume            = provider-reported volume, unchanged
```

Lineage persists raw OHLC, adjusted close, factor, adjusted OHLC, raw/provider volume, provider and provider symbol, raw/normalized date, source/ingestion timestamps, price basis, and normalization version. The factor must be finite and positive; no guess or repair is allowed.

## 5. Validation and invalid-data behavior

Canonical rows require:

- symbol in the exact 12-symbol universe;
- a valid completed-session trading date within the bounded reference window;
- present, numeric, finite OHLCV and provider adjusted close;
- raw `open/high/low/close > 0`, adjusted close `> 0`, and volume `>= 0`;
- finite positive adjustment factor;
- `high >= low`, `high >= open`, `high >= close`, `low <= open`, and `low <= close`;
- no duplicate `(symbol, trading_date)`.

Stable rejection codes are `UNEXPECTED_SYMBOL`, `MISSING_REQUIRED_FIELD`, `INVALID_TRADING_DATE`, `NON_FINITE_VALUE`, `NON_POSITIVE_PRICE`, `INVALID_ADJUSTED_CLOSE`, `INVALID_ADJUSTMENT_FACTOR`, `INVALID_OHLC_RELATION`, `NEGATIVE_VOLUME`, and `DUPLICATE_BAR`. A rejection records run, provider, provider symbol, known symbol/date, code, reason, raw evidence, and ingestion time. Invalid rows are excluded from canonical bars and affect completeness. No `abs(volume)`, zero clipping, forward fill, high/low swap, non-finite continuation, or silent pre-validation crop exists.

## 6. Historical and metric semantics

The calendar adapter supplies the full bounded window that covers the provider request and verifies at least 90 completed US sessions ending exactly at `as_of`. All returned rows within that window are validated/preserved; metric functions use exact session anchors and never calendar-day subtraction.

Using adjusted close:

```text
return_N = price_today / price_N_completed_sessions_ago - 1
relative_return_vs_spy_N = sector_return_N - SPY_return_N
RVOL20 = today_volume / mean(previous 20 completed-session volumes)
MFM = (2*Close - High - Low) / (High - Low)
CMF_N_proxy = sum(MFM * Volume) / sum(Volume)
```

Returns and relative returns use 1/5/20/60 sessions. RVOL excludes today; zero mean is unavailable. MFM uses uniformly adjusted OHLC and defines a zero-range bar as `0`. CMF uses provider volume and returns unavailable for a zero total-volume denominator. Insufficient/missing/duplicate/non-finite history returns unavailable, never `0%` or infinity.

The one-day field is `flow_pressure_1d_proxy`; windows are `cmf_5d_proxy`, `cmf_20d_proxy`, and `cmf_60d_proxy`. API metadata says `derived_proxy` / `ohlcv_derived_proxy`. There is no Net Dollar Flow or claim of real/institutional/smart-money inflow.

## 7. Ranking and change detection

The six transparent ranking dimensions are `return_1d`, `relative_return_vs_spy_5d`, `relative_return_vs_spy_20d`, `relative_return_vs_spy_60d`, `rvol20`, and `cmf_20d_proxy`. They use descending dense rank over the 11 sector ETFs only. Tied values share the same rank; symbol is only a stable output sort key.

Previous rank comes from the preceding successfully published trading-session snapshot of the same metric version. It ignores weekends, calendar gaps, and `PARTIAL`/`FAILED` attempts. `rank_change = previous_rank - current_rank`, so `7 -> 2` is `+5 / IMPROVED`. Directions are `IMPROVED`, `DECLINED`, `UNCHANGED`, and `NOT_AVAILABLE`. No predictive rotation label was forced into Phase 1.

## 8. Ingestion states and publication

- `SUCCEEDED`: 12/12 symbols are received/valid through the required history and metrics, all 12 snapshot rows exist, and all 11 sectors have all ranks. It may publish.
- `PARTIAL`: the request succeeded and at least one symbol is usable, but any missing/rejected/provider-failed/insufficient/metric-incomplete/universe-incomplete condition remains. It is persisted and quarantined but cannot move latest.
- `FAILED`: request-level failure, zero usable symbols, or no usable candidate. It is persisted as failed and cannot move latest.

The states are mutually exclusive and exhaustive in the candidate builder. `/latest` follows the last complete pointer; `/health` reads the latest attempt independently. A new partial/failed attempt therefore never hides the previous stable snapshot.

Successful publication is also monotonic by trading date. PostgreSQL takes a stable SHA-256-derived transaction advisory lock before locking/reading the named pointer row, so even the first publication with no existing pointer is serialized. An equal/newer successful revision may move it, while an older successful backfill is retained as published history without moving `/latest` backward. Same-session `published_at` is assigned after the lock and ordered strictly after the current revision, matching history's `(published_at, run_id)` winner policy. A true PostgreSQL two-transaction execution test remains blocked by the local environment and is called out separately.

## 9. Transaction boundaries

```text
Boundary A — no database writes
  completed-session lookup
  -> one Yahoo batch request
  -> raw mapping / strict validation
  -> canonical + rejection candidates
  -> metrics / input hash / attempt identity

Boundary B — one existing SqlUnitOfWork transaction
  exact-idempotency lookup
  -> prior successfully published snapshot lookup
  -> candidate/ranks/status
  -> FeatureRun RUNNING row
  -> audit + canonical bars + rejections + snapshots
  -> final FAILED / QUARANTINED / PUBLISHED transition
  -> advisory-lock + row-lock/compare/update named pointer when eligible
  -> one commit

Commit failure
  -> UoW rollback removes run, audit, canonical, rejection,
     snapshot, final status, and pointer effects together
```

This prevents a visible snapshot without audit, a moved pointer without committed snapshots, or canonical evidence committed while the run remains `RUNNING`. Provider I/O is intentionally outside the final transaction. Request failures are still persisted as independent attempts.

## 10. Idempotency

Successful and row-level-invalid inputs use a SHA-256 key over pipeline, `as_of`, fixed-universe hash, content input hash, normalization version, and metric version. Identical input reuses the logical run and cannot add duplicate snapshot rows. Concurrent insertion of the same key is translated to a domain conflict and resolves by reading the committed winner.

Request-level failures deliberately add an attempt identity to their key. Two identical timeouts must create two `FAILED` audits so latest-attempt time/provider health advances and transient retries are not suppressed.

## 11. Data model and migration

Migration `20260826_0031` is additive and depends on `20260823_0030`. It adds:

- `market_intelligence_run_audits`: attempt identity, hashes, status, provider/request health, versions/basis, target session, counters, missing/provider failures, freshness, and timestamps;
- `market_intelligence_canonical_bars`: full raw-to-adjusted evidence per run/symbol/date;
- `market_intelligence_rejections`: structured quarantine evidence;
- `market_intelligence_sector_snapshots`: daily metrics, rank maps, provider/freshness/version/quality metadata.

All four tables reference existing `feature_runs`; publication continues to use existing `feature_run_pointers`. The downgrade removes only the four additive tables and their indexes in dependency-safe order. Operational rollback is: stop the new scheduled task, repoint/retain the last stable existing pointer if necessary, downgrade one migration only if Phase 1 data removal is explicitly accepted, and revert the Phase 1 commits. No existing table or column is destructively rewritten.

## 12. Snapshot and API contracts

Each snapshot row contains trading date, symbol, benchmark/sector asset type, sector name, all returns, all relative returns, RVOL20, four pressure proxies, current/previous/change/direction ranks, provider, source freshness, price basis, metric version, calculation time, and data-quality status.

Metric version: `market_intelligence_v1`.

Normalization version: `market_intelligence_adjusted_ohlcv_v1`.

Endpoints:

- `GET /api/v1/market-intelligence/sectors/latest`
- `GET /api/v1/market-intelligence/sectors/history`
- `GET /api/v1/market-intelligence/sectors/health`

`latest` includes `as_of`, `published_at`, `run_id`, provider/status, versions, price basis, proxy semantics, calculation timestamp, explicit universe, benchmark, sectors, per-item metrics/ranks, freshness, and quality. `history` filters by trading date, symbol, and metric version and keeps the newest published revision per session. `health` returns persisted latest-attempt and latest-published bundles independently.

## 13. Data Health output

Run audit counters are `expected_symbols`, `symbols_received`, `valid_bars`, `rejected_bars`, `missing_symbols`, `duplicate_rows`, `invalid_volume`, `invalid_ohlc`, `usable_symbols`, and `snapshot_rows`. Data Health also exposes provider status/failures, request failure, target/current/calculation/ingestion/publish timestamps, source freshness, missing/stale symbols, run/lifecycle status, versions/basis, last successful run, last complete published date, and whether the latest attempt moved the stable pointer. The API does not reconstruct these counters from UI data.

## 14. Affected components and commits

Affected components are the Market Intelligence domain, Yahoo adapter/session source, typed SQLAlchemy models/repository/UoW wiring, Alembic migration, use case, Celery task and US daily/bootstrap composition, FastAPI v1 router/schemas, deterministic fixtures/tests, and documentation. Existing scan, static, frontend, authentication, RRG, Market RS, theme, and broader universe paths were not rewritten.

Logical commits before final documentation:

```text
03b03597 docs: record market intelligence phase 1 design
8f057b28 docs: add market intelligence phase 1 plan
15910720 feat: define sector intelligence domain contracts
8c721fa4 feat: add strict sector bar validation
ebfe52cf feat: add deterministic sector intelligence metrics
352498dd feat: add sector ranking and snapshot assembly
830d217e feat: persist sector intelligence snapshots
52646ff8 feat: atomically publish sector intelligence runs
428c2e5f feat: wire daily sector intelligence ingestion
62a789ed feat: expose sector intelligence API
6f8037b7 fix: preserve sector intelligence architecture invariants
08393243 fix: harden sector publication and audit semantics
```

The final review repair commit covers independent failure-attempt audits, full bounded-window validation, and monotonic latest publication.

## 15. Tests and deterministic fixture

The golden raw-like fixture covers SPY plus sector behavior with rising/falling paths, differentiated volume/pressure, and exact adjustment lineage. It flows through adapter-like rows, validation, canonical bars, metrics, ranking, snapshots, persistence, publication, and API contracts. Additional fixtures/tests inject negative volume, all OHLC relation failures, non-finite values, duplicates, unexpected/missing symbols, zero ranges/volumes, insufficient/missing sessions, ties, prior-rank gaps, request outages, 12/12, 11/12, 1/12, 0/12, reruns, rollback, partial pointer preservation, out-of-order backfill, migration, health, and API history/version filters. No Phase 1 deterministic test accesses a live provider.

Final deterministic results:

- Phase 1 exact suite: `123 passed, 2 warnings`;
- focused adjacent feature-run/UoW/Celery/price/Market-RS suites: `123 passed, 5 warnings`;
- full backend source-neutral Windows diagnostic: `13 failed, 6122 passed, 3 skipped, 1025 warnings in 904.11s`; the exact 13-failure set matches Phase 0;
- unmodified backend command: `42 errors during collection` from the known Unix-only `resource` import on Windows;
- frontend Vitest: `598 passed, 8 failed` across `89` files, matching Phase 0 exactly.

No new Phase 1 test is skipped, xfailed, disabled, or live-network dependent.

## 16. Known baseline failures and blocked integrations

The full source-neutral backend comparison retains exactly 13 Phase 0 failures: 11 Windows/Unix assumption failures (two `/tmp` path cases, three Unix worker-wrapper cases, one POSIX static-artifact separator case, one fake `gh` launcher case, and four static-workflow/fake-`gh` cases) plus two deterministic baseline failures (feature-run list tie ordering and scan-throughput zero tick). It retains three documented skips. The frontend retains the eight `App.static` failures and one Windows doubled-drive-path suite condition; its aggregate remains the documented `598 passed, 8 failed`. Phase 1 introduced zero new baseline failures.

Because this machine has no Docker, WSL, PostgreSQL, Redis, or production worker stack, the following are explicitly `BLOCKED`, not silently passed:

- executing upgrade/downgrade against real PostgreSQL;
- executing real PostgreSQL transaction isolation, same-key race, advisory/pointer locks, concurrent initial pointer creation, and rollback visibility (the production code path is implemented but cannot be exercised here);
- Redis broker/cache/lock degradation in a running stack;
- a real Celery worker/beat execution of the daily pipeline;
- live Yahoo contract/drift and end-to-end production startup.

No infrastructure or system setting was installed or changed to bypass these limits.

## 17. Lint, dependencies, and security

- Python `compileall`: passed.
- `pip check`: `No broken requirements found`.
- Frontend lint: zero errors and four pre-existing warnings.
- Dependency manifests/lockfiles: no diff from the Phase 0 baseline; no dependency added.
- `git diff --check`: passed.
- Production-diff secret scan: no secret-like assignment or device state found.
- Generated `backend/.local/state/gh/device-id`: untracked local device state, removed and not committed.
- `npm audit`: 21 existing advisories (1 critical, 16 high, 3 moderate, 1 low); no `npm audit fix` or dependency churn was performed.

## 18. Final code review findings

The final review checked all requested invariants. There is no big-bang refactor or duplicate data pipeline; Phase 1 uses typed extensions at existing seams. Canonical/audit/snapshot/status/pointer effects share one final transaction. Request failures and row rejections remain separate. Negative volume has no coercion path. Full raw/adjustment lineage is durable. Adjusted basis is consistent across returns, relative return, and CMF. The 60-day anchor is exactly 60 sessions. RVOL excludes today. CMF handles zero range and denominator. Missing history is unavailable, not zero. Dense ties and rank-change directions are deterministic. Previous ranks use only a prior successful published session. Versions are persisted. Same content reruns reuse one logical run. Partial/failed attempts cannot repoint. Latest publication is monotonic. Health derives from persisted audit truth. No dependency, frontend, misleading flow language, secret, or scope expansion was introduced.

Review found and fixed six material defects before completion:

1. an infrastructure exception had crossed into the use-case layer; it is now translated to a domain idempotency conflict;
2. the compute task was not included in runtime bootstrap; US bootstrap/daily composition now includes it and rejects non-US scheduling;
3. repeated identical request failures were incorrectly deduplicated; each now creates an independent failed audit;
4. provider rows before the exact 90-session metric tail were silently cropped; the bounded provider window is retained and every returned row is validated before metrics select trailing anchors;
5. an older backfill, first concurrent publication, or same-session revision could produce an inconsistent latest winner; advisory/row locking and the monotonic revision policy now close those paths;
6. extra evidence bars could hide a missing date inside the required trailing 90 sessions; publishability now requires exact set coverage of all 90 dates.

SQLite tests prove sequential semantics and rollback; true PostgreSQL concurrency remains the explicit environment-blocked execution check above. Independent final re-review reported Critical `0`, Important `0`, no actionable Minor, and assessed the branch `Ready`.

## 19. Recommended Phase 2 — not started

First run the migration, Celery, pointer-lock/race, and rollback checks in a disposable PostgreSQL/Redis environment and observe several real completed sessions with Data Health. Only after that evidence should Phase 2 consider a broader ETF universe and a separately specified transparent score. Do not expand to S&P 500/full-market movers, themes, industries, news, AI, options/institutional flow, or frontend redesign until the slice is operationally proven.

Phase 2 has not been started.
