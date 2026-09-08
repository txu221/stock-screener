# Task 6 Report: Pointer-Versioned Redis Read Cache

## Outcome

Implemented a fail-open JSON read-through cache for the five stable Market Intelligence read endpoints: overview, movers, ETF radar, sector latest, and sector history. Sector health remains an uncached PostgreSQL read.

Committed as `bfa899e1` (`feat: cache stable market intelligence reads`).

Every cache key is namespaced by cache format version and includes endpoint identity, the validated stable published run ID, stable trading date, metric version, and canonical request parameters. Missing or unpublished pointers bypass Redis storage. A post-compute pointer recheck prevents a publication swap during computation from writing a new payload under the old generation.

## RED evidence

### Cache service contract

Command:

```powershell
backend\venv\Scripts\python.exe -m pytest backend/tests/unit/services/test_market_intelligence_read_cache.py -q
```

Initial result: **11 errors** with `ImportError` because `market_intelligence_read_cache` did not exist. This covered key identity/normalization, A/B/C/D transitions, malformed JSON, Redis get/set failure, TTL, concurrent miss coalescing, independent keys, and exception cleanup.

The publication-race regression initially failed with `TypeError` because `cached_market_intelligence_payload` did not accept a stability recheck. The Redis client-creation regression initially failed with an uncaught `ConnectionError`. Both failure paths were implemented and rerun green.

The mandatory review identified four further edge cases. A six-case RED selection then failed for syntactically valid but schema-invalid JSON (`null`, scalar, and legacy object), cached freshness rollover, metric-version whitespace/query alignment, and ABA pointer repoints. After the fixes, the same selection passed **7 tests** (the schema case is parametrized three ways).

### Endpoint integration

The initial focused endpoint selection failed because the existing routes did not invoke the cache. The no-pointer overview and stable sector latest/history assertions observed zero cache calls. An initial movers test input also exposed FastAPI's intentional case-sensitive `Literal` validation and was corrected to test whitespace/case normalization on the already accepted sector parameter without changing the API contract.

## GREEN and verification evidence

### Focused cache and affected read/API suite

Command:

```powershell
backend\venv\Scripts\python.exe -m pytest backend/tests/unit/services/test_market_intelligence_read_cache.py backend/tests/unit/services/test_market_intelligence_read_service.py backend/tests/unit/test_market_intelligence_mvp_endpoints.py backend/tests/unit/test_market_intelligence_endpoints.py -q
```

Final result: **46 passed, 2 warnings**. The warnings are existing Pydantic class-config and pandas/PyArrow deprecations.

### Broad Market Intelligence regression suite

Command:

```powershell
backend\venv\Scripts\python.exe -m pytest backend/tests/unit/market_intelligence backend/tests/unit/services/test_market_intelligence_read_cache.py backend/tests/unit/services/test_market_intelligence_read_service.py backend/tests/unit/test_market_intelligence_endpoints.py backend/tests/unit/test_market_intelligence_mvp_endpoints.py backend/tests/unit/repositories/test_market_intelligence_repo.py backend/tests/unit/use_cases/test_build_sector_intelligence_snapshot.py backend/tests/unit/test_market_intelligence_tasks.py -q
```

Final result: **243 passed, 2 warnings** in 40.57 seconds.

### Integration/API contracts available on this host

Command:

```powershell
backend\venv\Scripts\python.exe -m pytest backend/tests/integration/market_intelligence/test_api_contract.py backend/tests/integration/market_intelligence/test_runtime_semantics.py backend/tests/integration/market_intelligence/test_postgres_api.py -q
```

Result: **2 passed, 1 skipped, 2 warnings**. The PostgreSQL contract remains opt-in.

Static compilation of both changed production modules passed. Ruff is not installed in the repository virtualenv, so no Ruff result is claimed.

### Independent review

The mandatory reviewer initially found four Important edge cases: dynamic MVP freshness cached too long, syntactically valid but schema-invalid JSON, history metric-version key/query mismatch, and an ABA pointer race. All four were fixed with RED/GREEN regressions. The closure review found no Critical or Important issues and returned **Ready to merge**, subject to the real-Redis CI contract.

## Key format and invalidation semantics

Key shape:

```text
market-intelligence:read:v1:<endpoint>:run:<stable-run-id>:date:<stable-date>:metric:<metric-version>:params:<canonical-url-encoded-json>
```

