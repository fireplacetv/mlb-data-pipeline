# Command Reference

Every task in the pipeline runs via `docker compose`. This is the full reference; see `README.md` for a quick start.

## Building and Setup

### Build the Docker image

```bash
docker compose build
```

Pulls the base image, installs dependencies, runs `dbt deps`. Do this once after cloning, or when `pyproject.toml` or `dbt/packages.yml` changes.

### Shell in the container

```bash
docker compose run --rm pipeline bash
```

Useful for debugging, inspecting the environment, or running one-off commands.

---

## Data Ingestion

All ingest commands follow the same pattern: run one day at a time, with retries and logging.

### Ingest Statcast (Baseball Savant)

Catch up since the last load (incremental):

```bash
docker compose run --rm pipeline python -m mlb.pipelines.statcast
```

Backfill a specific date range:

```bash
docker compose run --rm pipeline python -m mlb.pipelines.statcast --start 2024-06-01 --end 2024-09-30
```

**Flags:**
- `--start YYYY-MM-DD`: First date to pull (inclusive).
- `--end YYYY-MM-DD`: Last date to pull (inclusive). Must be yesterday or earlier.
- Give both flags or neither. With neither, the pipeline catches up from the watermark. See "Watermarks and Incremental Loading" below.

**What a run does:**
- Pulls one day at a time with `pybaseball.statcast()`, sleeping 2 seconds between days and retrying a failed day up to 3 times with backoff (5s, then 10s).
- Skips days outside February 15 to November 15 without calling Savant, and skips days that return no pitches (off days, All-Star break).
- If a day still fails after its retries, logs it, carries on with the remaining days, loads what it got, and exits with code `1` listing the failed days. Re-run those days with `--start`/`--end`.
- Each run is **one dlt load**: running the same range twice appends a second copy of the rows with a new `_dlt_load_id`. Staging deduplicates (latest load wins).
- Backfills (`--start`/`--end`) turn on the pybaseball cache in `data/cache/pybaseball/`, so re-running a failed backfill doesn't re-download the days that already succeeded. Catch-up runs never use the cache, so they always see Savant's latest revisions. Cached days don't expire for a year: if you re-backfill a recent range to pick up revisions, clear the cache first (see "Resetting").

**Output:**
- Parquet files in `data/lake/raw_statcast/pitches/`, named `<load_id>.<file_id>.parquet`. Every row carries `_dlt_load_id`.
- One completed-load marker per load: `data/lake/raw_statcast/_dlt_loads/statcast__<load_id>.jsonl`. A load id without a marker didn't finish, and staging ignores its rows.
- dlt's own tables next to them: `_dlt_pipeline_state/` (the synced watermark) and `_dlt_version/` (schema versions).
- The dlt schema in `schemas/export/statcast.schema.yaml`. Commit it: a new, removed, or retyped column shows up as a git diff.
- Logs to stdout and `data/logs/statcast_<timestamp>.log`: the watermark, the window, rows per day, schema changes (new tables, new columns, variant columns), rows loaded per table, duration per dlt step, and the new watermark.

**Exit codes:** `0` success; `1` one or more days failed (the rest loaded); `2` refused to run (catch-up gap over `MAX_CATCHUP_DAYS`, or invalid flags).

**Duration:** A few seconds for two days, minutes to hours for a season, depending on network and Savant responsiveness.

### Ingest MLB Stats API

```bash
docker compose run --rm pipeline python -m mlb.pipelines.mlb_api
```

Or backfill:

```bash
docker compose run --rm pipeline python -m mlb.pipelines.mlb_api --start 2024-06-01 --end 2024-09-30
```

**Output:**
- Parquet files in `data/lake/raw_mlb/schedule/`, `boxscore/`, `standings/`, `teams/`, `rosters/`, `people/`, plus child tables (e.g., `boxscore__players__...`).
- Load markers in `data/lake/raw_mlb/_dlt_loads/`.
- Updated schema in `schemas/export/dlt_mlb_api_schema.json`.
- Logs to stdout and `data/logs/mlb_api_<timestamp>.log`.

---

## Watermarks and Incremental Loading

Each pipeline tracks a `loaded_through` date in dlt's source state. When you run with no flags, the pipeline:

1. **On first run:** Loads `yesterday − LOOKBACK_DAYS` through `yesterday`. Logs a suggestion to run a full backfill (e.g., `--start 2015-04-01 --end 2025-09-30`) if you want all historical data.
2. **On subsequent runs:** Loads `loaded_through − LOOKBACK_DAYS` through `yesterday`, catching up if the pipeline hasn't run in a few days.
3. **If a gap is too large:** If more than `MAX_CATCHUP_DAYS` have passed since the last load, refuses to run and prints the backfill command to use instead. Prevents silently skipping huge date ranges.

