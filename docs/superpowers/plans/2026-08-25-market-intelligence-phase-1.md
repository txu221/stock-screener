# Market Intelligence Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a deterministic daily sector-intelligence vertical slice for exactly SPY plus 11 Sector SPDR ETFs, from strict Yahoo raw-row validation through atomic publication and versioned API/Data Health reads.

**Architecture:** Extend the existing feature-run Unit of Work and named-pointer publication lifecycle. Add a small Market Intelligence bounded context for strict raw/adjusted OHLCV lineage, pure calculations, ranks, typed persistence, and read models; never route Phase 1 rows through the permissive shared price normalizer and never create a parallel run or pointer system.

**Tech Stack:** Python 3.12, pandas already present, frozen dataclasses/enums, SQLAlchemy 2.0, Alembic, FastAPI/Pydantic v2, Celery, pytest, existing yfinance `BulkDataFetcher`, and the repository's SQLite test harness.

## Global Constraints

- Fixed universe: `SPY`, `XLC`, `XLY`, `XLP`, `XLE`, `XLF`, `XLV`, `XLI`, `XLB`, `XLRE`, `XLK`, `XLU`; never expand dynamically.
- Reuse `FeatureRun`, `FeatureRunPointer`, `SqlUnitOfWork`, and `SqlFeatureRunRepository.publish_atomically()`.
- Pointer key: `latest_market_intelligence_sectors_us`.
- Metric version: `market_intelligence_v1`.
- Normalization version: `market_intelligence_adjusted_ohlcv_v1`.
- Price basis: `yahoo_adjusted_ohlc_provider_volume`.
- Primary provider: existing Yahoo/yfinance bulk path; no fallback provider.
- Never use `_volume_or_zero`, `abs(volume)`, `max(volume, 0)`, forward-filled OHLCV, calendar-day lookbacks, or live-network tests.
- Row-level rejection and request-level provider failure are different contracts and different storage fields.
- Only `SUCCEEDED` may move the pointer; `PARTIAL` and `FAILED` never publish.
- Previous rank comes only from an earlier successfully published trading session with the same metric version.
- All flow fields use proxy terminology and `metric_semantics="ohlcv_derived_proxy"`; do not implement Net Dollar Flow.
- No frontend, static JSON publication, new dependency, dependency upgrade, infrastructure installation, skip, xfail, big-bang rewrite, push, PR, or merge.
- Known baseline failures remain baseline; Phase 1 gate is zero new failures.

---

## Goal

Prove the complete Provider -> Validation -> Canonical Data -> Rejection Audit -> Historical Window -> Metrics -> Ranking -> Change Detection -> Daily Snapshot -> Atomic Publish -> API -> Data Health chain on 12 ETFs.

## Scope

- Strict raw Yahoo daily-bar ingestion with raw and adjusted lineage.
- At least 90 completed US trading sessions ending at the requested session.
- Returns, relative returns versus SPY, RVOL20, close-location pressure, and CMF proxies.
- Descending dense ranks over only the 11 sectors.
- Previous-published-session rank changes.
- Durable candidate/audit data and pointer-selected complete snapshots.
- Latest, history, and health APIs.
- Fixture-only deterministic tests and existing daily-Celery registration for US.

## Non-goals

No stocks outside the 12-symbol universe, themes, industries, movers, ETF score, rotation prediction, news, AI, options, measured fund flow, institutional labels, signals, backtests, portfolio, alerts, authentication, frontend, static bundle, or dependency maintenance.

## Affected components

- New: `backend/app/domain/market_intelligence/` pure domain package.
- New: `backend/app/infra/db/models/market_intelligence.py` and repository.
- Modify: `backend/app/infra/db/uow.py`, model registries, and Alembic head.
- New: Yahoo adapter, orchestration runner, runtime wiring, and Celery task.
- New: Pydantic schemas and `/api/v1/market-intelligence` router.
- New: golden scenario fixture and focused unit/repository/API tests.
- Modify: `backend/app/celery_app.py`, `backend/app/api/v1/router.py`, and US daily pipeline signatures only.
- Modify: final specification and create `docs/phase1-implementation-report.md` after verification.

## Data model

- `market_intelligence_run_audits`: one-to-one extension of `FeatureRun`, unique idempotency key, ingestion/provider statuses, counters, failures, versions, timestamps, and freshness.
- `market_intelligence_canonical_bars`: `(run_id, symbol, trading_date)` primary key with raw OHLC, provider Adj Close, factor, adjusted OHLC, provider volume, provider/source/ingestion lineage.
- `market_intelligence_rejections`: stable code/reason plus provider, symbol/date identity, raw evidence, and timestamp.
- `market_intelligence_sector_snapshots`: `(run_id, symbol)` primary key with typed metrics and validated rank maps.

## Metric definitions

```text
adjustment_factor = raw_adjusted_close / raw_close
adjusted_price = raw_price * adjustment_factor
return_N = adjusted_close_today / adjusted_close_N_sessions_ago - 1
relative_return_vs_spy_N = sector_return_N - spy_return_N
rvol20 = today_volume / mean(previous 20 completed-session volumes)
MFM = (2 * adjusted_close - adjusted_high - adjusted_low)
      / (adjusted_high - adjusted_low)
cmf_N_proxy = sum(MFM * provider_volume, N) / sum(provider_volume, N)
rank_change = previous_rank - current_rank
```

Zero-range MFM is zero. Zero RVOL/CMF denominators and missing anchors are unavailable, never infinity or zero-by-default.

## Validation rules

Expected symbol and completed-session date; all required values present and finite; raw OHLC and Adj Close positive; volume non-negative; full OHLC relations; positive finite adjustment factor; no duplicate symbol/date. Duplicate groups reject all members. Stable codes are exactly those in the approved design spec.

## Snapshot model

SPY stores local metrics but no sector ranks. Each sector stores returns, four relative returns, RVOL20, four pressure proxies, rank maps, predecessor maps, changes, directions, lineage, freshness, metric version, calculation timestamp, and data-quality status.

## Publication semantics

The final Unit of Work persists run, audit, canonical rows, rejections, candidate snapshots, lifecycle transition, and optional pointer move in one commit. `SUCCEEDED -> published`; `PARTIAL -> completed -> quarantined`; `FAILED -> failed`. `/latest` reads only the named pointer; `/health` reads the latest attempt independently.

