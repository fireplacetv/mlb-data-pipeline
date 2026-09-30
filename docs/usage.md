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

Catch up since the last load (incremental):

```bash
docker compose run --rm pipeline python -m mlb.pipelines.mlb_api
```

Backfill a specific date range:

```bash
docker compose run --rm pipeline python -m mlb.pipelines.mlb_api --start 2024-06-01 --end 2024-09-30
```

**Flags:** the same as Statcast: `--start` and `--end` together for a backfill, neither for a catch-up. The watermark is separate from Statcast's.

**What a run does:** several dlt loads, in this order:

1. **One load per day** (days outside February 15 to November 15 are skipped): the day's `schedule` (one row per game, including postponed ones) and the `boxscore` of each game that is Final (`status.codedGameState` `F` or `O`). Days are 1 second apart. dlt's HTTP client retries timeouts, connection errors, and 429/5xx responses with backoff. A day that still fails is logged and skipped, and the run carries on and exits `1` at the end, listing the failed days.
2. **One snapshot load**, as of the window's **end date**: `teams` for every season the window touches, `standings` for both leagues, and the 40-man `rosters` of every team in the end date's season. A multi-day window does not produce daily standings or rosters.
3. **One `people` load:** bios for player IDs seen in this run's boxscores and rosters that were never fetched before, up to 100 per request. Existing bios aren't refreshed. This load also saves the watermark and the set of fetched IDs, so if the snapshot or `people` step fails, the run exits non-zero with the watermark unchanged.

So a two-day backfill makes four loads (four marker files). Re-running a range appends new copies of its rows under new load ids. Staging deduplicates (latest load wins).

**Output:**
- Parquet files in `data/lake/raw_mlb/<table>/`. The main tables:

  | Table | One row per | Notes |
  |---|---|---|
  | `schedule` | game on that day | `game_pk`, `game_type`, `official_date`, `status__*`, `teams__{away,home}__*` |
  | `boxscore` | Final game | `game_pk`, team totals (`teams__{away,home}__team_stats__*`) |
  | `boxscore__players` | player × game | `game_pk`, `team_id`, `side`, `person__id`, `stats__batting__*`, `stats__pitching__*` |
  | `standings` | division × as-of date | `as_of_date`, `division__id`, `league__id` |
  | `standings__team_records` | team × as-of date | `as_of_date`, `team__id`, `wins`, `losses`, ranks, games back |
  | `teams` | team × season | `id`, `season`, `abbreviation`, `league__id`, `division__id` |
  | `rosters` | player × team × roster date | `team_id`, `roster_date`, `person__id`, `status__code` |
  | `people` | player | `id`, `full_name`, `bat_side__code`, `pitch_hand__code` |

  dlt also writes smaller child tables for the other lists in a response, for example `boxscore__teams__away__batters`, `boxscore__officials`, and `standings__team_records__records__split_records`. Child tables link to their parent row through `_dlt_parent_id`. Every table, child tables included, carries `_dlt_load_id`.
- One completed-load marker per load: `data/lake/raw_mlb/_dlt_loads/mlb_api__<load_id>.jsonl`.
- The dlt schema in `schemas/export/mlb_api.schema.yaml`. Commit it.
- Logs to stdout and `data/logs/mlb_api_<timestamp>.log`: the watermark and window, rows per table and duration of each dlt step for every load, schema changes, total rows per table, the number of new player IDs, and the new watermark.

**Exit codes:** the same as Statcast: `0` success; `1` one or more days failed (the rest loaded), or the snapshot or `people` step failed; `2` refused to run.

**Row shaping:** the pipeline changes the structure of three responses, never their values, so that staging never needs to join raw tables: boxscore players move into one list tagged with `game_pk`, `team_id`, and `side`; standings rows get `as_of_date`; roster rows get `team_id` and `roster_date`. See `ARCHITECTURE.md` §6.4.

---

## Watermarks and Incremental Loading

Each pipeline tracks a `loaded_through` date in dlt's source state. When you run with no flags, the pipeline:

1. **On first run:** Loads `yesterday − LOOKBACK_DAYS` through `yesterday`. Logs a suggestion to run a full backfill (e.g., `--start 2015-04-01 --end 2025-09-30`) if you want all historical data.
2. **On subsequent runs:** Loads `loaded_through − LOOKBACK_DAYS` through `yesterday`, catching up if the pipeline hasn't run in a few days.
3. **If a gap is too large:** If more than `MAX_CATCHUP_DAYS` have passed since the last load, refuses to run and prints the backfill command to use instead. Prevents silently skipping huge date ranges.

**Lookback:** Recent games are revised by Savant for a few days after they're played. The lookback re-pulls those days to get the latest data. Keep `LOOKBACK_DAYS` small (default 4).

