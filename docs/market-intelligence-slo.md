# Market Intelligence read SLO

## Current status

The dedicated PostgreSQL 16 job is configured to enforce a common **1000 ms
p95** ceiling for all six read families. This initial threshold is derived from
the completed 20-sample baseline in GitHub Actions run
[`33430844324`](https://github.com/txu221/stock-screener/actions/runs/33430844324),
where the highest p95 was 717.408 ms and the highest single request was
721.843 ms. The threshold leaves 282.592 ms (39.4%) above the highest measured
p95 for shared-runner variation without hiding a full-second regression.

The baseline job succeeded, but the new enforcement settings have not yet run
in CI. A second Actions run must demonstrate that the configured enforcement
passes on PostgreSQL 16 and that the integration-test trigger lookup fix works
when multiple generated schemas contain the same trigger name.

No index was added. Any index change requires a captured PostgreSQL plan showing
a specific scan, sort, lookup, or buffer cost and measured before/after evidence.

## Database and dataset contract

The performance job owns a fresh PostgreSQL 16 service and a database named
exactly `market_intelligence_slo`. Before seeding, the harness connects and
asserts all of the following:

- `current_database()` exactly equals `market_intelligence_slo`;
- the connected server major version is 16;
- the live `alembic_version` rows exactly equal the repository Alembic heads.

The job runs `python -m alembic upgrade head`; the harness does not use ORM
`create_all`. The exact-name checks intentionally reject similar names such as
`market_intelligence_slo_copy` before destructive seed work.

The migrated database receives a deterministic synthetic S&P-universe workload:

- 500 active US equities and one published feature row per equity;
- bounded rank-decay market caps from $3 trillion to $5 billion;
- 90 price sessions for the equities and fixed ETF universe;
- 60 published sector-intelligence sessions with all 12 fixed symbols;
- both production publication pointers targeting the latest successful run.

## Request and SQL measurement

The benchmark mounts the production v1 router at `/api/v1`. It explicitly
overrides both the production database dependency and `require_server_session`,
and bypasses the optional Redis payload cache. Each API family is warmed once,
then measured sequentially 20 times:

- `overview`
- `movers`
- `etfs`
- `sectors/latest`
- `sectors/history`
- `sectors/health`

Warm-ups occur before SQL capture begins and are excluded. Every measured
family/request must capture at least one relevant PostgreSQL `SELECT` or the test
fails. The baseline JSON contains:

- per-family sample count, p50, p95, and worst API latency;
- per-request API latency, query count, aggregate SQL time, and API-minus-SQL
  time;
- per-family statement fingerprints, frequencies, and aggregate SQL time;
- the slowest measured SELECT and its request/family identity;
- measured after-cursor recorder bookkeeping overhead, in total and per SELECT.

The recorder's bookkeeping is included in API latency and excluded from cursor
SQL time. Its reported overhead covers statement/parameter observation work
performed after the cursor returns. SQLAlchemy event dispatch, the before-cursor
timer/context assignment, and the after-cursor timer read are not separately
isolated; small portions can land in either the observed SELECT interval or
API-minus-SQL. The JSON lists these unisolated components. API-minus-SQL also
includes routing, overridden dependency resolution, ORM row materialization,
application logic, and response serialization.

The slowest measured SELECT is replayed with its captured bind parameters using
`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`.

## Benchmark boundary and exclusions

Requests use `httpx.ASGITransport` in-process. The evidence includes FastAPI
production-router matching, dependency-override resolution, handler execution,
ORM work, PostgreSQL calls, and response serialization. It excludes:

- Uvicorn or another ASGI server, worker scheduling, and process handoff;
- socket/network latency, reverse proxies, and TLS;
- production lifespan and main-application middleware;
- real server-session authentication logic because that dependency is explicitly
  overridden for deterministic measurement;
- Redis and live-provider latency.

This boundary is suitable for database/read-path regression evidence, not an
end-user network-latency claim.

## Failure evidence

The baseline and plan JSON files are initialized before version validation and
written from an exception-transparent finalizer. Version, migration, seed,
warm-up, endpoint, query-capture, EXPLAIN, or SLO failures retain stage status,
the exception type/message, and whatever samples/query observations were
available. Artifact-write errors never replace an already-active pytest failure.

## CI command and artifact

The dedicated `market-intelligence-slo` job runs:

```bash
cd backend
python -m alembic upgrade head
MARKET_INTELLIGENCE_SLO_SAMPLE_COUNT=20 \
MARKET_INTELLIGENCE_ENFORCE_SLO=1 \
MARKET_INTELLIGENCE_SLO_P95_MS=1000 \
MARKET_INTELLIGENCE_SLO_ARTIFACT_DIR="$RUNNER_TEMP/market-intelligence-slo-evidence" \
python -m pytest \
  tests/integration/market_intelligence/test_postgres_performance_slo.py \
  -k postgresql_16_uncached_read_baseline_and_opt_in_slo \
  -q -s
```

`set -o pipefail` ensures `tee` cannot hide pytest or migration failures.
Metadata and upload steps use `if: always()`. Download
`market-intelligence-slo-<github_run_id>`, which contains available partial or
complete evidence:

- `market-intelligence-slo-baseline.json`;
- `market-intelligence-slowest-query-plan.json`;
- migration and pytest logs;
- run metadata and this document.

The ordinary integration job runs the service-independent helper/workflow
contracts but does not collect timing evidence.

## Promotion to an enforceable SLO

The first reviewed PostgreSQL 16 run established the initial threshold. For a
new baseline or threshold change:

1. Confirm all six families contain 20 successful measured requests and relevant
   SELECT evidence.
2. Inspect per-request counts and statement frequencies for N+1 behavior.
3. Inspect SQL versus API-minus-SQL time to distinguish database cost from ORM,
   application, and serialization cost.
4. Inspect the slowest plan for scans, sorts, lookup cost, and buffer pressure.
5. Repeat the run if runner noise makes the evidence questionable.
6. Choose and document a conservative p95 threshold from reviewed evidence.

Enforcement requires both `MARKET_INTELLIGENCE_ENFORCE_SLO=1` and a positive,
evidence-derived `MARKET_INTELLIGENCE_SLO_P95_MS`. Comparisons use the unrounded
p95; three-decimal rounding is serialization/display only. The dedicated job
now sets both values to enforce the measured 1000 ms ceiling.

## PostgreSQL 16 baseline results

Run `33430844324` measured commit `ef1fdec7` against PostgreSQL 16.15 and Alembic
head `20260829_0035`. The fresh `market_intelligence_slo` database contained 500
equities, 528 total price symbols, 47,520 price rows (90 sessions per symbol),
and 60 published sector sessions with 12 symbols each. One warm-up per family
was excluded; every family then completed 20 uncached requests.

| Family | SELECTs/request | p50 ms | p95 ms | worst ms | aggregate SQL ms | aggregate API-minus-SQL ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| overview | 3 | 34.406 | 36.111 | 37.708 | 43.924 | 646.144 |
| movers | 5 | 671.817 | 717.408 | 721.843 | 886.050 | 10,707.824 |
| ETFs | 3 | 60.660 | 82.767 | 448.268 | 132.111 | 1,464.120 |
| sectors/latest | 5 | 8.395 | 9.236 | 9.455 | 48.745 | 120.861 |
| sectors/history | 182 | 176.416 | 206.309 | 553.114 | 1,160.998 | 2,727.982 |
| sectors/health | 4 | 19.129 | 20.385 | 30.232 | 85.063 | 306.279 |

Query counts were constant across the 20 requests in each family. The
`sectors/history` count is a follow-up concern: three statement fingerprints
each ran 60 times per request, producing 182 SELECTs/request. Its SQL share was
29.85% of API time, so batching/eager-loading should be investigated separately;
an index does not remove that N+1 shape.

The slowest observed SELECT belonged to `movers` and took 43.695 ms in request
10. `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` used the existing
`uix_symbol_date` index, returned 21,500 rows versus 21,398 estimated, completed
the index scan in 5.849 ms and the plan in 6.627 ms, hit 1,601 shared buffers,
and performed no shared reads, writes, or temporary I/O. Across movers requests,
SQL was only 7.64% of API time; API-minus-SQL was 10,707.824 ms of 11,593.874 ms.
The evidence therefore supports **no new index**. The difference between the
captured SELECT duration and EXPLAIN execution also includes returning the full
rowset through the driver, which EXPLAIN does not do.

Two isolated worst-case samples were application-side rather than SQL-side:
ETFs took 448.268 ms with only 6.543 ms SQL, and `sectors/history` took
553.114 ms with 58.033 ms SQL. Movers also showed large API-minus-SQL variation
while SQL stayed near 40–51 ms. The artifact cannot distinguish shared-runner
pauses from ORM/application/serialization work. This API-minus-SQL variation is
why the first ceiling is conservative and why it should be reassessed after
more enforced runs.

Recorder bookkeeping measured 22.024551 ms total across 4,040 SELECTs, or
0.005452 ms/SELECT. This is included in API time and excluded from SQL time;
the event-dispatch and timer components listed earlier remain unisolated.