## Testing strategy

Follow RED-GREEN-REFACTOR per task. Pure tests precede SQL tests; SQL tests use SQLite with foreign keys; API tests use `httpx.ASGITransport`; provider tests patch `BulkDataFetcher`; Celery tests call `.run()` and never use a broker. Run focused tests after every change, then the Phase 1 suite, then baseline comparison.

## Migration strategy

Add one Alembic revision after `20260823_0030`, register four new ORM tables, add only their constraints/indexes/FKs, and leave every existing table unchanged.

## Rollback strategy

Stop task invocation, remove only the dedicated pointer if explicitly needed, downgrade the additive revision, and revert router/task/wiring. Existing price, Market RS, scan, and feature-store data remain untouched. PARTIAL/FAILED require no pointer rollback.

## Known environment constraints

Native Windows has no Docker, WSL, PostgreSQL, or Redis. Pure and SQLite-backed tests are executable. PostgreSQL transaction/migration and real Celery worker integration are reported BLOCKED; no system component is installed.

## Acceptance criteria

Every criterion in the approved design and user brief maps to Tasks 1-9 below. The last task must show Phase 1 tests all passing, no new baseline failure, no dependency drift, no credential/device state, a completed final review, and a final report; then stop.

---

### Task 1: Fixed universe, domain contracts, and golden scenario

**Files:**
- Create: `backend/app/domain/market_intelligence/__init__.py`
- Create: `backend/app/domain/market_intelligence/constants.py`
- Create: `backend/app/domain/market_intelligence/models.py`
- Create: `backend/tests/fixtures/market_intelligence/sector_golden_scenario.json`
- Create: `backend/tests/unit/market_intelligence/conftest.py`
- Create: `backend/tests/unit/market_intelligence/test_contracts.py`

**Interfaces:**
- Consumes: approved spec constants.
- Produces: `MARKET_INTELLIGENCE_UNIVERSE`, `SECTOR_SYMBOLS`, `UNIVERSE_HASH`, enums, `RawBar`, `CanonicalBar`, `BarRejection`, `ProviderBatchResult`, `SectorMetrics`, `RankRecord`, `SectorSnapshot`, `RunAudit`, and fixture builders used by every later task.

- [ ] **Step 1: Write contract tests that fail because the domain package does not exist**

```python
def test_phase1_universe_is_exact_and_spy_is_not_ranked():
    assert MARKET_INTELLIGENCE_UNIVERSE == (
        "SPY", "XLC", "XLY", "XLP", "XLE", "XLF",
        "XLV", "XLI", "XLB", "XLRE", "XLK", "XLU",
    )
    assert SECTOR_SYMBOLS == MARKET_INTELLIGENCE_UNIVERSE[1:]
    assert len(set(MARKET_INTELLIGENCE_UNIVERSE)) == 12


def test_versions_and_pointer_are_frozen():
    assert METRIC_VERSION == "market_intelligence_v1"
    assert NORMALIZATION_VERSION == "market_intelligence_adjusted_ohlcv_v1"
    assert PRICE_BASIS == "yahoo_adjusted_ohlc_provider_volume"
    assert LATEST_POINTER_KEY == "latest_market_intelligence_sectors_us"
```

- [ ] **Step 2: Run the RED test**

Run:

```powershell
cd backend
.\venv\Scripts\python.exe -m pytest tests\unit\market_intelligence\test_contracts.py -q
```

Expected: collection fails with `ModuleNotFoundError: app.domain.market_intelligence`.

- [ ] **Step 3: Implement exact constants and immutable value objects**

```python
class IngestionStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class RankDirection(str, Enum):
    IMPROVED = "IMPROVED"
    DECLINED = "DECLINED"
    UNCHANGED = "UNCHANGED"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class RejectionCode(str, Enum):
    UNEXPECTED_SYMBOL = "UNEXPECTED_SYMBOL"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    INVALID_TRADING_DATE = "INVALID_TRADING_DATE"
    NON_FINITE_VALUE = "NON_FINITE_VALUE"
    NON_POSITIVE_PRICE = "NON_POSITIVE_PRICE"
    INVALID_ADJUSTED_CLOSE = "INVALID_ADJUSTED_CLOSE"
    INVALID_ADJUSTMENT_FACTOR = "INVALID_ADJUSTMENT_FACTOR"
    INVALID_OHLC_RELATION = "INVALID_OHLC_RELATION"
    NEGATIVE_VOLUME = "NEGATIVE_VOLUME"
    DUPLICATE_BAR = "DUPLICATE_BAR"
```

Define frozen dataclasses with explicit fields from the Data model section. Validate symbol normalization, timezone-aware timestamps, enum values, finite numeric metrics, and JSON-safe rank-map keys in `__post_init__` without adding any data-repair behavior.

Use these exact field contracts:

```text
RawBar(provider, provider_symbol, symbol, raw_trading_date, trading_date,
       open, high, low, close, adjusted_close, volume, source_timestamp)
CanonicalBar(provider, provider_symbol, symbol, raw_trading_date, trading_date,
             raw_open, raw_high, raw_low, raw_close, provider_adjusted_close,
             adjustment_factor, adjusted_open, adjusted_high, adjusted_low,
             adjusted_close, provider_volume, source_timestamp,
             ingestion_timestamp, price_basis, normalization_version)
BarRejection(provider, provider_symbol, symbol, trading_date, code, reason,
             raw_evidence, ingestion_timestamp)
RequestFailure(code, message)
ProviderSymbolFailure(symbol, code, message)
ProviderBatchResult(provider, response_timestamp, rows, symbol_failures,
                    request_failure)
ValidationResult(canonical_bars, rejections, received_symbols)
SectorMetrics(return_1d, return_5d, return_20d, return_60d,
              relative_return_vs_spy_1d, relative_return_vs_spy_5d,
              relative_return_vs_spy_20d, relative_return_vs_spy_60d,
              rvol20, flow_pressure_1d_proxy, cmf_5d_proxy,
              cmf_20d_proxy, cmf_60d_proxy)
RankRecord(current_rank, previous_rank, rank_change, rank_direction)
SectorSnapshot(trading_date, symbol, asset_type, sector_name, metrics,
               ranks, provider, source_freshness, price_basis,
               metric_version, calculation_timestamp, data_quality_status)
RunAudit(idempotency_key, input_hash, ingestion_status, provider,
         provider_status, request_failure, metric_version,
         normalization_version, price_basis, counters, missing_symbols,
         provider_failures, target_session, provider_response_at,
         source_freshness, calculation_timestamp, ingestion_timestamp)
```

