# Upstream Baseline Audit

Audit date: 2026-08-25 (America/New_York)

Workspace: `D:\文档\ChatGPT\美股资金走向`

## Executive verdict

The selected upstream was imported with Git history and attribution intact. Its frontend installs, lints, builds, serves, and passes the Playwright operator-path smoke test. The repository has a very large, mostly healthy deterministic backend suite: a source-neutral Windows diagnostic run passed 5,991 of 6,007 tests.

The full live stack is **not normally runnable in this Windows workspace**. The upstream deployment path assumes Linux/Docker plus PostgreSQL and Redis. Docker, WSL, PostgreSQL, and Redis are not available here, and the backend imports the Unix-only Python `resource` module unconditionally. These are pre-existing upstream/environment constraints; Phase 0 did not patch around them in product source.

Two deterministic backend test failures remain after platform-specific failures are removed: non-deterministic feature-run ordering when creation timestamps tie, and missing progress throughput when an extremely fast scan observes zero elapsed clock ticks. The full frontend Vitest command also fails on a Windows path-construction bug, although its eight companion static-App failures pass when isolated.

Baseline status: **suitable as the architectural base, not clean on native Windows, and not ready to call fully green without a Linux/PostgreSQL run or the documented fixes.**

## 1. Git provenance and import

| Item | Value |
|---|---|
| Primary upstream | `https://github.com/xang1234/stock-screener.git` |
| Audited/imported commit | `e65d1fc67db4b468471376aa29741fdce3759ffc` |
| Local branch | `feat/market-intelligence-engine` |
| Tracking branch | `upstream/main` |
| Remote name | `upstream` |
| License | Apache-2.0 |
| History | Full upstream history fetched; no squash or source-copy import |

Verification commands:

```powershell
git branch --show-current
git rev-parse HEAD
git log --oneline -5
git remote -v
```

At the end of baseline execution, product source, lockfiles, dependency manifests, and UI files remained unchanged. Only Phase 0 documents are new.

## 2. Local environment

| Component | Observed baseline |
|---|---|
| OS | Windows 10 Home China, x64, reported build 26200 |
| Python | 3.12.13, bundled workspace runtime |
| Backend virtual environment | `backend\venv` |
| pip | 25.0.1 inside the created venv |
| Node.js | v24.18.0 |
| npm | 11.16.0 |
| Git | 2.55.0.windows.1 |
| Docker | Not installed/on `PATH` |
| WSL | Not installed/configured; `wsl.exe --status` requests WSL installation |
| PostgreSQL | No local service and no listener on port 5432 |
| Redis | Not provisioned for this workspace |

Upstream CI uses Ubuntu and Python 3.11 for the backend, and Ubuntu and Node 22 for the frontend. The local Python 3.12/Node 24/Windows combination is therefore useful portability evidence but is not identical to the supported CI environment.

## 3. Dependency installation

### Frontend

Command:

```powershell
cd frontend
npm ci
```

Result: success; 576 packages installed. npm reported the existing ESLint peer-range warning and an `esbuild` install-script approval warning. No lockfile was changed.

`npm audit --json` reported 21 advisories:

| Severity | Count |
|---|---:|
| Critical | 1 |
| High | 16 |
| Moderate | 3 |
| Low | 1 |

The critical advisory is in direct dev dependency `vitest`. Direct dependencies with reported high advisories include `axios`, `react-router-dom`, and `vite`; several transitive packages are also affected. Phase 0 did not auto-upgrade them because that could change behavior and the lockfile. They require a separate tested dependency-maintenance change.

Playwright's package was installed by `npm ci`, but the Chromium binary is a separate test prerequisite. The first smoke run correctly failed with “Executable doesn't exist.” The following standard command installed only the required Chromium/browser shell outside the repository:

```powershell
npx playwright install chromium
```

### Backend

Virtual environment:

```powershell
python -m venv backend\venv
```

The upstream's unconstrained `litellm>=1.50.0` conflicts with its pinned `pydantic==2.5.3` for current LiteLLM releases. A normal install caused extensive resolver backtracking and reached a LiteLLM source distribution that attempted a Maturin/Rust build on Windows. Current release wheels and dependency requirements make the result OS-sensitive.

A dry run proved that requiring a binary LiteLLM distribution lets pip select the compatible `litellm==1.83.0`. Dependencies were then installed without modifying upstream manifests:

```powershell
backend\venv\Scripts\python.exe -m pip install --only-binary=litellm `
  -r backend\requirements-runtime.txt `
  -r backend\requirements-test.txt
```

Resolved key versions:

| Package | Version |
|---|---:|
| FastAPI | 0.109.0 |
| Pydantic | 2.5.3 |
| LiteLLM | 1.83.0 |
| SQLAlchemy | 2.0.25 |
| pandas | 2.2.0 |

