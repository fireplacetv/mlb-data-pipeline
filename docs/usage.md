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
- `--chunk-days [N]`: automatic backfill instead of catch-up — loads `N` days (or `BACKFILL_CHUNK_DAYS` if `N` is omitted) from `backfilled_through`, then stops. Can't be combined with `--start`/`--end`. See "Automatic Backfill" below.

**What a run does:**
- Pulls one day at a time with `pybaseball.statcast()`, sleeping 2 seconds between days and retrying a failed day up to 3 times with backoff (5s, then 10s).
- Skips days outside February 15 to November 15 without calling Savant, and skips days that return no pitches (off days, All-Star break).
- If a day still fails after its retries, logs it, carries on with the remaining days, loads what it got, and exits with code `1` listing the failed days. Re-run those days with `--start`/`--end`.
- Each run is **one dlt load**: running the same range twice appends a second copy of the rows with a new `_dlt_load_id`. Staging deduplicates (latest load wins).
- Backfills (`--start`/`--end`) turn on the pybaseball cache in `data/cache/pybaseball/`, so re-running a failed backfill doesn't re-download the days that already succeeded. Catch-up runs never use the cache, so they always see Savant's latest revisions. Cached days don't expire for a year: if you re-backfill a recent range to pick up revisions, clear the cache first (see "Resetting").

**Output** (paths shown for the default local lake; with `S3_BUCKET_URL` set, the same layout lands in that R2 bucket instead, see [`docs/configuration.md`](./configuration.md)):
- Parquet files in `data/lake/raw_statcast/pitches/`, named `<load_id>.<file_id>.parquet`. Every row carries `_dlt_load_id`.
- One completed-load marker per load: `data/lake/raw_statcast/_dlt_loads/statcast__<load_id>.jsonl`. A load id without a marker didn't finish, and staging ignores its rows.
- dlt's own tables next to them: `_dlt_pipeline_state/` (the synced watermark) and `_dlt_version/` (schema versions).
- The dlt schema in `schemas/export/statcast.schema.yaml`. Commit it: a new, removed, or retyped column shows up as a git diff.
- Logs to stdout and `data/logs/statcast_<timestamp>.log`: the lake destination, the watermark, the window, rows per day, schema changes (new tables, new columns, variant columns), rows loaded per table, duration per dlt step, and the new watermark.