- [ ] **Step 4: Add a compact scenario template and fixture expansion helper**

The JSON contains fixed session dates, one parameter row per symbol (`start_close`, `daily_return`, `base_volume`), and named overrides for negative volume, invalid OHLC, zero range, zero denominator, duplicate, unexpected symbol, and missing session. `conftest.py` expands it into raw-like `RawBar` sequences; it does not return precomputed metrics.

```json
{
  "as_of": "2026-05-15",
  "session_count": 91,
  "symbols": {
    "SPY": {"start_close": 500.0, "daily_return": 0.001, "base_volume": 80000000},
    "XLK": {"start_close": 200.0, "daily_return": 0.002, "base_volume": 12000000},
    "XLU": {"start_close": 70.0, "daily_return": -0.0005, "base_volume": 9000000}
  },
  "default_sector": {"start_close": 100.0, "daily_return": 0.0005, "base_volume": 5000000}
}
```

- [ ] **Step 5: Run contract tests GREEN and commit**

```powershell
.\venv\Scripts\python.exe -m pytest tests\unit\market_intelligence\test_contracts.py -q
git add backend/app/domain/market_intelligence backend/tests/fixtures/market_intelligence backend/tests/unit/market_intelligence
git commit -m "feat: define sector intelligence domain contracts"
```

Expected: all Task 1 tests pass.

---

### Task 2: Strict canonical validation and adjustment lineage

**Files:**
- Create: `backend/app/domain/market_intelligence/validation.py`
- Create: `backend/tests/unit/market_intelligence/test_validation.py`
- Modify: `backend/tests/unit/market_intelligence/conftest.py`

**Interfaces:**
- Consumes: `RawBar`, `CanonicalBar`, `BarRejection`, `RejectionCode`, fixed universe, ordered completed sessions.
- Produces: `ValidationResult(canonical_bars, rejections, received_symbols)` and `validate_provider_rows(rows, expected_sessions, ingested_at)`.

- [ ] **Step 1: Write parameterized failing validation tests**

```python
@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"volume": -1}, RejectionCode.NEGATIVE_VOLUME),
        ({"high": 9, "low": 10}, RejectionCode.INVALID_OHLC_RELATION),
        ({"high": 10, "open": 11}, RejectionCode.INVALID_OHLC_RELATION),
        ({"high": 10, "close": 11}, RejectionCode.INVALID_OHLC_RELATION),
        ({"low": 10, "open": 9}, RejectionCode.INVALID_OHLC_RELATION),
        ({"low": 10, "close": 9}, RejectionCode.INVALID_OHLC_RELATION),
        ({"close": float("nan")}, RejectionCode.NON_FINITE_VALUE),
        ({"volume": float("inf")}, RejectionCode.NON_FINITE_VALUE),
        ({"close": 0}, RejectionCode.NON_POSITIVE_PRICE),
        ({"adjusted_close": 0}, RejectionCode.INVALID_ADJUSTED_CLOSE),
    ],
)
def test_invalid_row_is_rejected_without_coercion(valid_raw_bar, sessions, change, code):
    result = validate_provider_rows(
        [replace(valid_raw_bar, **change)], sessions, FIXED_NOW
    )
    assert result.canonical_bars == ()
    assert result.rejections[0].code is code
    assert result.rejections[0].raw_evidence["volume"] == change.get("volume", valid_raw_bar.volume)
```

Also write separate tests for unexpected symbol, missing field, invalid date, duplicate group rejecting both rows, positive factor calculation, raw/adjusted evidence preservation, `high == low` acceptance, and negative volume never becoming positive/zero.

- [ ] **Step 2: Run RED**

```powershell
.\venv\Scripts\python.exe -m pytest tests\unit\market_intelligence\test_validation.py -q
```

Expected: import failure for `validation.py`.

- [ ] **Step 3: Implement two-pass strict validation**

```python
def validate_provider_rows(rows, expected_sessions, ingested_at):
    expected = frozenset(expected_sessions)
    grouped = Counter((row.symbol, row.trading_date) for row in rows)
    accepted, rejected = [], []
    for row in rows:
        code, reason = _validate_one(row, expected, grouped)
        if code is not None:
            rejected.append(_rejection(row, code, reason, ingested_at))
            continue
        factor = row.adjusted_close / row.close
        accepted.append(CanonicalBar(
            provider=row.provider,
            provider_symbol=row.provider_symbol,
            symbol=row.symbol,
            raw_trading_date=row.raw_trading_date,
            trading_date=row.trading_date,
            raw_open=row.open,
            raw_high=row.high,
            raw_low=row.low,
            raw_close=row.close,
            provider_adjusted_close=row.adjusted_close,
            adjustment_factor=factor,
            adjusted_open=row.open * factor,
            adjusted_high=row.high * factor,
            adjusted_low=row.low * factor,
            adjusted_close=row.close * factor,
            provider_volume=row.volume,
            source_timestamp=row.source_timestamp,
            ingestion_timestamp=ingested_at,
            price_basis=PRICE_BASIS,
            normalization_version=NORMALIZATION_VERSION,
        ))
    received_symbols = tuple(sorted({row.symbol for row in rows}))
    return ValidationResult(tuple(accepted), tuple(rejected), received_symbols)
```

Check fields in stable precedence so one raw row has one primary rejection code. Check duplicates before numeric conversion. Serialize non-finite raw evidence as explicit strings so rejection JSON remains standards-compliant.

- [ ] **Step 4: Run GREEN, then run shared price-normalization tests to prove isolation**

```powershell
.\venv\Scripts\python.exe -m pytest tests\unit\market_intelligence\test_validation.py tests\unit\test_price_row_normalization.py -q
```