**After the run:** the watermark moves to the last day before the first failed day (or to yesterday if none failed). It never moves backward, so a failure inside the lookback days leaves it where it was.

**Manual backfill:** Use `--start` and `--end` to load any date range explicitly. A backfill moves the watermark to its end date only if every day succeeded **and** it connects to the watermark: it starts on or before `loaded_through + 1`, or no watermark exists yet (so a first backfill sets it). A disconnected backfill (starting after `loaded_through + 1`) still lands its data but leaves the watermark unchanged.

**Where the watermark lives:** in dlt source state, saved with each load and synced to the lake (`data/lake/raw_statcast/_dlt_pipeline_state/`, or `data/lake/raw_mlb/_dlt_pipeline_state/` for `mlb_api`, which also keeps its fetched player IDs there as `people_fetched`). `data/dlt_pipelines/` is only a local working copy: deleting it is safe, and the next run restores the watermark from the lake.

**Printing the watermark:** every run logs it (`Watermark (loaded_through): ...`). To check without running:

```bash
docker compose run --rm pipeline dlt pipeline --pipelines-dir /data/dlt_pipelines statcast info -v
```

Look for `loaded_through` under `sources`. The commands below use the Statcast pipeline; for the MLB Stats API, replace `statcast` with `mlb_api` and `raw_statcast` with `raw_mlb`. If `data/dlt_pipelines/` was deleted, restore it from the lake first:

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

Runs dbt in the container, reading from the lake and writing to the warehouse (`data/warehouse/mlb.duckdb`). All tests run automatically. The `dbt` service creates `data/warehouse/` first if it's missing, so the warehouse can be deleted at any time and rebuilt from the lake.

**Duration:** Seconds to minutes, depending on how much data is in the lake.

