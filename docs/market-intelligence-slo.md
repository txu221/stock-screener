# Market Intelligence read SLO

## Current status

The PostgreSQL read baseline is **provisional and not yet enforced**. The local
development host did not have PostgreSQL listening on `localhost:5432`, so this
change does not claim latency values, a slow-query diagnosis, or an index need.
The reviewed harness must first run against PostgreSQL 16 in GitHub Actions.

No index was added. Any index change requires the captured PostgreSQL plan to
show a relevant scan, sort, or lookup cost and must include measured before/after
evidence.

## What is measured

The harness seeds a deterministic, disposable schema with:

- 500 active US S&P-style equities and one published feature row per equity;
- 90 price sessions for the 500 equities and the fixed ETF universe;
- 60 published sector-intelligence sessions with all 12 fixed symbols;
- both production publication pointers targeting the latest successful run.

It warms each endpoint once, bypasses the optional Redis read cache, then takes
10 sequential samples of each API family:

- `overview`
- `movers`
- `etfs`
- `sectors/latest`
- `sectors/history`
- `sectors/health`

The report records sample count, p50, p95, and worst latency in milliseconds.
Percentiles use deterministic linear interpolation at
`(sample_count - 1) * percentile`. Warm-up calls are excluded. There are no
wall-clock latency assertions in the default unit or service-independent test
runs.

During measured calls, the harness records individual PostgreSQL `SELECT`
durations. It selects the slowest observed statement and executes
`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` with the captured bind parameters.
This is a database baseline; it requires neither live Yahoo data nor Redis.

## CI command and evidence

The PostgreSQL 16 integration job runs:

```bash
cd backend
MARKET_INTELLIGENCE_SLO_ARTIFACT_DIR="$RUNNER_TEMP/phase2-evidence" \
python -m pytest \
  tests/integration/market_intelligence/test_postgres_performance_slo.py \
  -k postgresql_16_uncached_read_baseline_and_opt_in_slo \
  -q -s
```

Download the Actions artifact named
`market-intelligence-integration-<github_run_id>`. It contains:

- `market-intelligence-slo-baseline.json` — dataset metadata, sampling method,
  per-family p50/p95/worst values, query counts, and the slowest SQL statement;
- `market-intelligence-slowest-query-plan.json` — the same query identity plus
  PostgreSQL's machine-readable `ANALYZE` and `BUFFERS` plan;
- `phase2-slo.log` and this document.

## Promotion to an enforceable SLO

After the controller pushes the reviewed harness and the PostgreSQL 16 Actions
run completes:

1. Verify that all six families have the configured sample count and successful
   responses.
2. Inspect the slowest plan for sequential scans over relevant large relations,
   avoidable sorts, repeated/N+1 query patterns, buffer pressure, and JSON
   decoding/response assembly cost.
3. Repeat the run if runner noise or cold-start behavior makes the sample
   questionable.
4. Choose a conservative p95 threshold from the reviewed measurements and
   record the run ID and values in this document.
5. Enable enforcement only by setting both
   `MARKET_INTELLIGENCE_ENFORCE_SLO=1` and the evidence-derived positive value
   `MARKET_INTELLIGENCE_SLO_P95_MS=<milliseconds>` in the dedicated performance
   job. The single threshold applies to every API family.

Until those steps are complete, the artifact is diagnostic evidence and the SLO
fields report `enforced: false` and `p95_threshold_ms: null`.

## PostgreSQL 16 baseline results

Pending the first reviewed GitHub Actions run. Do not fill this section from a
SQLite timing or an unreviewed local estimate.