Expected: all selected tests pass; shared normalization behavior remains unchanged.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/domain/market_intelligence/validation.py backend/tests/unit/market_intelligence
git commit -m "feat: add strict sector bar validation"
```

---

### Task 3: Session-aware deterministic metrics

**Files:**
- Create: `backend/app/domain/market_intelligence/metrics.py`
- Create: `backend/tests/unit/market_intelligence/test_metrics.py`

**Interfaces:**
- Consumes: ordered `CanonicalBar` values and exact reference sessions.
- Produces: `calculate_symbol_metrics(bars, sessions) -> SectorMetrics` and `with_relative_returns(sector_metrics, spy_metrics) -> SectorMetrics`.

- [ ] **Step 1: Write failing hand-calculated tests**

```python
def test_return_offsets_use_sessions_not_calendar_days(canonical_series, sessions):
    metrics = calculate_symbol_metrics(canonical_series, sessions)
    assert metrics.return_1d == pytest.approx(close[-1] / close[-2] - 1)
    assert metrics.return_5d == pytest.approx(close[-1] / close[-6] - 1)
    assert metrics.return_20d == pytest.approx(close[-1] / close[-21] - 1)
    assert metrics.return_60d == pytest.approx(close[-1] / close[-61] - 1)


def test_rvol_excludes_today(canonical_series, sessions):
    metrics = calculate_symbol_metrics(canonical_series, sessions)
    assert metrics.rvol20 == pytest.approx(volume[-1] / mean(volume[-21:-1]))


def test_relative_return_is_sector_minus_spy(sector_metrics, spy_metrics):
    result = with_relative_returns(sector_metrics, spy_metrics)
    assert result.relative_return_vs_spy_20d == pytest.approx(
        sector_metrics.return_20d - spy_metrics.return_20d
    )
```

Add RED tests for insufficient 1/5/20/60 history, missing required session, duplicate date, NaN, RVOL zero denominator, zero-range MFM=0, CMF 5/20/60, CMF zero-volume denominator, and the fact that missing values are `None`, never `0.0`.

- [ ] **Step 2: Run RED**

```powershell
.\venv\Scripts\python.exe -m pytest tests\unit\market_intelligence\test_metrics.py -q
```

Expected: import failure for `metrics.py`.

- [ ] **Step 3: Implement minimal pure functions**

```python
def _return_at(close_by_date, sessions, offset):
    if len(sessions) <= offset:
        return None
    current = close_by_date.get(sessions[-1])
    anchor = close_by_date.get(sessions[-1 - offset])
    if current is None or anchor is None:
        return None
    return current / anchor - 1.0


def _mfm(bar):
    spread = bar.adjusted_high - bar.adjusted_low
    if spread == 0:
        return 0.0
    return (2.0 * bar.adjusted_close - bar.adjusted_high - bar.adjusted_low) / spread


def _cmf(window):
    denominator = sum(bar.provider_volume for bar in window)
    if denominator == 0:
        return None
    return sum(_mfm(bar) * bar.provider_volume for bar in window) / denominator
```

Require exact date coverage rather than indexing the Nth available provider row. Do not import SQLAlchemy, FastAPI, Redis, Celery, or provider modules.

- [ ] **Step 4: Run GREEN and the golden path**

```powershell
.\venv\Scripts\python.exe -m pytest tests\unit\market_intelligence\test_metrics.py -q
```

Expected: all metric tests pass with no warning or infinity.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/domain/market_intelligence/metrics.py backend/tests/unit/market_intelligence/test_metrics.py
git commit -m "feat: add deterministic sector intelligence metrics"
```

---

### Task 4: Dense ranks, predecessor semantics, and candidate assembly

**Files:**
- Create: `backend/app/domain/market_intelligence/ranking.py`
- Create: `backend/app/domain/market_intelligence/snapshot.py`
- Create: `backend/tests/unit/market_intelligence/test_ranking.py`
- Create: `backend/tests/unit/market_intelligence/test_snapshot.py`

**Interfaces:**
- Consumes: complete sector metrics, optional prior published sector snapshots.
- Produces: `dense_rank_sectors`, `build_rank_records`, `classify_ingestion_status`, and `build_candidate_snapshot`.

- [ ] **Step 1: Write RED ranking tests**

```python
def test_dense_rank_preserves_ties_without_symbol_tiebreak():
    ranks = dense_rank_sectors({"XLK": 0.2, "XLF": 0.2, "XLU": 0.1})
    assert ranks == {"XLF": 1, "XLK": 1, "XLU": 2}


@pytest.mark.parametrize(
    ("previous", "current", "change", "direction"),
    [
        (7, 2, 5, RankDirection.IMPROVED),
        (2, 7, -5, RankDirection.DECLINED),
        (3, 3, 0, RankDirection.UNCHANGED),
        (None, 3, None, RankDirection.NOT_AVAILABLE),
    ],
)
def test_rank_change_direction(previous, current, change, direction):
    result = rank_record(current=current, previous=previous)
    assert (result.change, result.direction) == (change, direction)
```

Assert SPY never appears, all six ranking metrics exist, symbol only orders output, and an unavailable metric prevents a publishable rank set.

- [ ] **Step 2: Write RED status and predecessor tests**

```python
@pytest.mark.parametrize(
    ("request_ok", "usable", "complete", "status"),
    [
        (True, 12, True, IngestionStatus.SUCCEEDED),
        (True, 11, False, IngestionStatus.PARTIAL),
        (True, 1, False, IngestionStatus.PARTIAL),
        (True, 0, False, IngestionStatus.FAILED),
        (False, 0, False, IngestionStatus.FAILED),
    ],
)
def test_status_partition_is_exhaustive(request_ok, usable, complete, status):
    assert classify_ingestion_status(request_ok, usable, complete) is status
```

Add a Monday-success/Tuesday-partial/Wednesday-success fixture in which `build_rank_records` is passed Monday rows and ignores Tuesday candidate rows by contract.

- [ ] **Step 3: Run RED**

```powershell
.\venv\Scripts\python.exe -m pytest tests\unit\market_intelligence\test_ranking.py tests\unit\market_intelligence\test_snapshot.py -q
```

- [ ] **Step 4: Implement deterministic ranking and assembly**

```python
def dense_rank_sectors(values):
    ordered_values = sorted(set(values.values()), reverse=True)
    rank_by_value = {value: index + 1 for index, value in enumerate(ordered_values)}
    return {symbol: rank_by_value[value] for symbol, value in sorted(values.items())}


def rank_record(*, current, previous):
    if previous is None:
        return RankRecord(current, None, None, RankDirection.NOT_AVAILABLE)
    change = previous - current
    direction = (
        RankDirection.IMPROVED if change > 0 else
        RankDirection.DECLINED if change < 0 else
        RankDirection.UNCHANGED
    )
    return RankRecord(current, previous, change, direction)
```

