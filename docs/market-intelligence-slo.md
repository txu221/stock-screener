# Market Intelligence read SLO

## Current status

The PostgreSQL read baseline is **provisional and not yet enforced**. The local
development host does not have PostgreSQL listening on `localhost:5432`, so this
change claims no latency value, plan diagnosis, SLO, or index need. The first
evidence must come from the dedicated PostgreSQL 16 GitHub Actions job.

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

After a reviewed PostgreSQL 16 Actions run:

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
p95; three-decimal rounding is serialization/display only. Until reviewed
evidence exists, `enforced` remains false and the threshold remains null.

## PostgreSQL 16 baseline results

Pending the first reviewed GitHub Actions run. Do not populate this section from
SQLite timing, an unreviewed estimate, or the in-process benchmark alone.