**Exit codes:** `0` success; `1` one or more days failed (the rest loaded); `2` refused to run (catch-up gap over `MAX_CATCHUP_DAYS`, invalid flags, or a lake setting that doesn't add up: see `S3_BUCKET_URL` in [`docs/configuration.md`](./configuration.md)).

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

**Flags:** the same as Statcast: `--start` and `--end` together for a backfill, neither for a catch-up, or `--chunk-days [N]` for an automatic backfill (see "Automatic Backfill" below). The watermark and the `backfilled_through` mark are both separate from Statcast's.

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
- Logs to stdout and `data/logs/mlb_api_<timestamp>.log`: the lake destination, the watermark and window, rows per table and duration of each dlt step for every load, schema changes, total rows per table, the number of new player IDs, and the new watermark.

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

**Where the watermark lives:** in dlt source state, saved with each load and synced to the lake (`data/lake/raw_statcast/_dlt_pipeline_state/`, or `data/lake/raw_mlb/_dlt_pipeline_state/` for `mlb_api`, which also keeps its fetched player IDs there as `people_fetched`). `data/dlt_pipelines/` is only a local working copy: deleting it is safe, and the next run restores the watermark from the lake. An automatic `--chunk-days` backfill's progress lives alongside it in the same state, under a separate key, `backfilled_through` — see "Automatic Backfill" below for why it isn't `loaded_through` itself.

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
- When it was built and from which commit (a PR's head commit). CI passes these in as `VITE_REPORT_BUILT_AT` and `VITE_REPORT_GIT_SHA`. The SHA isn't a link: the build's link check treats a link to github.com as a page inside the site and fails; a local build says it has neither.
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
3. Runs both dlt pipelines against the live sources for one fixed day: `2025-09-01`, unless the `CI_INGEST_DATE` repository variable is set (see "Changing the CI date" below). A pipeline whose module doesn't exist yet is skipped with a warning annotation.
4. Runs `dbt run` and then `dbt test` on that day's data, which checks the staging column lists and tests against real API responses (unit tests use hand-written fixtures). They're separate steps so a model error and a test failure show up separately, and a failing test doesn't stop other models from building.
5. Builds the data report (see "Data Report") if `dbt run` succeeded, even when `dbt test` failed, and uploads it as the `data-report` artifact.
6. Uploads `data/logs/` and `dbt/logs/` as the `logs` artifact, even on failure.
7. In a separate `publish-report` job, publishes the report to GitHub Pages on the `gh-pages` branch:
   - **On a PR:** to `https://<owner>.github.io/<repo>/pr-preview/pr-<N>/`. A bot comment on the PR links to it, updated on every push. `.github/workflows/report-preview-cleanup.yml` deletes the preview when the PR closes. PRs from forks don't get a preview.
   - **On `main`:** to `https://<owner>.github.io/<repo>/`, leaving the PR previews in place.

   After publishing, the job adds a `.nojekyll` file to the branch root if it's missing. Without it, Pages runs Jekyll, which skips folders starting with `_`, and the report loads with no styles or charts (Evidence's CSS and JS are in `_app/`).

   The job summary of `publish-report` also has the link. GitHub Pages can take a minute or two to update after the job finishes.

**One-time repository setup for the report:** after the first CI run has created the `gh-pages` branch, set **Settings → Pages → Build and deployment → Source** to **Deploy from a branch**, branch `gh-pages`, folder `/ (root)`. (Not "GitHub Actions": the preview Action pushes to the branch.) The workflows ask for write access themselves, so the default workflow permissions can stay read-only. The site is public, even for a private repository on a plan that allows Pages.

**Changing the CI date:** set a repository variable named `CI_INGEST_DATE` to a day in `YYYY-MM-DD` form under **Settings → Secrets and variables → Actions → Variables**. Every later CI run, on PRs and on `main`, loads that day instead of `2025-09-01`, and the report shows it. Delete the variable to go back to the default. Pick an in-season day whose games are all final; an off day loads no games, and the report flags the empty models. A malformed date fails the ingest step.

To reproduce CI locally (with your date in place of `2025-09-01` if you set `CI_INGEST_DATE`):

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

## Scheduled Runs and GitHub Actions

`.github/workflows/scheduled-ingest.yml` runs the production pipeline daily with no manual intervention (`docs/roadmap/phase-2-cloud-and-scale.md`, P2M2): catch up both pipelines against the R2 lake, rebuild the warehouse, and upload it to R2. It does not build or publish the data report — that stays CI-only, built from CI's own fixed-day data (see "Data Report" and "Continuous Integration" above), not from production data.

**Schedule:** `0 2 * * *` (2 AM UTC), every day of the year. Off-season days load zero games — the pipelines already skip dates outside the season (`SEASON_START`/`SEASON_END`) — so nothing special is needed to pause it.

**What it does, on an `ubuntu-latest` runner:**

1. Writes `.env` from `.env.example`, with `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` set from the repository secrets of the same name, `S3_BUCKET_URL` from the repository variable of the same name, and `IS_PROD=true`. This is the only workflow that writes to R2; CI (`ci.yml`) always uses the local lake.
2. Runs `docker compose build`, then both pipelines with no flags (catch-up from the watermark stored in the R2 lake) — or with `--start`/`--end` when the run was triggered manually with those inputs (see "Manual trigger and backfills" below). Both pipelines keep the same per-day politeness (sleep between days, retry with backoff) on every invocation, scheduled or manual: it's hardcoded in `src/mlb/pipelines/statcast.py` and `mlb_api.py` (§6.3/§6.4), not something this workflow configures, so a manual backfill gets exactly the same source-friendly pacing as a daily catch-up.
3. Runs `docker compose run --rm dbt build`, which reads the R2 lake and writes the warehouse locally (DuckDB has no server mode to write to remotely; see `docs/roadmap/phase-2-cloud-and-scale.md`).
4. Uploads `data/warehouse/mlb.duckdb` to the R2 bucket as a single object (`aws s3 cp --endpoint-url ...`), so the built warehouse persists past the runner (the `aws` CLI is preinstalled on `ubuntu-latest`; no R2 read access is needed to use it).
5. Logs the box scores of every game on the last day the MLB Stats API step loaded (`python -m mlb.box_scores`, see "Show box scores" below), for a quick visual check of the run. It runs even if an earlier step failed.
6. Uploads `data/logs/` and `dbt/logs/` as the `scheduled-ingest-logs` artifact, even on failure.

A failed run shows red in the Actions tab and in its job summary; there's no separate notification channel today (open question in the phase doc).

The `scheduled-ingest` concurrency group keeps runs from overlapping: a manual dispatch while the daily cron is still running (or a long backfill still in progress when the next day's run fires) queues instead of racing on the same R2 watermark.

**One-time repository setup:** in **Settings → Secrets and variables → Actions**, add secrets `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`, and a **variable** (not a secret — it names no credential, only the bucket) `S3_BUCKET_URL`, each from the R2 bucket and token described in [`docs/setup.md`](./setup.md#optional-cloud-lake-on-cloudflare-r2). Without these, the workflow fails at the first pipeline step with the same `LakeConfigError` message you'd see running locally with a bad `.env`.

**Manual trigger and backfills:** run the workflow from the GitHub UI (Actions → Scheduled ingest → Run workflow), optionally with `start` and `end` inputs (`YYYY-MM-DD`) to run a one-off backfill against R2 instead of a catch-up. Leave both empty for an ad hoc catch-up run outside the schedule.

**Checking it ran:** the Actions tab lists each run; open one to see per-step logs (the **Box scores (last day loaded)** step shows what the run landed for its final day), or download the `scheduled-ingest-logs` artifact for the same `data/logs/` and `dbt/logs/` files a local run would produce.

---

## Automatic Backfill

`.github/workflows/backfill.yml` steadily backfills history (`BACKFILL_START`, default `2015-04-01`, through yesterday) into the R2 lake on its own daily schedule, with no manual intervention (`docs/roadmap/phase-2-cloud-and-scale.md`, P2M4) — the automated version of the "full historical ingest" chunking in "Common Workflows" below.

**Why this needs its own flag and its own mark, not just a long `--start`/`--end` run left to a timeout:**
- **Statcast** does the whole requested window as one dlt load; the watermark is only written after every day in it has been attempted. If GitHub killed a multi-month run at the timeout, nothing would be committed — no data (no completed-load marker, so staging ignores anything that landed) and no watermark — so the next firing would restart from scratch, forever re-downloading the same days from Savant.
- **MLB Stats API** does commit each day's data as it goes, but the watermark only advances in the final `people` step, after the whole window's days, snapshots, and people load have finished. A kill partway through would leave `loaded_through` unmoved, so the next firing would re-request the same days again — wasteful, and no faster than a short run.
- Sizing each firing to one bounded chunk sidesteps both problems: the chunk finishes and commits normally, well inside the workflow's timeout, so every firing is a real, permanent step forward.

**`--chunk-days [N]`** runs this instead of a catch-up or a manual backfill: it loads `N` days (or `BACKFILL_CHUNK_DAYS`, default 30, if `N` is omitted) starting the day after `backfilled_through`, or from `BACKFILL_START` if that mark doesn't exist yet, then stops. It can't be combined with `--start`/`--end`.

**Why `backfilled_through` is a separate mark, not `loaded_through`:** the watermark never moves backward (see "Watermarks and Incremental Loading" above) — that's what makes a disconnected backfill safe. But it also means that once the daily catch-up has run even once, `loaded_through` sits at yesterday, and a backfill chunk into history is permanently "disconnected" from it: `next_watermark` would correctly refuse to move `loaded_through` backward in time, and a backfill tracked through that key would never make visible progress again. Tracking backfill progress through its own `backfilled_through` key (next to `people_fetched`'s precedent in `mlb_api`'s state) lets `backfill.yml` and `scheduled-ingest.yml` run independently, on their own schedules, without either resetting the other's progress. Chunks are always contiguous (each starts the day after the last one ended, including across the off-season), which is what keeps `next_watermark`'s connectedness check passing and `backfilled_through` advancing every chunk that fully succeeds.

**If a day inside a chunk fails:** the whole chunk's `backfilled_through` mark holds at its prior value (the same "don't move on any failure" rule a manual backfill follows), the run exits `1`, and the next firing — scheduled or manual — retries the exact same chunk. No manual `--start`/`--end` re-run is needed.

**Once the backfill reaches yesterday:** a firing logs `Backfill complete: reached yesterday (...); nothing to do.` and exits `0` having loaded nothing. The schedule keeps firing daily after that, but every run is a fast no-op — there is currently no step that disables the schedule once it's done; see "Open Questions" in the Phase 2 roadmap doc.

**Concurrency:** `backfill.yml` shares `scheduled-ingest`'s concurrency group — both write the same R2 lake and dlt pipeline state, which assumes a single writer. A long chunk can push that day's catch-up out of the queue; the next day's catch-up self-heals via `LOOKBACK_DAYS`. It does not run `dbt build` or upload the warehouse: `scheduled-ingest.yml` already rebuilds the warehouse from the lake daily, so repeating that on every chunk would be pure waste and would hold the shared slot longer.

**Tuning the pace:** the recurring daily schedule always uses `BACKFILL_CHUNK_DAYS` (`docs/configuration.md`, default 30), changed by editing that default in `.env.example` and committing — there's no repository variable for it, since the schedule has no per-run inputs. For a one-off run instead, a manual trigger's `chunk_days` input overrides it for that run only, with no commit needed (see "Manual trigger" below). Smaller chunks make slower overall progress but a smaller blast radius per firing; larger chunks finish the backfill sooner but hold the shared concurrency slot longer per run.

**One-time repository setup:** none beyond `scheduled-ingest.yml`'s — the same `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `S3_BUCKET_URL` repository secrets/variable are reused.

**Manual trigger:** run the workflow from the GitHub UI (Actions → Automatic backfill → Run workflow), optionally with a `chunk_days` input to load a different number of days than `BACKFILL_CHUNK_DAYS` for that one run — for example a larger number to speed through a backfill faster than one default-sized chunk a day. Leave it empty to use the default.

**Checking it ran:** the Actions tab lists each run; open one for per-step logs (the **Box scores (last day loaded)** step shows the box scores of the chunk's final in-season day, or "nothing to show" for an off-season-only chunk), or download the `backfill-logs` artifact for the same `data/logs/` files a local run would produce.

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

### Show box scores

```bash
docker compose run --rm pipeline python -m mlb.box_scores                    # last day the last mlb_api run loaded
docker compose run --rm pipeline python -m mlb.box_scores --date 2025-09-01  # any day in the lake
```

Logs a box score for each Final game on the day — line score (R/H/E), then each team's batting (AB R H RBI BB SO HR) and pitching (IP H R ER BB SO HR NP) lines — read back from the lake (local or R2, whichever `.env` points at), not the API, so it shows what was actually ingested. When a day was loaded more than once (e.g. by `LOOKBACK_DAYS`), it uses that day's latest completed load. Without `--date`, it shows the last day the most recent `mlb_api` run loaded, which that run records in `data/mlb_api_last_day.txt`; if that run loaded no in-season day, it logs that there's nothing to show and exits `0`. Both scheduled workflows run it as their last step before uploading logs.

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

In production, `.github/workflows/backfill.yml` does this by itself, a chunk at a time, with no manual intervention — see "Automatic Backfill" above. The steps below are for a manual, local, or one-off run.

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