- Parameter mappings are sorted; `None` entries are omitted; strings are trimmed; integral floats and negative zero normalize to integers; dates use ISO format; non-finite floats are rejected. Endpoints case-normalize only fields whose query semantics are case-insensitive.
- Endpoint identity prevents collisions between overview, movers, ETFs, latest, and history.
- A successful publication establishes generation A and its payload. Partial B and failed C attempts do not move the validated published pointer, so they cannot create a new key or overwrite A. Successful D moves the pointer and immediately produces a distinct generation D key; generation A expires naturally.
- The TTL comes from `settings.cache_ttl_seconds` and is clamped to 60 seconds through 604800 seconds. Pointer versioning is the immediate invalidation mechanism; TTL is the bounded cleanup backstop.
- A pointer recheck after computation gates the Redis write. The comparison includes the pointer's `updated_at` when it changes. If an A→D→A repoint shares one timestamp, the captured run binding still computes immutable A rather than D; history additionally compares its immutable publication generation.
- Sector history retains the published pointer's metric version in stable identity and includes its separately normalized requested metric version in parameters, matching repository query and response semantics.

## Fallback and concurrency semantics

- Redis package/pool/client absence, client creation failure, `GET` failure, invalid UTF-8/JSON, schema validation failure, serialization failure, and `SETEX` failure never replace the PostgreSQL/compute result with HTTP 500.
- Invalid JSON and syntactically valid but response-schema-incompatible JSON are treated as misses and repaired with the recomputed JSON payload when Redis remains writable.
- A ref-counted process-local lock registry is keyed by the complete cache key. Same-key concurrent misses share one computation; different keys compute concurrently.
- Computation exceptions are propagated, waiting callers observe the same failed flight, and the per-key registry entry is removed after the last caller. No distributed lock or new dependency was added.

## Files

### Production

- `backend/app/services/market_intelligence_read_cache.py`
- `backend/app/api/v1/market_intelligence.py`

### Tests and task evidence

- `backend/tests/unit/services/test_market_intelligence_read_cache.py`
- `backend/tests/unit/test_market_intelligence_endpoints.py`
- `backend/tests/unit/test_market_intelligence_mvp_endpoints.py`
- `backend/tests/integration/market_intelligence/test_redis_celery_runtime.py`
- `.superpowers/sdd/task-6-plan.md`
- `.superpowers/sdd/task-6-report.md`

## Self-review

- Confirmed all five stable read endpoints validate the appropriate published pointer before constructing cache keys.
- Confirmed absent pointers and unpublished/incomplete attempts never store unavailable or attempt payloads.
- Confirmed health has no cache call and therefore continues to expose the latest attempt/failure state immediately.
- Confirmed response models are serialized in Pydantic JSON mode and reconstructed through the same response type, preserving dates, datetimes, status codes, and response schemas.
- Confirmed cached MVP responses refresh `expected_session` and `freshness_status` from the current completed-session calendar on every request, without changing the stable pointer generation key.
- Confirmed no pre-existing ETag or conditional handling exists on these endpoints, so none was removed or bypassed.
- Confirmed the change does not alter provider, universe, metric calculations, database schema, migrations, Celery routing, or external dependencies.
- Confirmed cache locks are per-key, ref-counted, released on success and exception, and absent after each concurrency test.
- Confirmed review findings for dynamic freshness, schema-invalid JSON, history metric-version collisions, ABA repoints, and UUID test isolation were fixed before final verification.

## Blocked real-Redis integration

A UUID-scoped real-Redis cache round-trip/TTL contract was added beside the existing connectivity contract. It patches only the cache service's client source, verifies one computation across two reads, verifies stored JSON and a positive bounded TTL, and deletes only its generated key.

Local command forced the opt-in contract against `redis://127.0.0.1:6379/15`. Both the existing connectivity test and the new cache test were blocked by `redis.exceptions.ConnectionError` / Windows error 10061 because no Redis server is running on this host. The contract is preserved for Redis-enabled CI; unit fallback, transition, TTL, deserialization, and concurrency semantics were not skipped.

## Review Fix: immutable read generations (2026-08-31)

### Outcome

Closed all three Important and both Minor follow-up findings. Cache format is now `v2`. Overview, movers, and ETF misses resolve the captured MVP run ID; sector latest resolves the captured sector run ID; history resolves only rows at or before its captured immutable publication generation. Pointer revision and history generation are part of cache identity, so an A→D→A repoint cannot coalesce a D computation under A, and a successful historical backfill invalidates history even when the latest pointer does not move.

