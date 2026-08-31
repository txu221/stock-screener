# Task 9 report: Yahoo replacement provenance CI regression

## Outcome

The Backend Unit Suite shard 2/4 failure from GitHub Actions run
`33449992426` was a stale legacy test contract, not a production persistence
defect. The regression test now exercises the real Yahoo replacement boundary
with explicit provider and reconciliation evidence and verifies both the
current row and its append-only revision history.

No production code, dependency, workflow, or Task 8 file changed.

## Root cause

`test_store_in_database_replaces_latest_day_row` directly called the private
`PriceCacheService._store_in_database` method without a provider. Corporate-
action hardening in commit `73aebd56` deliberately changed
`stock_price_row_from_ohlcv` to accept `Adj Close` only when the source is
explicitly `provider="yahoo"`. This prevents native CN/KRX columns that merely
alias their raw close as `Adj Close` from being treated as reconciled Yahoo
adjustment evidence.

The provider-less test therefore asked the normalizer to replace the current
row with unproven data. The close was updated to `110.0`, but the adjusted close
was correctly normalized to `None`, producing the CI assertion failure.
Production direct Yahoo fetch paths already pass `provider="yahoo"` and a
reconciliation timestamp. Defaulting the private method to Yahoo would have
violated the provenance restriction and was intentionally rejected.

## Surgical correction

The existing regression test now supplies:

- `provider="yahoo"`;
- an explicit UTC `reconciled_at` timestamp; and
- a real Yahoo frame whose replacement adjusted close is `109.5`.

It verifies the current row's close, adjusted close, adjustment factor,
provider, reconciled price basis, reconciliation marker, and revision number.
It also verifies the append-only audit contains legacy revision 0 followed by
Yahoo revision 1, with the correct adjusted-close and provenance values.

## RED evidence

The repository's documented in-memory `resource` shim was required because the
local Windows runtime cannot import the Unix-only module. Before changing the
test:

```text
python -c "... pytest.main([
  'tests/unit/test_yahoo_batch_ingestion.py::test_store_in_database_replaces_latest_day_row',
  '-q'
])"

FAILED test_store_in_database_replaces_latest_day_row
assert None == 109.5
1 failed, 2 warnings in 3.02s
```

This reproduced the same failure as CI run `33449992426`.

## GREEN and regression evidence

After correcting the test boundary:

```text
test_yahoo_batch_ingestion.py::test_store_in_database_replaces_latest_day_row
1 passed, 2 warnings in 1.86s

tests/unit/test_yahoo_batch_ingestion.py
41 passed, 4 warnings in 8.02s

tests/unit/test_price_row_normalization.py
tests/unit/test_stock_price_persistence.py
22 passed, 2 warnings in 4.45s

tests/unit/test_price_cache_non_finite.py
tests/unit/test_daily_price_bundle_service.py
tests/unit/test_benchmark_cache_service.py
tests/unit/test_market_intelligence_production_hardening_migration.py
49 passed, 20 warnings in 14.97s
```

The warnings are existing Pydantic, pandas/PyArrow, and `datetime.utcnow()`
deprecations. No new warning or failure was introduced.

## Scope and semantics

- The Yahoo adjusted close remains accepted only with explicit Yahoo evidence.
- Native CN/KRX adjusted-close aliases remain unreconciled.
- The current `stock_prices` materialization is replaced in place.
- Legacy and incoming provider evidence remain preserved in the append-only
  `stock_price_revisions` audit.
- Task 8 files (`mvp.py`, `FreshnessBanner`, and workflows) were not touched.