**Lookback:** Recent games are revised by Savant for a few days after they're played. The lookback re-pulls those days to get the latest data. Keep `LOOKBACK_DAYS` small (default 4).

**After the run:** the watermark moves to the last day before the first failed day (or to yesterday if none failed). It never moves backward, so a failure inside the lookback days leaves it where it was.

**Manual backfill:** Use `--start` and `--end` to load any date range explicitly. A backfill moves the watermark to its end date only if every day succeeded **and** it connects to the watermark: it starts on or before `loaded_through + 1`, or no watermark exists yet (so a first backfill sets it). A disconnected backfill (starting after `loaded_through + 1`) still lands its data but leaves the watermark unchanged.

**Where the watermark lives:** in dlt source state, saved with each load and synced to the lake (`data/lake/raw_statcast/_dlt_pipeline_state/`). `data/dlt_pipelines/` is only a local working copy: deleting it is safe, and the next run restores the watermark from the lake.

**Printing the watermark:** every run logs it (`Watermark (loaded_through): ...`). To check without running:

```bash
docker compose run --rm pipeline dlt pipeline --pipelines-dir /data/dlt_pipelines statcast info -v
```

Look for `loaded_through` under `sources`. If `data/dlt_pipelines/` was deleted, restore it from the lake first:

```bash
docker compose run --rm -e DESTINATION__FILESYSTEM__BUCKET_URL=/data/lake pipeline \
  dlt -y pipeline --pipelines-dir /data/dlt_pipelines statcast sync --destination filesystem --dataset-name raw_statcast
```

**Resetting the watermark:** deleting `data/dlt_pipelines/` does **not** reset it (it comes back from the lake). Drop it from state instead. This appends a new state record to the lake; no Parquet data is touched:

```bash
docker compose run --rm -e DESTINATION__FILESYSTEM__BUCKET_URL=/data/lake pipeline \
  dlt -y pipeline --pipelines-dir /data/dlt_pipelines statcast drop --state-paths loaded_through --state-only
```

(The pipeline sets the lake path in code, so the `dlt` CLI needs it passed as `DESTINATION__FILESYSTEM__BUCKET_URL`.) The next run with no flags behaves like a first run: it loads the last `LOOKBACK_DAYS` days and sets a new watermark. To re-establish a watermark over existing history instead, run a backfill ending where you want it; a backfill with no watermark sets it.

Never use `drop` without `--state-only`: on the filesystem destination it deletes the table's Parquet files from the lake.

---

## Transformation and dbt

### Build the staging layer

```bash
docker compose run --rm dbt build
```

Runs dbt in the container, reading from the lake and writing to the warehouse (`data/warehouse/mlb.duckdb`). All tests run automatically.

**Duration:** Seconds to minutes, depending on how much data is in the lake.

### Build from scratch (full refresh)

```bash
docker compose run --rm dbt build --full-refresh
```

Drops and rebuilds every model. Useful after major changes to the staging layer.

### Build only some models

```bash
docker compose run --rm dbt build --select stg_statcast__pitches
docker compose run --rm dbt build --select tag:critical
```