During final audit, the preserved fix was found to use `MAX(run_id)` as history generation. That misses a lower-ID run which completes or is force-published after a higher-ID latest run, and its ID-only bound can admit that new row into an older in-flight generation. A new RED regression reproduced this as `assert 2 != 2`. The generation is now the immutable `(published_at, run_id)` publication-order pair. SQL history reads use the same pair as their visibility ceiling, including the publication timestamp tie-break by run ID.

A second audit regression covered the empty-generation boundary: when the requested metric version had no history at capture time, treating `None` as an unbounded query allowed the first concurrent publication to appear in the old in-flight response. The RED test returned run 2 instead of an empty item list. Empty captured generations now compute an explicitly empty history, while the full-identity recheck suppresses a cache write after the first publication.

Strict Redis decoding now rejects `NaN`, `Infinity`, and `-Infinity` through `json.loads(parse_constant=...)` at any nesting depth. These values are cache misses, are recomputed from PostgreSQL, and are repaired when Redis remains writable. Optional movers `sector` and `search` values canonicalize whitespace-only input to `None` for both cache identity and computation. A failed same-key local flight stores its exception for all registered waiters, every waiter terminates, and the lock registry is removed after the final caller.

### RED/GREEN evidence

Late lower-ID publication RED command:

```powershell
backend\venv\Scripts\python.exe -m pytest backend/tests/unit/repositories/test_market_intelligence_repo.py::test_history_generation_tracks_a_lower_run_id_published_late -q
```

Initial result: **1 failed** with `assert 2 != 2`, proving `MAX(run_id)` did not advance when an older backfill run was created first and published after the latest run.

Generation-focused GREEN command:

```powershell
backend\venv\Scripts\python.exe -m pytest backend/tests/unit/repositories/test_market_intelligence_repo.py::test_history_generation_tracks_a_lower_run_id_published_late backend/tests/unit/repositories/test_market_intelligence_repo.py::test_history_generation_and_bound_include_backfill_without_repointing backend/tests/unit/test_market_intelligence_endpoints.py::test_history_generation_advances_for_older_backfill_without_pointer_move backend/tests/unit/test_market_intelligence_endpoints.py::test_history_compute_is_bounded_to_generation_during_older_backfill backend/tests/unit/test_market_intelligence_endpoints.py::test_history_identity_rejects_aba_pointer_swap -q
```

Result: **5 passed, 2 warnings**.

Empty-generation race RED/GREEN command:

```powershell
backend\venv\Scripts\python.exe -m pytest backend/tests/unit/test_market_intelligence_endpoints.py::test_empty_history_generation_stays_empty_during_first_publication -q
```

Initial result: **1 failed** because the response contained newly published run 2. After bounding the captured empty generation, result: **1 passed, 2 warnings**.

Same-timestamp ABA proof command:

```powershell
backend\venv\Scripts\python.exe -m pytest backend/tests/unit/test_market_intelligence_endpoints.py::test_latest_compute_stays_pinned_during_same_timestamp_aba_swap backend/tests/unit/test_market_intelligence_endpoints.py::test_history_generation_is_stable_during_same_timestamp_aba_swap backend/tests/unit/test_market_intelligence_endpoints.py::test_empty_history_generation_stays_empty_during_first_publication backend/tests/unit/test_market_intelligence_mvp_endpoints.py::test_overview_compute_stays_pinned_during_same_timestamp_aba_swap -q
```

Result: **4 passed, 2 warnings**. Latest and overview returned captured A even when the pointer completed A→D→A with an unchanged timestamp. Pre-existing history remained at its captured generation; a concurrently first-published history changed generation and stayed out of the captured-empty response.

Strict non-finite Redis/API selection:

```powershell
backend\venv\Scripts\python.exe -m pytest backend/tests/unit/services/test_market_intelligence_read_cache.py backend/tests/unit/test_market_intelligence_mvp_endpoints.py -k "nonfinite or non_finite" -q
```

Result: **9 passed, 31 deselected, 2 warnings**. Six cache-service cases cover scalar and nested `NaN`/positive and negative infinity; three endpoint cases prove fallback without HTTP 500.

Failed-flight waiter selection:

```powershell
backend\venv\Scripts\python.exe -m pytest backend/tests/unit/services/test_market_intelligence_read_cache.py -k "concurrent_failed_flight" -q
```

Result: **1 passed, 23 deselected, 2 warnings** with six same-key callers, one computation, six propagated failures, no live threads, and an empty lock registry.

