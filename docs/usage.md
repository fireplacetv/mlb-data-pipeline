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
- `--end YYYY-MM-DD`: Last date to pull (inclusive).
- If neither flag is given, the pipeline catches up from the watermark (the last date with a completed load). See "Watermarks and incremental loading" below.

**Output:**
- Parquet files land in `data/lake/raw_statcast/pitches/` (one file per day per load).
- A load marker file in `data/lake/raw_statcast/_dlt_loads/` (one per completed load).
- Updated schema in `schemas/export/dlt_statcast_schema.json` (committed to git).
- Logs to stdout and `data/logs/statcast_<timestamp>.log`.

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

**Manual backfill:** Use `--start` and `--end` to load any date range explicitly. A backfill that starts before the current watermark advances it; one that starts after it (a disconnected fill) leaves the watermark unchanged.

**Resetting the watermark:** For development or if something goes wrong:

```bash
docker compose run --rm pipeline bash
cd data/dlt_pipelines/
ls -la  # See the pipeline directories

# For Statcast:
# Edit or delete statcast/1234567890.state.json to reset the watermark

# For MLB API:
# Edit or delete mlb_api/1234567890.state.json
```

Or blow it away entirely:

```bash
rm -rf data/dlt_pipelines/
docker compose run --rm pipeline python -m mlb.pipelines.statcast  # First run again
```

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
docker compose run --rm pipeline ls -la data/lake/raw_statcast/_dlt_loads/
docker compose run --rm pipeline cat data/lake/raw_statcast/_dlt_loads/statcast_*.json | head -1
```

### Check the dlt watermark

```bash
docker compose run --rm pipeline bash
cd data/dlt_pipelines/statcast/
ls -la  # Find the .state.json file
cat 1234567890.state.json | python -m json.tool | grep loaded_through
```

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
docker compose run --rm pipeline python -c \
  "import json; schema = json.load(open('schemas/export/dlt_statcast_schema.json')); \
  print(json.dumps(schema, indent=2))"
```

---

## Resetting

### Clear pybaseball cache

```bash
rm -rf data/cache/
```

### Reset the warehouse only

```bash
rm -rf data/warehouse
```

### Reset pipeline state (watermarks)

```bash
rm -rf data/dlt_pipelines/
```

Next run will start fresh (and on first run, will ask you to backfill if no dates are given).

### Reset everything (lake + warehouse + state)

```bash
rm -rf data/
```

Then re-run the ingest and dbt commands.