Integrity command and result:

```powershell
backend\venv\Scripts\python.exe -m pip check
```

Result: `No broken requirements found.` pandas emitted its upstream warning that pyarrow may become required in pandas 3.0.

Risk: because LiteLLM is not upper-bounded or locked, a clean install is not reproducible across time and platforms. Pinning/lock generation should be a dedicated maintenance task, not hidden inside Market Intelligence feature work.

## 4. Generated-contract check

Command:

```powershell
backend\venv\Scripts\python.exe scripts\generate_scan_filter_contract.py --check
```

Result: pass, exit code 0, no generated drift.

## 5. Backend tests

### Unmodified upstream command

Command:

```powershell
cd backend
.\venv\Scripts\python.exe -m pytest tests\unit -m "not live_service and not load" -q
```

Result: failed during collection after discovering 5,504 items and reporting 42 collection errors. Every displayed error has the same root cause:

```text
app/services/runtime_diagnostics.py:7
    import resource
ModuleNotFoundError: No module named 'resource'
```

Python's `resource` module is Unix-only. The import is unconditional even though its only use is process RSS diagnostics. This also blocks the native FastAPI import path on Windows.

### Source-neutral diagnostic run

To learn whether the rest of the suite was healthy, a second process injected a minimal in-memory module named `resource`, with `RUSAGE_SELF=0` and `getrusage(...).ru_maxrss=0`. No repository file or installed package was changed. This result is diagnostic and does not replace the failed unmodified command.

Result:

```text
13 failed, 5991 passed, 3 skipped, 1025 warnings in 774.71s (0:12:54)
```

The 13 failures divide into two groups.

#### Windows/Unix execution assumptions: 11 failures

- 2 tests compare Windows-rendered `\tmp\...` paths with hard-coded `/tmp/...` strings.
- 3 worker-config tests execute a Unix shell wrapper directly and receive `WinError 193`.
- 1 static-artifact validation test expects POSIX separators inside an error message.
- 1 fake-`gh` launcher test attempts to execute a Unix launcher directly and receives `WinError 193`.
- 4 static fallback-workflow tests fail because the fake Unix `gh` launcher is not used on Windows; the subprocess reaches the real CLI without the test's authentication environment.

These failures identify unsupported test portability, not failed market calculations.

#### Stable deterministic failures: 2 failures

Both were rerun alone and failed again:

```text
tests/unit/use_cases/test_list_feature_runs.py::
  TestListFeatureRunsUseCase::test_multiple_runs_ordered_by_created_at_desc

tests/unit/use_cases/test_run_bulk_scan.py::
  TestProgressEvents::test_progress_tracks_passed_and_failed
```

Root-cause audit:

1. The fake feature-run repository creates runs with `datetime.now()` and sorts only by `created_at`. On Windows, two immediate calls can have the same observed timestamp, so insertion order survives and the older run appears first. Production ordering should also have a stable secondary key such as ID.
2. Bulk-scan throughput uses `time.monotonic() - start_time`. A very fast test can observe zero elapsed ticks on Windows, causing `throughput=None`. Clock injection or an explicit zero-duration policy is needed for a deterministic test and API contract.

Warnings are predominantly existing Pydantic v2 class-config deprecations, `datetime.utcnow()` deprecations, and the pandas/pyarrow notice.

### CI interpretation

The upstream CI shards the unit suite four ways on Ubuntu/Python 3.11 and runs detector correctness, temporal integrity, integration, performance, and golden gates separately. Recent upstream CI was green at research time. The local result does not invalidate that Linux baseline, but it proves that native Windows is not currently supported and surfaces two timing/order assumptions that can be hidden by Linux clock behavior.

## 6. Frontend checks

### Lint

Command:

```powershell
cd frontend
npm run lint
```

Result: pass, exit code 0; 0 errors and 4 warnings:

- one unused `formatLargeNumber` function in `StockDetails.jsx`;
- three `react-refresh/only-export-components` warnings in market context modules.

### Unit/component tests

Command:

```powershell
npm run test:run
```

Result:

```text
Test Files  2 failed | 87 passed (89)
Tests       8 failed | 598 passed (606)
Duration    342.31s
```

One failed suite is a real Windows path bug in `src/features/scan/filterExpression.test.js`: conversion of a Vite `/@fs/` URL produces `D:\D:\文档\...\contracts\scan_filter_truth_table.json` and raises `ENOENT`.

The other eight failures were all in `src/App.static.test.jsx`, following a 15-second timeout/empty DOM in the full concurrent run. The file was immediately rerun alone without changes:

```text
Test Files  1 passed (1)
Tests       9 passed (9)
Duration    18.28s
```