Focused cache/read/repository/endpoint command:

```powershell
backend\venv\Scripts\python.exe -m pytest backend/tests/unit/services/test_market_intelligence_read_cache.py backend/tests/unit/services/test_market_intelligence_read_service.py backend/tests/unit/repositories/test_market_intelligence_repo.py backend/tests/unit/test_market_intelligence_endpoints.py backend/tests/unit/test_market_intelligence_mvp_endpoints.py -q
```

Final result: **79 passed, 2 warnings**.

Broad Windows-safe Market Intelligence unit command:

```powershell
backend\venv\Scripts\python.exe -m pytest backend/tests/unit/market_intelligence backend/tests/unit/services/test_market_intelligence_read_cache.py backend/tests/unit/services/test_market_intelligence_read_service.py backend/tests/unit/test_market_intelligence_endpoints.py backend/tests/unit/test_market_intelligence_mvp_endpoints.py backend/tests/unit/repositories/test_market_intelligence_repo.py backend/tests/unit/use_cases/test_build_sector_intelligence_snapshot.py backend/tests/unit/test_market_intelligence_tasks.py -q
```

Final result: **264 passed, 2 warnings**.

Local integration contract command:

```powershell
backend\venv\Scripts\python.exe -m pytest backend/tests/integration/market_intelligence/test_api_contract.py backend/tests/integration/market_intelligence/test_runtime_semantics.py backend/tests/integration/market_intelligence/test_postgres_api.py -q
```

Result: **2 passed, 1 skipped, 2 warnings**. PostgreSQL remains opt-in.

Static verification command:

```powershell
backend\venv\Scripts\python.exe -m compileall -q backend/app/api/v1/market_intelligence.py backend/app/domain/market_intelligence/ports.py backend/app/infra/db/repositories/market_intelligence_repo.py backend/app/services/market_intelligence_read_cache.py backend/app/services/market_intelligence_read_service.py
git diff --check
```

Result: both commands exited **0**.

### Final key and generation semantics

Key shape:

```text
market-intelligence:read:v2:<endpoint>:run:<captured-run-id>:date:<captured-date>:metric:<metric-version>:revision:<pointer-updated-at>:generation:<history-published-at>#<history-run-id>:params:<canonical-json>
```

- Non-history endpoints use `generation:None`; all stable endpoints include the captured pointer revision.
- The history generation is ordered by immutable publication time and then run ID. A later publication changes the generation regardless of whether its run ID or trading date is older and regardless of whether the latest pointer moves.
- History SQL applies `published_at < cutoff OR (published_at = cutoff AND run_id <= cutoff_run_id)`, so a newly published row cannot leak into computation for an older generation.
- The post-compute stability check compares the full captured identity. A changed pointer timestamp or history generation suppresses the Redis write. If a same-timestamp ABA returns to A, an A write remains safe because every computation is run-pinned; D uses a distinct run key. History content is independently bounded by its publication generation.
- Partial and failed attempts remain outside identity and history. Health remains uncached. Redis remains fail-open and no dependency or response schema was added.

### Self-review

- Traced all five endpoint miss paths to immutable captured inputs: explicit MVP `published_run_id`, sector `get_published_by_run_id`, or history `max_generation`.
- Confirmed no-pointer MVP reads are pinned to unavailable state instead of following a pointer that appears after identity capture.
- Confirmed same-timestamp pointer ABA cannot substitute D for A: endpoint computation remains pinned to captured A, while D has a distinct run key. When a publication changes history content, the independent history generation changes and fails the recheck.
- Confirmed history backfill invalidation for both newly created higher-ID and pre-existing lower-ID late publications.
- Confirmed strict JSON constants are rejected before response-schema validation and never escape the cache layer.
- Confirmed blank filter normalization is identical between key parameters and read-service arguments.
- Confirmed all failed-flight waiters see the same failure and registry cleanup permits a later successful retry.
- Confirmed health, fail-open behavior, external API schemas, dependencies, and Celery routing are unchanged.

### Final review disposition

The final reviewer raised one concern that `FeatureRunPointer.updated_at` is not guaranteed unique. No pointer-revision schema change was made because uniqueness is not a correctness prerequisite after immutable run binding: an A-key miss can only compute A, and a D request has a different run ID/key. Pointer repoints among already-published runs do not change history content. The same-timestamp ABA tests above directly cover this reasoning. A subsequent rereview isolated a separate valid history-publication collision, addressed in the next section.