See [dbt docs on selection](https://docs.getdbt.com/reference/node-selection/syntax).

### Generate dbt docs

```bash
docker compose run --rm dbt docs generate
docker compose run --rm dbt docs serve  # In a separate terminal to see them locally
```

---

## Testing and Quality

### Run unit tests

```bash
docker compose run --rm pipeline pytest
```

Runs all tests in `tests/`. Tests use recorded fixtures and don't touch the network.

**Options:**
```bash
docker compose run --rm pipeline pytest -v                    # Verbose
docker compose run --rm pipeline pytest tests/test_config.py  # Single file
docker compose run --rm pipeline pytest -k "test_window"      # By name
```

### Lint with ruff

```bash
docker compose run --rm pipeline ruff check .
```

Check for style and correctness issues.

### Format check

```bash
docker compose run --rm pipeline ruff format --check .
```

Check that the code matches ruff's formatting standards.

### Auto-fix and format

```bash
docker compose run --rm pipeline ruff check --fix .
docker compose run --rm pipeline ruff format .
```

---

## Continuous Integration

`.github/workflows/ci.yml` runs on every pull request and on every push to `main` (i.e. on merge). On an `ubuntu-latest` runner it:

1. Writes `.env` from `.env.example` with `UID`/`GID` set to the runner user, so the container can write to the bind-mounted `data/`.
2. Runs `docker compose build`.
3. Runs both dlt pipelines against the live sources for one fixed day (`INGEST_START`/`INGEST_END` at the top of the workflow, currently `2025-09-01`). A pipeline whose module doesn't exist yet is skipped with a warning annotation.
4. Runs `dbt run --empty` (builds every model with zero rows, which checks that the SQL compiles and runs against the real lake schemas) and then `dbt test`.
5. Uploads `data/logs/` and `dbt/logs/` as the `logs` artifact, even on failure.

To reproduce CI locally:

```bash
docker compose build
docker compose run --rm pipeline python -m mlb.pipelines.statcast --start 2025-09-01 --end 2025-09-01
docker compose run --rm pipeline python -m mlb.pipelines.mlb_api --start 2025-09-01 --end 2025-09-01
docker compose run --rm dbt run --empty
docker compose run --rm dbt test
```

---

## Inspecting Data

### Query the warehouse

```bash
docker compose run --rm pipeline python -c \
  "import duckdb; db = duckdb.connect('data/warehouse/mlb.duckdb', read_only=True); \
  print(db.execute('SELECT COUNT(*) FROM staging.stg_statcast__pitches;').fetchall())"
```

Or use a shell and open DuckDB interactively:

```bash
docker compose run --rm pipeline bash
python -c "import duckdb; db = duckdb.connect('data/warehouse/mlb.duckdb', read_only=True); db.sql('SELECT * FROM staging.stg_statcast__pitches LIMIT 5;').show()"
```

### List lake folders

```bash
docker compose run --rm pipeline ls -lh data/lake/
docker compose run --rm pipeline ls -lh data/lake/raw_statcast/
docker compose run --rm pipeline find data/lake -name "*.parquet" | wc -l
```

### Check load markers

```bash
ls -la data/lake/raw_statcast/_dlt_loads/
head -1 data/lake/raw_statcast/_dlt_loads/statcast__*.jsonl
```

### Check the dlt watermark

```bash
docker compose run --rm pipeline dlt pipeline --pipelines-dir /data/dlt_pipelines statcast info -v
```

See "Watermarks and Incremental Loading" above for resetting it.

---

## Common Workflows

### A full historical ingest (first time)

1. Build the image: `docker compose build`
2. Ingest Statcast: `docker compose run --rm pipeline python -m mlb.pipelines.statcast --start 2015-04-01 --end <yesterday>`
3. Ingest MLB API: `docker compose run --rm pipeline python -m mlb.pipelines.mlb_api --start 2015-04-01 --end <yesterday>`
4. Build staging: `docker compose run --rm dbt build`
5. Run tests: `docker compose run --rm pipeline pytest`

Statcast for 10 years takes many hours (respect Savant's rate limiting). Consider breaking it into season chunks:

```bash
for year in {2015..2024}; do
  docker compose run --rm pipeline python -m mlb.pipelines.statcast --start ${year}-04-01 --end ${year}-11-01
done
```

### Daily catch-up (after historical backfill)

```bash
docker compose run --rm pipeline python -m mlb.pipelines.statcast
docker compose run --rm pipeline python -m mlb.pipelines.mlb_api
docker compose run --rm dbt build
docker compose run --rm pipeline pytest
```

All commands skip dates already loaded and pull a lookback window.

### Rebuild the warehouse (safe)

```bash
rm -rf data/warehouse
docker compose run --rm dbt build
```

The lake is never touched. dbt rebuilds all models from Parquet.

### After changing staging logic

```bash
docker compose run --rm dbt build --full-refresh
docker compose run --rm pipeline pytest
```

---

## Debugging

### Check logs

Pipeline logs go to `data/logs/` and stdout. After a run:

```bash
ls -lh data/logs/
tail -100 data/logs/statcast_*.log
```

### Enable debug logging

```bash
export LOG_LEVEL=DEBUG
docker compose run --rm pipeline python -m mlb.pipelines.statcast --start 2025-09-01 --end 2025-09-02
```

### Check dbt logs

```bash
tail -100 dbt/logs/dbt.log
```

### Inspect the lake schema

```bash
cat schemas/export/statcast.schema.yaml
git diff schemas/export/   # what changed in the last ingest
```

---

## Resetting

### Clear pybaseball cache

```bash
rm -rf data/cache/pybaseball/
```

Only backfills use the cache. Clear it before re-backfilling a recent range to pick up Savant's revisions.

### Reset the warehouse only

```bash
rm -rf data/warehouse
```

### Reset pipeline state (watermarks)

Deleting `data/dlt_pipelines/` is safe but doesn't reset anything: state is restored from the lake on the next run. To reset a watermark, drop it from state as shown in "Watermarks and Incremental Loading".

### Reset everything (lake + warehouse + state)

```bash
rm -rf data/
```

Then re-run the ingest and dbt commands.