`build_candidate_snapshot` creates local-metric rows for usable symbols; it adds ranks only when all 11 sectors and all six ranking sources are present. Completeness requires 12 valid 90-session histories, zero row rejections, every required metric, benchmark alignment, and 12 snapshot rows.

- [ ] **Step 5: Run GREEN and commit**

```powershell
.\venv\Scripts\python.exe -m pytest tests\unit\market_intelligence\test_ranking.py tests\unit\market_intelligence\test_snapshot.py -q
git add backend/app/domain/market_intelligence backend/tests/unit/market_intelligence
git commit -m "feat: add sector ranking and snapshot assembly"
```

---

### Task 5: Additive persistence, repository reads, and Unit of Work integration

**Files:**
- Create: `backend/app/infra/db/models/market_intelligence.py`
- Create: `backend/app/domain/market_intelligence/ports.py`
- Create: `backend/app/infra/db/repositories/market_intelligence_repo.py`
- Create: `backend/alembic/versions/20260826_0031_add_market_intelligence_phase1.py`
- Modify: `backend/app/infra/db/models/__init__.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/infra/db/uow.py`
- Modify: `backend/tests/unit/repositories/conftest.py`
- Modify: `backend/tests/unit/repositories/test_uow.py`
- Create: `backend/tests/unit/repositories/test_market_intelligence_repo.py`
- Create: `backend/tests/unit/test_market_intelligence_migration.py`

**Interfaces:**
- Consumes: domain audit, bars, rejections, snapshots, and existing `FeatureRun` IDs.
- Produces: `MarketIntelligenceRepository` methods `find_exact`, `persist_candidate`, `get_previous_published`, `get_latest_attempt`, `get_latest_published`, and `list_published_history`; `SqlUnitOfWork.market_intelligence` shares the feature-run session.

- [ ] **Step 1: Write RED ORM/repository tests**

```python
def test_market_intelligence_repository_shares_uow_transaction(factory):
    with SqlUnitOfWork(factory) as uow:
        assert uow.market_intelligence._session is uow.feature_runs._session


def test_persist_candidate_rolls_back_all_rows_and_pointer_on_error(factory):
    with pytest.raises(RuntimeError):
        with SqlUnitOfWork(factory) as uow:
            run = uow.feature_runs.start_run(
                as_of_date=date(2026, 5, 15),
                run_type=RunType.DAILY_SNAPSHOT,
                universe_hash=UNIVERSE_HASH,
                input_hash="a" * 64,
                config_json={"pipeline": PIPELINE_NAME},
            )
            uow.market_intelligence.persist_candidate(run.id, audit, bars, rejections, snapshots)
            raise RuntimeError("force rollback")
    assert _counts(factory) == {"audits": 0, "bars": 0, "rejections": 0, "snapshots": 0}
```

Also test all raw/adjusted lineage round-trips, stable rejection identity, unique idempotency key, duplicate snapshot PK, latest attempt including failed/quarantined, latest pointer reads, history metric-version filtering, newest successful revision per session, and previous published query excluding same-date/partial/failed runs.

- [ ] **Step 2: Run RED**

```powershell
.\venv\Scripts\python.exe -m pytest tests\unit\repositories\test_market_intelligence_repo.py tests\unit\repositories\test_uow.py -q
```

Expected: imports/tables fail because persistence does not exist.

- [ ] **Step 3: Implement four ORM tables and additive migration**

Use `Numeric(24, 10)` for price/factor/volume evidence, timezone-aware `DateTime`, JSON for raw evidence/counter maps/rank maps, enum check constraints, FK cascades, and indexes for `(trading_date, metric_version)`, `(symbol, trading_date)`, and latest-attempt lookup.

```python
class MarketIntelligenceRunAudit(Base):
    __tablename__ = "market_intelligence_run_audits"
    run_id = Column(Integer, ForeignKey("feature_runs.id", ondelete="CASCADE"), primary_key=True)
    idempotency_key = Column(String(64), nullable=False, unique=True)
    ingestion_status = Column(String(16), nullable=False)
    provider = Column(String(32), nullable=False)
    provider_status = Column(String(16), nullable=False)
    metric_version = Column(String(64), nullable=False)
    normalization_version = Column(String(64), nullable=False)
    price_basis = Column(String(64), nullable=False)
    target_session = Column(Date, nullable=False, index=True)
    counters_json = Column(JSON, nullable=False)
    missing_symbols_json = Column(JSON, nullable=False)
    provider_failures_json = Column(JSON, nullable=False)
    request_failure_json = Column(JSON, nullable=True)
    source_freshness_json = Column(JSON, nullable=False)
    provider_response_at = Column(DateTime(timezone=True), nullable=True)
    ingestion_timestamp = Column(DateTime(timezone=True), nullable=False)
    calculation_timestamp = Column(DateTime(timezone=True), nullable=False)
```

`MarketIntelligenceCanonicalBar` has `run_id`, normalized symbol/date identity, raw provider trading date, provider/provider symbol, raw Open/High/Low/Close, provider adjusted Close, adjustment factor, adjusted Open/High/Low/Close, provider volume, source timestamp, ingestion timestamp, price basis, and normalization version. `MarketIntelligenceRejection` has generated ID, run/provider/provider-symbol identity, nullable normalized symbol/date, rejection code/reason, JSON raw evidence, and ingestion timestamp. `MarketIntelligenceSectorSnapshot` has run/symbol identity, trading date, asset type, sector name, the 13 metric columns, four rank JSON maps, provider, freshness JSON, price basis, metric version, calculation timestamp, and data-quality status. None of these fields are stored in `StockFeatureDaily`.

- [ ] **Step 4: Implement repository mapping and UoW registration**

```python
self.market_intelligence = SqlMarketIntelligenceRepository(self.session)
```

Insert that assignment after the existing repository assignments in `SqlUnitOfWork.__enter__`; do not reorder or replace the existing repositories.

Repository write methods flush but never commit. Repository published reads join `FeatureRun.status == "published"`; predecessor reads require `FeatureRun.as_of_date < target_session` and the same metric version.

- [ ] **Step 5: Run GREEN plus migration upgrade/downgrade test**