### Environment concern

Forced real-Redis command:

```powershell
$env:RUN_MARKET_INTELLIGENCE_REDIS='1'; $env:PHASE2_REDIS_URL='redis://127.0.0.1:6379/15'; backend\venv\Scripts\python.exe -m pytest backend/tests/integration/market_intelligence/test_redis_celery_runtime.py::test_real_redis_connectivity_uses_scoped_round_trip backend/tests/integration/market_intelligence/test_redis_celery_runtime.py::test_real_redis_market_intelligence_read_cache_round_trip_and_ttl -q
```

Result: **2 failed only because Redis was unreachable**, `redis.exceptions.ConnectionError`, Windows error 10061 at `127.0.0.1:6379`. No local Redis service is running. The UUID-scoped real-Redis contracts remain available for Redis-enabled CI; this is the only completion concern.

## Review Fix 2: serialize publication generations (2026-08-31)

### Root cause and fix

The immutable history token is ordered by `(published_at, run_id)`, but `publish_atomically_if_not_older` previously advanced `published_at` only when the candidate and current pointer had the same `as_of_date`. A pre-existing lower-ID backfill could therefore publish later with exactly the current high-water timestamp. Given captured generation `(T, 20)`, publishing backfill `(T, 10)` left the generation unchanged and the captured SQL cutoff admitted run 10 because its timestamp equaled `T` and its ID was below 20.

The existing PostgreSQL advisory lock and pointer row lock already serialize publications for one pointer key. After acquiring those locks, the repository now queries the committed `MAX(feature_runs.published_at)` across published runs and assigns the candidate `max(now, high_water + 1 microsecond)`. Naive database timestamps are interpreted as UTC before comparison. Every successful monotonic publication therefore advances publication order even for an older trading date and lower run ID. No schema, dependency, cache format, or API change was added.

### RED/GREEN evidence

The existing lower-ID late-publication regression was strengthened by freezing both publication attempts to the same instant:

```powershell
backend\venv\Scripts\python.exe -m pytest backend/tests/unit/repositories/test_market_intelligence_repo.py::test_history_generation_tracks_a_lower_run_id_published_late -q
```

RED result: **1 failed, 2 warnings**. Both `before` and `after` were `PublishedHistoryGeneration(published_at=2026-05-15T21:10:00Z, run_id=2)`, proving the generation did not advance.

GREEN result after the high-water fix: **1 passed, 2 warnings**. The late lower-ID backfill received `T + 1 microsecond`, generation advanced, the latest pointer remained on the newer trading date, and the captured pre-publication cutoff excluded the backfill.

Focused feature-store and Market Intelligence command:

```powershell
backend\venv\Scripts\python.exe -m pytest backend/tests/unit/repositories/test_feature_run_repo.py backend/tests/unit/repositories/test_market_intelligence_repo.py backend/tests/unit/services/test_market_intelligence_read_cache.py backend/tests/unit/services/test_market_intelligence_read_service.py backend/tests/unit/test_market_intelligence_endpoints.py backend/tests/unit/test_market_intelligence_mvp_endpoints.py -q
```

Result: **115 passed, 2 warnings**.

Broad Windows-safe Market Intelligence command:

```powershell
backend\venv\Scripts\python.exe -m pytest backend/tests/unit/market_intelligence backend/tests/unit/services/test_market_intelligence_read_cache.py backend/tests/unit/services/test_market_intelligence_read_service.py backend/tests/unit/test_market_intelligence_endpoints.py backend/tests/unit/test_market_intelligence_mvp_endpoints.py backend/tests/unit/repositories/test_market_intelligence_repo.py backend/tests/unit/use_cases/test_build_sector_intelligence_snapshot.py backend/tests/unit/test_market_intelligence_tasks.py -q
```

Result: **264 passed, 2 warnings**.

### Self-review

- Confirmed the high-water query runs after the existing PostgreSQL advisory lock and `FOR UPDATE` pointer read.
- Confirmed the older-backfill rule is unchanged: the run becomes published history while the pointer remains on the newer `as_of_date`.
- Confirmed the prior same-session revision rule is subsumed because the current pointer publication is part of the global committed high-water.
- Confirmed history key generation and repository cutoff remain the same immutable pair; only the publication-order guarantee was repaired.
- Confirmed ordinary feature-store publish, monotonic pointer, same-date revision, Market Intelligence history, cache, and endpoint tests remain green.

The real-Redis environment concern above is unchanged.