**What gets built** (all tables in the `staging` schema; SQL in `dbt/models/staging/`, descriptions in each folder's `_*__models.yml`):

| Model | Built from | One row per | Key |
|---|---|---|---|
| `stg_dlt__completed_loads` | `_dlt_loads/` marker files in every lake dataset | completed dlt load | `dataset`, `load_id` |
| `stg_statcast__pitches` | `raw_statcast/pitches` | pitch | `game_pk`, `at_bat_number`, `pitch_number` |
| `stg_mlb__games` | `raw_mlb/schedule` | game | `game_pk` |
| `stg_mlb__boxscore_batters` | `raw_mlb/boxscore__players` with batting stats | player × game | `game_pk`, `player_id` |
| `stg_mlb__boxscore_pitchers` | `raw_mlb/boxscore__players` with pitching stats | player × game | `game_pk`, `player_id` |
| `stg_mlb__standings` | `raw_mlb/standings__team_records` | team × as-of date | `team_id`, `as_of_date` |
| `stg_mlb__teams` | `raw_mlb/teams` | team × season | `team_id`, `season` |
| `stg_mlb__rosters` | `raw_mlb/rosters` | player × team × roster date | `player_id`, `team_id`, `roster_date` |
| `stg_mlb__people` | `raw_mlb/people` | player | `player_id` |

Every model keeps only rows whose `_dlt_load_id` has a completed-load marker (a load that crashed midway is ignored), then keeps the latest load's row for each key. Re-ingesting a range therefore never duplicates rows in staging. Staging doesn't join raw tables, aggregate, or filter by team or game type.

**Tests** run as part of `dbt build`:
- `not_null` on key columns, and uniqueness on each model's key.
- Accepted values for Statcast `game_type` and `location_reference`, and boxscore `side`; accepted ranges for `launch_speed` (0–125) and `launch_angle` (-90 to 90).
- `assert_staging_rows_match_distinct_raw_keys`: each model has exactly one row per distinct key among raw rows from completed loads, so deduplication is exact.
- `assert_final_games_have_pitches` (warning only): Final regular-season and postseason games, up to the newest pitch date, with no Statcast pitches. A warning here means a hole in the Statcast lake; backfill the dates it lists.
- Freshness (off by default): warns if the newest completed load is over 30 hours old and fails at 54 hours. Turn it on during the season:

```bash
docker compose run --rm dbt build --vars '{check_freshness: true}'
```

### Build from scratch (full refresh)

```bash
docker compose run --rm dbt build --full-refresh
```

Drops and rebuilds every model. Useful after major changes to the staging layer.

### Build only some models

```bash
docker compose run --rm dbt build --select stg_statcast__pitches
docker compose run --rm dbt build --select stg_mlb__games+          # a model and everything downstream of it
docker compose run --rm dbt test --select assert_final_games_have_pitches   # one test
```

See [dbt docs on selection](https://docs.getdbt.com/reference/node-selection/syntax).

### Generate dbt docs

```bash
docker compose run --rm dbt docs generate
docker compose run --rm dbt docs serve  # In a separate terminal to see them locally
```

---

## Data Report

`reports/` is an [Evidence](https://github.com/evidence-dev/evidence) project: one page of scores, standings and row counts over the staging layer, for smell-testing what landed. CI builds it for every PR (see "Continuous Integration"); you can also build it or run it live from your own warehouse. It runs in the `reports` service (a `node` image), not the Python image.

Install its packages once (and again after `reports/package-lock.json` changes). They go in `reports/node_modules/`:

```bash
docker compose run --rm reports ci
```

Build the static site into `reports/build/`:

```bash
docker compose run --rm reports run build
```

Or run the dev server, which reloads as you edit `reports/pages/index.md`, at http://localhost:3000 (stop with Ctrl-C):

```bash
docker compose run --rm --service-ports reports run dev
```

**Before either:** build the warehouse with `dbt build`, and don't have dbt running at the same time. Both commands first run `evidence sources`, which opens `data/warehouse/mlb.duckdb` read-only and runs each query in `reports/sources/warehouse/` against it. To pick up new data, run the command again.

**What's on the page**, top to bottom:
- The dates loaded, and alerts for empty models or final games with no Statcast pitches.
- Scores: each game's final score (or its status, if not final), with its pitch and boxscore counts (a typical game has 250 to 350 pitches).
- Standings by division as of the last date loaded: wins, losses, winning percentage, games back and streak.
- Rows and dates per model.
- Columns that are null in every row. CI loads the same day every time, so a new entry here usually means the source renamed or dropped a field.

It shows no individual pitches or batted balls, which keeps the site small.

**Changing it:** source queries (`reports/sources/warehouse/*.sql`) read `staging.*` in DuckDB SQL; each becomes a table `warehouse.<file name>` that the page's SQL blocks query. Both builds run in strict mode, so a failing query fails the build. The page uses the Svelte-style syntax of the open-source Evidence, for example `<BarChart data={games} x=matchup y=pitches />`; see its [component docs](https://docs.evidence.dev/components/all-components). (Evidence's newer hosted product uses a different syntax and isn't used here; see `ARCHITECTURE.md` §9.3.)

**Network:** the build downloads DuckDB's Parquet extension for WebAssembly from `extensions.duckdb.org`, and so does the browser viewing the report.

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
4. Runs `dbt run` and then `dbt test` on that day's data, which checks the staging column lists and tests against real API responses (unit tests use hand-written fixtures). They're separate steps so a model error and a test failure show up separately, and a failing test doesn't stop other models from building.
5. Builds the data report (see "Data Report") if `dbt run` succeeded, even when `dbt test` failed, and uploads it as the `data-report` artifact.
6. Uploads `data/logs/` and `dbt/logs/` as the `logs` artifact, even on failure.
7. In a separate `publish-report` job, publishes the report to GitHub Pages on the `gh-pages` branch:
   - **On a PR:** to `https://<owner>.github.io/<repo>/pr-preview/pr-<N>/`. A bot comment on the PR links to it (with a QR code), updated on every push. `.github/workflows/report-preview-cleanup.yml` deletes the preview when the PR closes. PRs from forks don't get a preview.
   - **On `main`:** to `https://<owner>.github.io/<repo>/`, leaving the PR previews in place.

   After publishing, the job adds a `.nojekyll` file to the branch root if it's missing. Without it, Pages runs Jekyll, which skips folders starting with `_`, and the report loads with no styles or charts (Evidence's CSS and JS are in `_app/`).

   The job summary of `publish-report` also has the link. GitHub Pages can take a minute or two to update after the job finishes.

**One-time repository setup for the report:** after the first CI run has created the `gh-pages` branch, set **Settings → Pages → Build and deployment → Source** to **Deploy from a branch**, branch `gh-pages`, folder `/ (root)`. (Not "GitHub Actions": the preview Action pushes to the branch.) The workflows ask for write access themselves, so the default workflow permissions can stay read-only. The site is public, even for a private repository on a plan that allows Pages.

To reproduce CI locally:

```bash
docker compose build
docker compose run --rm pipeline python -m mlb.pipelines.statcast --start 2025-09-01 --end 2025-09-01
docker compose run --rm pipeline python -m mlb.pipelines.mlb_api --start 2025-09-01 --end 2025-09-01
docker compose run --rm dbt run
docker compose run --rm dbt test
docker compose run --rm reports ci
docker compose run --rm reports run build
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
docker compose run --rm pipeline ls -lh data/lake/raw_mlb/
docker compose run --rm pipeline find data/lake -name "*.parquet" | wc -l
```

### Check load markers

```bash
ls -la data/lake/raw_statcast/_dlt_loads/
head -1 data/lake/raw_statcast/_dlt_loads/statcast__*.jsonl
ls -la data/lake/raw_mlb/_dlt_loads/
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