```powershell
.\venv\Scripts\python.exe -m pytest tests\unit\repositories\test_market_intelligence_repo.py tests\unit\repositories\test_uow.py tests\unit\test_market_intelligence_migration.py -q
```

Expected: all tests pass on SQLite; migration creates and drops only the four new tables.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/infra/db backend/app/domain/market_intelligence/ports.py backend/app/models backend/alembic/versions/20260826_0031_add_market_intelligence_phase1.py backend/tests/unit/repositories backend/tests/unit/test_market_intelligence_migration.py
git commit -m "feat: persist sector intelligence snapshots"
```

---

### Task 6: Idempotent runner and atomic publication invariants

**Files:**
- Create: `backend/app/use_cases/market_intelligence/__init__.py`
- Create: `backend/app/use_cases/market_intelligence/build_sector_snapshot.py`
- Create: `backend/tests/unit/use_cases/test_build_sector_intelligence_snapshot.py`

**Interfaces:**
- Consumes: provider port, calendar session sequence, `SqlUnitOfWork` factory, validator, metrics, snapshot builder, repository.
- Produces: `BuildSectorSnapshotCommand`, `BuildSectorSnapshotResult`, and `BuildSectorSnapshotUseCase.execute(command)`.

- [ ] **Step 1: Write seven RED end-to-end use-case tests**

Use the golden raw scenario and real SQLite repositories.

```python
def test_case_a_complete_run_publishes(factory, complete_batch, sessions):
    result = runner(factory, complete_batch, sessions).execute(command)
    assert result.ingestion_status is IngestionStatus.SUCCEEDED
    assert _pointer(factory).run_id == result.run_id
    assert _snapshot_count(factory, result.run_id) == 12


def test_case_b_eleven_of_twelve_is_partial_and_keeps_pointer(
    factory, complete_batch, batch_missing_xlu, sessions
):
    previous = runner(factory, complete_batch, sessions).execute(command)
    result = runner(factory, batch_missing_xlu, sessions).execute(command_next_day)
    assert result.ingestion_status is IngestionStatus.PARTIAL
    assert _pointer(factory).run_id == previous.run_id


def test_case_d_request_failure_has_no_fabricated_rejections(
    factory, request_timeout_batch, sessions
):
    result = runner(factory, request_timeout_batch, sessions).execute(command)
    assert result.ingestion_status is IngestionStatus.FAILED
    assert _rejection_count(factory, result.run_id) == 0
    assert _audit(factory, result.run_id).request_failure_json["code"] == "PROVIDER_TIMEOUT"
```

Add Case C zero usable -> FAILED, Case E `/latest` repository read remains previous after partial, Case F Monday success/Tuesday partial/Wednesday success uses Monday ranks, and Case G identical input rerun returns same run ID and unchanged row counts.

- [ ] **Step 2: Run RED**

```powershell
.\venv\Scripts\python.exe -m pytest tests\unit\use_cases\test_build_sector_intelligence_snapshot.py -q
```

- [ ] **Step 3: Implement the runner with one final Unit of Work**

```python
def execute(self, command):
    sessions = tuple(self._calendar.completed_sessions("US", command.as_of, minimum=90))
    batch = self._provider.fetch(MARKET_INTELLIGENCE_UNIVERSE, command.as_of)
    prepared = self._prepare(batch, sessions, command.as_of)
    with self._uow_factory() as uow:
        existing = uow.market_intelligence.find_exact(prepared.idempotency_key)
        if existing is not None:
            return self._result_from_existing(existing)
        previous = uow.market_intelligence.get_previous_published(
            before=command.as_of, metric_version=METRIC_VERSION
        )
        candidate = prepared.with_previous(previous)
        run = uow.feature_runs.start_run(
            as_of_date=command.as_of,
            run_type=RunType.DAILY_SNAPSHOT,
            universe_hash=UNIVERSE_HASH,
            input_hash=prepared.input_hash,
            config_json={"pipeline": PIPELINE_NAME, "metric_version": METRIC_VERSION},
        )
        uow.market_intelligence.persist_candidate(run.id, candidate)
        self._finalize_feature_run(uow, run.id, candidate)
        uow.commit()
        return BuildSectorSnapshotResult(run.id, candidate.ingestion_status, candidate.published)
```

For `PARTIAL`, call `mark_completed` then `mark_quarantined` with one critical `DQResult`; for `SUCCEEDED`, call `mark_completed` then `publish_atomically(run_id, LATEST_POINTER_KEY)`; for `FAILED`, call `mark_failed`. Catch `IntegrityError` from a concurrent idempotency insert, roll back, and read the winner without creating a second logical run.

- [ ] **Step 4: Run GREEN and explicit transaction-failure tests**

```powershell
.\venv\Scripts\python.exe -m pytest tests\unit\use_cases\test_build_sector_intelligence_snapshot.py tests\unit\repositories\test_market_intelligence_repo.py -q
```

Expected: all A-G cases pass, including pointer preservation and same-run idempotency.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/use_cases/market_intelligence backend/tests/unit/use_cases/test_build_sector_intelligence_snapshot.py
git commit -m "feat: atomically publish sector intelligence runs"
```

---

### Task 7: Yahoo adapter, session adapter, runtime wiring, and existing Celery pipeline

**Files:**
- Create: `backend/app/infra/providers/market_intelligence_yahoo.py`
- Create: `backend/app/services/market_intelligence_session_source.py`
- Create: `backend/app/wiring/market_intelligence_services.py`
- Modify: `backend/app/wiring/bootstrap.py`
- Create: `backend/app/tasks/market_intelligence_tasks.py`
- Modify: `backend/app/celery_app.py`
- Modify: `backend/app/tasks/daily_market_pipeline_tasks.py`
- Create: `backend/tests/unit/market_intelligence/test_yahoo_adapter.py`
- Create: `backend/tests/unit/test_market_intelligence_tasks.py`
- Modify: `backend/tests/unit/test_celery_config.py`
- Modify: `backend/tests/unit/test_daily_market_pipeline_tasks.py`

**Interfaces:**
- Consumes: `BulkDataFetcher.fetch_batch_prices(symbols, period="6mo")`, `MarketCalendarService.trading_days`, use case.
- Produces: `YahooMarketIntelligenceProvider.fetch`, `CompletedSessionSource.completed_sessions`, runtime `get_market_intelligence_runner`, Celery task `calculate_sector_intelligence_snapshot` routed to `market_jobs_us` and included only in the US daily chain.