This makes the static-App failures order/load-sensitive or cascading rather than consistently broken assertions. The complete suite is still red and must not be reported as passing.

### Production build

Command:

```powershell
npm run build
```

Result: pass; 2,497 modules transformed and production assets emitted in 1m 55s.

### Playwright smoke

Command after installing Chromium:

```powershell
npm run test:smoke
```

Result: pass, 1/1 in 31.8s. The test covers the mocked single-tenant operator path: login, assistant, watchlist action, scan, theme review, auth expiry, and relogin.

## 7. Startup and HTTP smoke

### Frontend development server

Port 5173 was already in use by an unrelated local process, which was left untouched. The same native command was started on 5174:

```powershell
npm run dev -- --host 127.0.0.1 --port 5174 --strictPort
```

Result: Vite ready in 430ms. `GET http://127.0.0.1:5174/` returned HTTP 200, `text/html`, 630 bytes, and an `id="root"` mount element. The browser then logged expected proxy failures for `/api/v1/...` because the backend was not running. The server was terminated cleanly with Ctrl-C.

### Backend native command

Documented native command:

```powershell
cd backend
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Observed startup gates:

1. Without configuration, Settings fail fast because `DATABASE_URL` is required.
2. With a syntactically valid PostgreSQL URL, import fails at the Unix-only `resource` module.
3. A diagnostic run with the in-memory `resource` shim and the repository's test-only SQLite switch reaches Alembic, then fails because the PostgreSQL baseline migration emits `DEFAULT now()`, which SQLite rejects.

No PostgreSQL service is installed/listening locally. Docker is unavailable, so the recommended Docker Compose stack (PostgreSQL, Redis, backend, workers, and frontend) could not be started. Consequently `/livez` and `/readyz` could not be probed in a normal live process.

Interpretation: the frontend and mocked operator flow run locally; the full application requires its documented Linux/Docker/PostgreSQL/Redis environment or deliberate Windows portability work.

## 8. Existing functional baseline

The imported application already provides:

- 12-market security/universe and exchange-calendar support;
- Minervini, CANSLIM, IPO, Volume Breakthrough, Setup Engine, and custom scans;
- 80+ scan filters, composite scoring, export, and stock-detail drill-down;
- daily market snapshot and key-market history;
- StockBee-style market breadth with historical windows;
- industry-group relative-strength rankings and RRG;
- user watchlists, sparklines, chart navigation, and stewardship context;
- point-in-time feature runs, run/universe/input hashes, and atomic publish pointers;
- provider routing, rate budgets, circuit breakers, caches, and retry/accounting paths;
- operations/jobs/telemetry and data freshness/validation surfaces;
- static GitHub Pages export and server-backed modes;
- feature-gated themes, assistant/MCP, and validation/backtest surfaces.

This is why it is a stronger base than the smaller donor repositories even though its operating footprint is heavier.

## 9. Baseline technical debt and risks

Highest-priority items for later work:

1. **Platform support:** backend and several tests assume Unix/Linux; `resource`, shell launchers, path assertions, and PostgreSQL-only migrations prevent native Windows startup.
2. **Dependency reproducibility:** unbounded LiteLLM plus pinned Pydantic causes time/OS-dependent resolution; frontend audit reports 21 advisories.
3. **Price lineage:** `StockPrice` has no provider, provider timestamp, ingestion run, adjustment revision, or raw-row reference.
4. **OHLCV validation:** missing/non-finite volume can become zero, negative volume is not rejected, and high/low relational invariants are not enforced.
5. **Determinism:** feature-run listing needs a stable tie-breaker; progress telemetry needs a zero-duration/clock policy.
6. **Test portability:** full backend and frontend suites are not green on Windows, despite the strong Linux CI signal.
7. **Operational complexity:** PostgreSQL, Redis, Celery queues, multi-market jobs, live/static parity, and legacy/new architecture layers increase maintenance cost.
8. **Provider/legal risk:** unofficial Yahoo/Finviz-style access is replaceable in code but still subject to reliability, entitlement, retention, and redistribution constraints.
9. **Frontend debt:** lint warnings, order-sensitive static tests, and the Windows contract-path bug should be fixed separately from product feature work.
10. **Deprecations:** Pydantic class config, naive `datetime.utcnow()`, and future pandas/pyarrow requirements will require planned upgrades.

## 10. Phase 0 change boundary

No feature, formula, API, migration, provider, UI, source test, dependency manifest, or lockfile was changed. Phase 0 added only:

- `docs/upstream-research.md`;
- `docs/baseline-audit.md`;
- `docs/market-intelligence-spec.md`;
- the Phase 0 execution plan under `docs/superpowers/plans/`.

No push or pull request was created. Phase 1 has not started.
