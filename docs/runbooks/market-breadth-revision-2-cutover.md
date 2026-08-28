# Market breadth revision 2 cutover

This runbook replaces all legacy breadth history with the canonical revision-2
dataset. The build is performed in `market_breadth_rebuild`; readers continue
to use only revision-2 rows in `market_breadth` and therefore never mix old and
new formulas.

## Preconditions

- Deploy the schema and application code that understands revision 2.
- Confirm daily OHLCV caches, point-in-time universe history, and historical FX
  observations cover the rebuild period plus at least 252 trading sessions of
  warm-up.
- Review manually added universe rows. The classification migration marks them
  non-common by default; explicitly confirm any manual common equities before
  rebuilding breadth.
- Choose the first date that should be publicly available after cutover.
- Run this from `backend` with the production virtual environment activated.

## 1. Upgrade and back up

```bash
alembic upgrade head
pg_dump --format=custom --table=market_breadth "$BREADTH_DATABASE_URL" \
  --file=market_breadth_before_revision_2.dump
```

Keep the dump until the post-cutover monitoring window is complete.

## 2. Build the shadow history

Omit `--market` for the cutover build so every breadth-enabled market is staged.
Repeated `--market` options may be used for diagnosis, but a selective build is
marked partial and cannot validate or activate the full-table replacement.

```bash
python -m app.scripts.rebuild_market_breadth build \
  --start-date 2024-01-01 \
  --end-date 2026-08-25

python -m app.scripts.rebuild_market_breadth validate \
  > breadth-revision-2-validation.json
```

Validation must exit zero and report `"valid": true`. Investigate any missing
date, signature, denominator reconciliation, non-finite value, or revision
error. The build stores its exact market/date manifest beside the staging table;
standalone validation requires the staged rows to match it exactly. Re-running
`build` recreates only the staging data and manifest; it does not modify the
live table.

## 3. Pause writers

Stop Celery beat and prevent the following task names from starting. Wait for
already-running instances and daily market pipelines to finish:

- `app.tasks.breadth_tasks.calculate_daily_breadth`
- `app.tasks.breadth_tasks.calculate_daily_breadth_with_gapfill`
- `app.tasks.breadth_tasks.backfill_breadth_data`
- `app.tasks.breadth_tasks.calculate_market_exposure`
- `app.tasks.breadth_tasks.backfill_market_exposure`
- `app.tasks.daily_market_pipeline_tasks.queue_daily_market_pipeline`
- local/background runtime bootstrap workflows, which enqueue breadth and
  exposure stages

Do not activate while any of these writers can commit.

## 4. Activate atomically

Run validation once more after writers are quiet, then activate with the
explicit replacement confirmation:

```bash
python -m app.scripts.rebuild_market_breadth validate
python -m app.scripts.rebuild_market_breadth activate --confirm-replace
```

Activation locks the live table, staging table, and build manifest, revalidates
the exact coverage under that lock, deletes the live breadth rows, copies the
explicitly named columns from staging, verifies the row count and revision, and
commits as one transaction. The staging data and manifest are retained.

## 5. Rebuild derived outputs

With breadth writers still paused:

1. Backfill market exposure for the same date range because exposure consumes
   daily breadth.
2. Republish breadth UI snapshots for every supported market.
3. Regenerate static-site breadth artifacts.
4. Clear any external response/CDN cache containing breadth bootstrap payloads.
5. Deploy/restart API, worker, and frontend processes on the revision-2 code.
6. Resume Celery workers and beat.

## 6. Monitor

For at least two normal market closes, verify:

- `/api/v1/breadth/current` returns `calculation_revision: 2` for each market;
- live and static latest dates match;
- advancing + declining + unchanged equals its eligible denominator;
- T2108 is between 0% and 100%;
- StockBee daily counts do not exceed their eligible denominator;
- daily writers continue producing revision-2 rows;
- exposure, digest, watchlist stewardship, stock regime, and copilot responses
  are present and use the corrected latest date.

## Rollback

Pause the same writers. Restore the backup into a separate table first, inspect
it, then replace `market_breadth` in one database transaction. Roll back the
application deployment to code that can read the restored dataset, regenerate
exposure and published snapshots, and resume writers. Do not restore directly
over a live table without the inspection step.

## Delayed cleanup

After the monitoring and rollback windows are complete:

```bash
python -m app.scripts.rebuild_market_breadth cleanup
```

Cleanup drops only `market_breadth_rebuild` and its build manifest; the live
revision-2 table is not changed. The staging data is not recoverable after this
command except by running the shadow build again.