- [ ] **Step 1: Write RED adapter tests**

```python
def test_yahoo_adapter_requests_exact_universe_without_auto_normalizer(fetcher):
    provider = YahooMarketIntelligenceProvider(fetcher, clock=fixed_clock)
    result = provider.fetch(MARKET_INTELLIGENCE_UNIVERSE, AS_OF)
    fetcher.fetch_batch_prices.assert_called_once_with(
        list(MARKET_INTELLIGENCE_UNIVERSE), period="6mo"
    )
    assert result.provider == "yahoo"


def test_total_batch_exception_is_one_request_failure_not_twelve_rows(fetcher):
    fetcher.fetch_batch_prices.side_effect = TimeoutError("timeout")
    result = provider.fetch(MARKET_INTELLIGENCE_UNIVERSE, AS_OF)
    assert result.request_failure.code == "PROVIDER_TIMEOUT"
    assert result.rows == ()
    assert result.symbol_failures == ()
```

Also test yfinance DataFrame -> raw field mapping, provider symbol, source/as-of timestamp, per-symbol missing response, malformed frame, and that no call imports or invokes `price_row_normalization`.

- [ ] **Step 2: Run RED**

```powershell
.\venv\Scripts\python.exe -m pytest tests\unit\market_intelligence\test_yahoo_adapter.py -q
```

- [ ] **Step 3: Implement adapter and completed-session source**

Convert DataFrame indices to dates, retain raw `Open/High/Low/Close/Adj Close/Volume` values untouched in `RawBar`, and let the strict validator decide validity. Map only total request conditions to `RequestFailure`; retain per-symbol `has_error` entries as symbol failures.

```python
class CompletedSessionSource:
    def completed_sessions(self, market, as_of, minimum=90):
        start = as_of - timedelta(days=minimum * 2 + 30)
        sessions = self._calendar.trading_days(market, start, as_of)
        if not sessions or sessions[-1] != as_of or len(sessions) < minimum:
            raise SessionWindowUnavailable(
                market=market,
                as_of=as_of,
                required_count=minimum,
                available_count=len(sessions),
            )
        return tuple(sessions[-minimum:])
```

- [ ] **Step 4: Write and run RED task/wiring tests**

```python
def test_task_is_registered_on_us_market_queue():
    name = "app.tasks.market_intelligence_tasks.calculate_sector_intelligence_snapshot"
    assert name in celery_app.conf.include
    assert celery_app.conf.task_routes[name] == {"queue": "market_jobs_us"}


def test_us_daily_pipeline_contains_sector_intelligence_only_once():
    tasks = [sig.task for sig in _build_daily_market_pipeline_signatures("US", AS_OF)]
    assert tasks.count(TASK_NAME) == 1
    assert TASK_NAME not in [sig.task for sig in _build_daily_market_pipeline_signatures("HK", AS_OF)]
```

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests\unit\test_market_intelligence_tasks.py tests\unit\test_celery_config.py tests\unit\test_daily_market_pipeline_tasks.py -q
```

- [ ] **Step 5: Implement minimal runtime and Celery wiring**

The task accepts only optional ISO `calculation_date`, resolves last completed US session when omitted, calls the runtime runner, returns run ID/status/published fields, and closes resources through existing runtime/session factories. Add one guard to the US chain that treats `SUCCEEDED`, `PARTIAL`, and `FAILED` as completed audit outcomes; it raises only on task execution exceptions, because a PARTIAL run is a valid non-publication outcome.

- [ ] **Step 6: Run GREEN and commit**

```powershell
.\venv\Scripts\python.exe -m pytest tests\unit\market_intelligence\test_yahoo_adapter.py tests\unit\test_market_intelligence_tasks.py tests\unit\test_celery_config.py tests\unit\test_daily_market_pipeline_tasks.py -q
git add backend/app/infra/providers/market_intelligence_yahoo.py backend/app/services/market_intelligence_session_source.py backend/app/wiring backend/app/tasks backend/app/celery_app.py backend/tests/unit
git commit -m "feat: wire daily sector intelligence ingestion"
```

---

### Task 8: Latest, history, and Data Health API contracts

**Files:**
- Create: `backend/app/schemas/market_intelligence.py`
- Create: `backend/app/api/v1/market_intelligence.py`
- Modify: `backend/app/api/v1/router.py`
- Create: `backend/tests/unit/test_market_intelligence_endpoints.py`

**Interfaces:**
- Consumes: SQL repository read models and named published pointer.
- Produces: `GET /api/v1/market-intelligence/sectors/latest`, `GET /api/v1/market-intelligence/sectors/history`, and `GET /api/v1/market-intelligence/sectors/health` Pydantic-validated JSON.

- [ ] **Step 1: Write RED API tests against SQLite**

```python
@pytest.mark.asyncio
async def test_latest_serves_previous_complete_after_new_partial(client, seeded_runs):
    response = await client.get("/api/v1/market-intelligence/sectors/latest")
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == seeded_runs.monday_published_id
    assert body["status"] == "SUCCEEDED"
    assert len(body["sectors"]) == 11
    assert body["benchmark"]["symbol"] == "SPY"


@pytest.mark.asyncio
async def test_health_reports_latest_attempt_and_latest_published_separately(client, seeded_runs):
    body = (await client.get("/api/v1/market-intelligence/sectors/health")).json()
    assert body["latest_attempt"]["status"] == "PARTIAL"
    assert body["latest_published"]["status"] == "SUCCEEDED"
    assert body["publication_occurred"] is False
```

Add tests for no published snapshot -> latest 404, health with failed request and zero row rejections, exact counters, provenance fields, proxy metadata, rank direction enums, history date/symbol/version filters, no v1/v2 mixing, and stable sector order.

- [ ] **Step 2: Run RED**

```powershell
.\venv\Scripts\python.exe -m pytest tests\unit\test_market_intelligence_endpoints.py -q
```

- [ ] **Step 3: Implement schemas and thin routes**

```python
@router.get("/sectors/latest", response_model=SectorIntelligenceLatestResponse)
def latest(db: Session = Depends(get_db)):
    bundle = SqlMarketIntelligenceRepository(db).get_latest_published(LATEST_POINTER_KEY)
    if bundle is None:
        raise HTTPException(status_code=404, detail="No complete sector intelligence snapshot has been published")
    return SectorIntelligenceLatestResponse.model_validate(bundle.to_api_dict())
```

History accepts `date_from`, `date_to`, optional fixed-universe `symbol`, `metric_version` defaulting to `market_intelligence_v1`, and bounded `limit`. Health returns audit counters directly without recalculation. Register with prefix `/market-intelligence` in the existing protected router.

- [ ] **Step 4: Run GREEN plus OpenAPI/router regression tests**

```powershell
.\venv\Scripts\python.exe -m pytest tests\unit\test_market_intelligence_endpoints.py tests\unit\test_router_feature_gating.py -q
```

Expected: all endpoint tests pass and no existing route disappears.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/schemas/market_intelligence.py backend/app/api/v1/market_intelligence.py backend/app/api/v1/router.py backend/tests/unit/test_market_intelligence_endpoints.py
git commit -m "feat: expose sector intelligence API"
```

---

### Task 9: Documentation, regression comparison, security check, and final review

**Files:**
- Modify: `docs/market-intelligence-spec.md`
- Create: `docs/phase1-implementation-report.md`
- Modify: `docs/superpowers/plans/2026-08-25-market-intelligence-phase-1.md` (check completed steps only)
- Verify: all files changed since `e65d1fc67db4b468471376aa29741fdce3759ffc`

**Interfaces:**
- Consumes: implementation and command evidence.
- Produces: final semantics/report, baseline comparison, review findings, and a stopped Phase 1 branch.

- [ ] **Step 1: Run the complete new Phase 1 suite**

```powershell
cd backend
.\venv\Scripts\python.exe -m pytest `
  tests\unit\market_intelligence `
  tests\unit\repositories\test_market_intelligence_repo.py `
  tests\unit\use_cases\test_build_sector_intelligence_snapshot.py `
  tests\unit\test_market_intelligence_tasks.py `
  tests\unit\test_market_intelligence_endpoints.py `
  tests\unit\test_market_intelligence_migration.py -q
```

Expected: all new tests pass, no skip/xfail.

- [ ] **Step 2: Run focused adjacent regression suites**

```powershell
.\venv\Scripts\python.exe -m pytest `
  tests\unit\repositories\test_feature_run_repo.py `
  tests\unit\repositories\test_uow.py `
  tests\unit\test_celery_config.py `
  tests\unit\test_daily_market_pipeline_tasks.py `
  tests\unit\test_price_row_normalization.py `
  tests\unit\test_market_rs_tasks.py -q
```

Expected: all selected adjacent tests pass.

- [ ] **Step 3: Run the unmodified baseline command and the documented Windows diagnostic command**

```powershell
.\venv\Scripts\python.exe -m pytest tests\unit -m "not live_service and not load" -q
```

Expected unmodified Windows behavior: the known `resource` collection limitation may remain. Then run the exact source-neutral shim command recorded in `docs/baseline-audit.md` and compare against `5991 passed, 13 failed, 3 skipped`; Phase 1 may add passing tests but must add zero failures.

```powershell
.\venv\Scripts\python.exe -c "import sys, types, pytest; m=types.ModuleType('resource'); m.RUSAGE_SELF=0; m.getrusage=lambda _: types.SimpleNamespace(ru_maxrss=0); sys.modules['resource']=m; raise SystemExit(pytest.main(['tests/unit','-m','not live_service and not load','-q']))"
cd ..\frontend
npm run test:run
cd ..\backend
```

Expected diagnostic comparison: the passing count increases by the new Phase 1 tests, the same 13 known backend failures remain, and no new failure appears. Expected frontend comparison: the same documented `598 passed, 8 failed` baseline remains, with no new failure.

- [ ] **Step 4: Run lint/compile, dependency, migration, and security checks**

```powershell
.\venv\Scripts\python.exe -m compileall -q app
.\venv\Scripts\python.exe -m pip check
cd ..\frontend
npm run lint
cd ..
git diff e65d1fc67db4b468471376aa29741fdce3759ffc -- 'backend/requirements*.txt' 'frontend/package*.json'
git diff --check
git status --short
git grep -n -I -E "(api[_-]?key|secret|token|password|device-id)" -- backend/app/domain/market_intelligence backend/app/infra/db/models/market_intelligence.py backend/app/infra/providers/market_intelligence_yahoo.py backend/app/use_cases/market_intelligence docs/phase1-implementation-report.md
```

Expected: compile and pip check pass; frontend lint matches the documented baseline of zero errors and four warnings; no dependency manifest diff; whitespace check passes; secret scan contains only deliberate documentation/field-name references and no value; `backend/.local/state/gh/device-id` is absent from status/index.

- [ ] **Step 5: Perform the final code review checklist**

Inspect and record evidence for all 24 requested review questions, especially:

```text
no parallel lifecycle/pointer
one final publication transaction
request failure != row rejection
no volume coercion path
raw and adjusted lineage retained
exact 60-session anchor
RVOL excludes current
CMF zero range/denominator semantics
dense ties
previous published session only
same-input idempotency
partial pointer preservation
health counters from audit
proxy terminology only
no dependency or baseline regression
```

If a defect is found, write a failing regression test first, observe RED, implement the minimal fix, rerun GREEN, and amend the relevant logical commit only if it has not been handed off; otherwise create a small repair commit.

- [ ] **Step 6: Update final docs with exact evidence**

`docs/market-intelligence-spec.md` must contain final universe/provider/basis/formulas/ranks/status/publication/version/Data Health/API semantics. `docs/phase1-implementation-report.md` must contain transaction-boundary diagram, migration/rollback, files/commits, test counts, known baseline failures, BLOCKED PostgreSQL/Redis/Celery-worker integrations, lint/dependency/security results, and final review findings.

- [ ] **Step 7: Commit documentation and perform final Git checks**

```powershell
git add docs/market-intelligence-spec.md docs/phase1-implementation-report.md docs/superpowers/plans/2026-08-25-market-intelligence-phase-1.md
git commit -m "docs: document market intelligence phase 1"
git status
git diff --stat e65d1fc67db4b468471376aa29741fdce3759ffc
git log --oneline e65d1fc67db4b468471376aa29741fdce3759ffc..HEAD
```

Expected: no uncommitted product changes, only small logical commits, no push/PR/merge, and final output ready. Stop without beginning Phase 2.
