# MLB Data Pipeline — Architecture & Build Spec

> **How to use this document:** This file is the source of truth for building the pipeline with Claude Code. Put it at the repo root and reference it from `CLAUDE.md`. Build one milestone at a time (§10), in order. Each milestone has acceptance checks that must pass before moving on. When this doc and a library's current docs disagree on API details, follow the library docs and update this file.

---

## 1. Goal and scope

A containerized **ELT data pipeline** for MLB data:

- **Extract + Load:** `dlt` pulls from Baseball Savant (Statcast) and the MLB Stats API and lands raw data as **Parquet files** in a local lake.
- **Transform (minimal):** `dbt` builds a thin **staging layer** in **DuckDB**: typed, renamed, deduplicated, one model per raw table. No business logic.
- **Warehouse:** DuckDB. Another warehouse such as Postgres is possible someday, not planned. The design avoids unnecessary DuckDB lock-in, but a move would still mean some dbt changes (§8).

**In scope:**
- Docker + Docker Compose environment, so every user runs identical versions with one command
- Two dlt pipelines (Statcast, MLB Stats API), with catch-up and backfill modes
- dbt project with sources, staging models, and staging-level tests
- Documented commands instead of a wrapper layer: each step runs directly with `docker compose run` (pipeline modules and `dbt`), listed in a Quick Start in `README.md` with fuller reference in `docs/` (§9.1)
- Unit tests with recorded fixtures, and CI
- A `docs/` folder (§9.1): environment setup instructions, configuration reference (`.env` and dlt config/secrets), and `docs/roadmap/` with design docs for future development phases

**Out of scope:**
- Intermediate models, facts, dimensions, marts, snapshots, rolling metrics, or any other business logic
- A semantic layer, BI, dashboards, notebooks, or reports
- Scheduling and orchestration (cron, Dagster, Airflow). Every run is started by hand with the documented commands.
- Cloud hosting and multi-user access

**Done means:** on a clean machine with only Docker and git, following the README Quick Start (build, ingest two days, `dbt build`) produces tested staging tables in DuckDB, and they can be rebuilt from the lake at any time without calling the APIs again.

---

## 2. Design principles

1. **Raw data is immutable and lives in files.** dlt only appends to the lake. Nothing edits or deletes raw Parquet.
2. **The warehouse is disposable.** Deleting `data/warehouse/mlb.duckdb` and running `dbt build` must rebuild everything from the lake.
3. **Idempotent loads.** Re-running any load for any date range is always safe. Duplicates are resolved in staging, never in ingestion.
4. **Staging is thin.** Rename, cast, deduplicate, and drop junk columns. No joins beyond load-completeness filtering, no aggregations, no derived metrics.
5. **Avoid unnecessary lock-in.** Prefer `dbt_utils` and standard SQL when they're as simple as the DuckDB-specific alternative. Don't add complexity purely for SQL compatibility (§8).
6. **Extract/load is schema-agnostic.** dlt lands whatever the source sends. Schema drift is detected at load and handled in staging (§6.5).
7. **No network in tests.** Unit tests use recorded fixtures.

---

## 3. Stack

| Layer | Tool | Notes |
|---|---|---|
| Language / env | Python 3.12, `uv` | Pin versions in `pyproject.toml` / `uv.lock` |
| Dev environment | Docker + Docker Compose | One image for pipeline, dbt, and tests (§9) |
| Extract + load | `dlt` | `filesystem` destination, Parquet format |
| Statcast access | `pybaseball` | Wrapped as a dlt resource |
| MLB Stats API | `dlt` REST API source | `https://statsapi.mlb.com`, no auth |
| Raw storage | Parquet on local disk | `data/lake/` |
| Warehouse | DuckDB | `data/warehouse/mlb.duckdb` |
| Transform | `dbt-core` + `dbt-duckdb` | Staging layer only |
| Quality | dbt tests, `pytest`, `ruff` | Run by hand and in CI |

**Python dependencies** (declared in `pyproject.toml`, locked in `uv.lock`):
- Runtime: `dlt[parquet]`, `pybaseball`, `pandas`, `pyarrow`, `duckdb`, `dbt-core`, `dbt-duckdb`
- Dev (a `dev` dependency group, also installed in the image): `pytest`, `ruff`

pybaseball is lightly maintained and pins older libraries in places. M0 must confirm it installs and imports on Python 3.12 alongside current pandas and numpy. If it doesn't, pin compatible versions, or drop to Python 3.11, and record the decision in `docs/configuration.md`.

---

## 4. Architecture overview

```
 SOURCES                   EXTRACT + LOAD (dlt)              LAKE (Parquet, append-only)
 ───────                   ────────────────────              ───────────────────────────
 Baseball Savant ────────▶ statcast pipeline        ───────▶ data/lake/raw_statcast/
  (via pybaseball)          watermark + lookback (§6.6)        pitches/, _dlt_loads/
 MLB Stats API ──────────▶ mlb_api pipeline         ───────▶ data/lake/raw_mlb/
  schedule, boxscores,      REST API source                    schedule/, boxscore/,
  standings, teams,         nested JSON → child tables         boxscore__*/, standings/, ...
  rosters, people

                                   │ dbt sources: read_parquet(..., union_by_name = true)
                                   ▼
                          WAREHOUSE: data/warehouse/mlb.duckdb
                          staging.*  typed, renamed, deduplicated, 1:1 with raw tables
```

---

## 5. Repository layout

```
mlb-pipeline/
├── ARCHITECTURE.md              # this file: the current, agreed spec
├── CLAUDE.md                    # points to this file + coding conventions (§9.2)
├── README.md                    # overview + Quick Start (all commands); links into docs/
├── docs/
│   ├── setup.md                 # environment setup, first run, troubleshooting
│   ├── configuration.md         # every env var, .env, dlt config/secrets
│   ├── usage.md                 # command reference: ingest, backfill, dbt, tests, reset
│   └── roadmap/
│       ├── README.md            # index: feature, status, link
│       ├── _template.md         # template for new phase docs
│       └── phase-N-<name>.md    # one design doc per future development phase
├── pyproject.toml
├── uv.lock
├── .env.example
├── .gitignore                   # data/, .env, dbt/target/, dbt/logs/, dbt/dbt_packages/
├── Dockerfile
├── .dockerignore
├── docker-compose.yml
├── .github/workflows/ci.yml
├── .dlt/
│   └── config.toml              # dlt runtime + destination config (no secrets)
├── schemas/
│   └── export/                  # dlt schemas exported after each run, committed (§6.5)
├── src/mlb/
│   ├── __init__.py
│   ├── config.py                # env vars, paths, date-window helpers
│   └── pipelines/
│       ├── common.py            # shared dlt helpers: lake pipeline, watermark, run logging
│       ├── statcast.py          # dlt resource + pipeline
│       └── mlb_api.py           # dlt REST API source + pipeline
├── dbt/
│   ├── dbt_project.yml
│   ├── profiles.yml             # checked in; paths/credentials from env vars
│   ├── packages.yml             # dbt_utils
│   ├── models/staging/
│   │   ├── _dlt/                # stg_dlt__completed_loads
│   │   ├── statcast/
│   │   └── mlb/
│   └── tests/                   # singular data tests
├── tests/                       # pytest
│   ├── fixtures/
│   │   ├── api/                 # recorded MLB API responses
│   │   ├── statcast/            # trimmed Statcast CSV/Parquet
│   │   └── lake/                # tiny lake used by CI dbt build
│   └── test_*.py
└── data/                        # gitignored; bind-mounted to /data in containers
    ├── lake/                    # Parquet landing zone (dlt)
    ├── warehouse/               # mlb.duckdb (dbt)
    ├── dlt_pipelines/           # dlt local working state
    ├── cache/                   # pybaseball cache
    └── logs/
```

---

## 6. Extract + Load (dlt)

### 6.1 Data sources

All sources are public and need no API keys. The data is MLB's, and use is subject to MLB's terms. This project is personal and non-commercial.

| Source | What we load | Access method | Coverage |
|---|---|---|---|
| **Baseball Savant (Statcast)** | Pitch-level data: pitch type, velocity, spin, movement, location, batted-ball EV/LA, xwOBA inputs, and the plate-appearance outcome on the last pitch | `pybaseball.statcast()`, which wraps Savant's Statcast Search CSV export | 2015 → present |
| **MLB Stats API** | Schedule and game status, boxscores, standings, teams, rosters, player bios | dlt REST API source against `https://statsapi.mlb.com/api/` | 2015 → present (to match Statcast) |

Possible later sources (not in this plan): FanGraphs and Baseball Reference season stats via `pybaseball`, and the Chadwick Bureau register for player-ID crosswalks.

**Source documentation:**
- **Statcast**
  - [Statcast Search CSV documentation](https://baseballsavant.mlb.com/csv-docs): the column dictionary. **Read it before writing `stg_statcast__pitches`.** It defines `game_type` codes, `type` (B/S/X), `launch_speed_angle`, `woba_value` / `woba_denom`, and which fields are deprecated.
  - [Statcast Search UI](https://baseballsavant.mlb.com/statcast_search): for spot-checking a pull by hand
  - [pybaseball README](https://github.com/jldbc/pybaseball) and [function docs](https://github.com/jldbc/pybaseball/tree/master/docs): `statcast()`, caching (`cache.enable()`)
  - [MLB terms of use](https://www.mlb.com/official-information/terms-of-use)
- **MLB Stats API**
  - MLB publishes **no official public documentation**. Treat live responses as the spec, and record fixtures from them.
  - [MLB-StatsAPI wiki — Endpoints](https://github.com/toddrob99/MLB-StatsAPI/wiki/Endpoints): the best community reference for paths and parameters. The wiki's function pages (`schedule`, `boxscore`, `standings`, `roster`) show each endpoint's query parameters.
  - [MLB data use notice](http://gdx.mlb.com/components/copyright.txt)

The pybaseball README notes that Statcast data can change even for past seasons. That is why every catch-up re-pulls a lookback window (§6.6) and why the warehouse is rebuilt from the lake rather than patched.

### 6.2 Common settings

- Destination: `filesystem`, `bucket_url = <MLB_DATA_DIR>/lake`, file format `parquet`, default layout `{table_name}/{load_id}.{file_id}.{ext}`. Set `bucket_url` in code from `MLB_DATA_DIR`. `.dlt/config.toml` holds only static settings.
- Write disposition: **`append` for every resource.** Don't use `replace`: on the filesystem destination it deletes the table's existing files, which breaks principle 1 and would drop earlier seasons. Staging deduplicates instead.
- Each pipeline has its own `pipeline_name` and `dataset_name`. dlt syncs pipeline state to the destination (`_dlt_pipeline_state/`).
- Set `pipelines_dir` to `<MLB_DATA_DIR>/dlt_pipelines`. Containers run with `--rm`, so dlt's home-directory default would be lost after every run.
- Every row carries `_dlt_load_id`, which staging uses for deduplication. For DataFrame/Arrow input (Statcast), dlt only adds it with `[normalize.parquet_normalizer] add_dlt_load_id = true` in `.dlt/config.toml`.
- Log row counts per resource after each run (from `load_info` / `pipeline.last_trace`).

> **No merge on plain Parquet.** On the filesystem destination, `merge` silently falls back to `append`. That's intended here: staging deduplicates (§7.3).

> **Completed-load markers.** On the filesystem destination, `_dlt_loads` is not a Parquet table. dlt writes one small JSON marker file per completed load into `<dataset>/_dlt_loads/`, named with the schema name and load id. A load id with no marker didn't finish, and staging ignores its rows. Inspect a real marker file before writing the SQL that parses it, because naming and compression vary by dlt version. With dlt 1.30 the name is `<schema>__<load_id>.jsonl` (for example `raw_statcast/_dlt_loads/statcast__1790615044.628745.jsonl`).

### 6.3 Statcast pipeline — `src/mlb/pipelines/statcast.py`

- `pipeline_name="statcast"`, `dataset_name="raw_statcast"`
- Runnable directly: `python -m mlb.pipelines.statcast [--start YYYY-MM-DD --end YYYY-MM-DD]` (a small `argparse` block under `if __name__ == "__main__":`). No flags means catch up since the last load (§6.6).
- Resource `pitches`:
  - Calls `pybaseball.statcast(start_dt, end_dt)` **one day at a time** and yields one DataFrame per day. Skips empty days.
  - Enables the pybaseball cache during backfills only, stored under `<MLB_DATA_DIR>/cache/pybaseball` (set via `pybaseball.cache.config.cache_directory`) so it survives container restarts. pybaseball caches date requests for a year, so catch-up runs never use the cache: they must see Savant's revisions.
  - Declares dlt column hints only for key columns (`game_pk`, `game_date`, `batter`, `pitcher`, `at_bat_number`, `pitch_number`). Everything else is inferred (§6.5). Hints give only `data_type`, matching what pybaseball returns (dlt keeps a conflicting hint in the schema but doesn't convert DataFrame data, so a mismatched hint would misdescribe the files): the IDs are `bigint`, and `game_date` is `text`, because pybaseball's date parsing skips pandas 3 string columns. Staging casts it.
  - Natural key (documented, enforced in staging, not at load): `(game_pk, at_bat_number, pitch_number)`.
- **Date windows:** chosen by the incremental rules in §6.6: catch-up by default, or an explicit `--start` / `--end` backfill. Either way the pipeline loads one day at a time and skips dates clearly outside a season (February 15 to November 15 as a default, so late-February spring training games are kept). Note that `pybaseball.statcast()` applies its own season bounds: before March 15 for seasons after 2020 (and per-season dates through 2020), it returns nothing, so late-February games don't arrive through it today. Those days count as empty, not failed.
- Be polite to Savant: sleep briefly between days and retry with backoff. A failed day is logged and skipped, and the run exits non-zero at the end listing the failed days.
- Each run is one dlt load (all days in the window go into one load package), so re-running a range creates a second load that staging deduplicates.

### 6.4 MLB Stats API pipeline — `src/mlb/pipelines/mlb_api.py`

- `pipeline_name="mlb_api"`, `dataset_name="raw_mlb"`
- Runnable directly: `python -m mlb.pipelines.mlb_api [--start ... --end ...]`, same flags and defaults as Statcast.
- dlt REST API source with base URL `https://statsapi.mlb.com`. Verify each path and its response shape against a live call, and record the response as a fixture.
- **One day at a time, like Statcast.** A REST source run fails as a whole, so the pipeline loops over the window's days and runs `schedule` and `boxscore` for one day per iteration (building the config with that day's dates). A failed day is isolated, and the watermark rules in §6.6 apply per day. Days outside a season (§6.3) are skipped. The snapshot resources (`teams`, `standings`, `rosters`) run once, after the daily loop, and `people` runs last.
- **Each step is its own dlt load**, all under one source named `mlb_api` (so they share one schema and one source state): one load per day, one snapshot load, and one `people` load. A two-day window makes four loads. The `people` load also saves the watermark and the fetched-ID set, so neither moves unless every earlier step finished; an exception in the snapshot or `people` step fails the run with the watermark unchanged.

| Resource | Endpoint | Disposition | Notes |
|---|---|---|---|
| `schedule` | `/api/v1/schedule?sportId=1&startDate=&endDate=` | append | Select `dates[*].games[*]` so one row = one game. Key `game_pk`. |
| `boxscore` | `/api/v1/game/{game_pk}/boxscore` | append | **Final games only.** Dependent on an unloaded `final_games` resource (the same schedule call, filtered with `processing_steps`), because a filter on `schedule` itself would also drop non-Final games from the `schedule` table. Players become one child table (below). |
| `standings` | `/api/v1/standings?leagueId=103,104&season=&date=` | append | One snapshot per run, as of the window's end date (§6.6). Select `records` (one row per division); `teamRecords` becomes a child table. |
| `teams` | `/api/v1/teams?sportId=1&season=` | append | One snapshot per season touched by the window (one resource per season, `teams_<season>`, all writing the `teams` table). Staging keeps the latest per team × season. |
| `rosters` | `/api/v1/teams/{team_id}/roster?rosterType=40Man&date=` | append | One snapshot per team per run, as of the window's end date (§6.6), dependent on the end date's season of `teams` |
| `people` | `/api/v1/people?personIds=<id>,<id>,...` | append | Bios only for player IDs not fetched before (§6.6), up to 100 IDs per request (the batch form of `/api/v1/people/{id}`) |

- **Final** means `status.codedGameState` is `F` (Final) or `O` (Game Over). Postponed (`D`) and cancelled (`C`) games also report `abstractGameState = "Final"`, so that field can't be used.
- **Minimal row shaping** (`processing_steps` maps). These change structure, never values, and exist so each staging model can read one raw table without joins (§7.1):
  - `boxscore`: the API keys players by `"ID<person id>"` under `teams.away.players` and `teams.home.players`. Left as is, dlt would create a column per player per stat. The map moves both sides into one `players` list, so dlt makes one child table, `boxscore__players` (one row per player per game). Each player row gets `game_pk`, `team_id`, and `side` (`away`/`home`). The boxscore row gets `game_pk` too, which the response lacks (it comes from the parent schedule row via `include_from_parent`).
  - `standings`: each division record and each of its team records gets `as_of_date` (the requested date, which the response lacks).
  - `rosters`: each entry gets `team_id` (from the parent `teams` row) and `roster_date`.
- **Load ids on child tables.** dlt stamps `_dlt_load_id` on root rows only. The source sets the relational normalizer's root propagation (`_dlt_load_id` → `_dlt_load_id`), so every nested table carries it and staging can filter and deduplicate child tables the same way as root tables (§7.3). The setting is saved in the exported schema.
- Same incremental rules as Statcast (§6.6), with its own watermark.
- Giants `team_id` = `137` goes in config, not code. Nothing in this plan filters to the Giants yet; all teams are loaded.

### 6.5 Schema drift

The extract/load layer never fails or transforms because a source's shape changed. It lands what arrives, makes the change visible, and leaves interpretation to staging.

**Accept (dlt schema contracts left at the default, `evolve`):**
- New tables and new columns are added automatically.
- If a value can't be coerced to an existing column's type, dlt writes it to a **variant column** (for example `release_speed__v_text`) instead of failing the load. This applies to DataFrame input as well as JSON.
- Don't set contracts to `freeze` or `discard_*` on these sources. That would turn upstream drift into failed or lossy loads.
- Column hints are limited to the natural-key and date columns (§6.3). Add a hint for another column only if drift actually shows up in it (for example a column that flips between types from day to day). A hint is configuration, not transformation.
- The lake doesn't care: each Parquet file carries its own schema, and older files are never rewritten.

**Make it visible:**
- Each pipeline exports its dlt schema to `schemas/export/` after every run (the pipeline's `export_schema_path` setting), and that folder is committed. A new, removed, or retyped column shows up as a git diff after an ingest.
- Each run logs any schema change it applied (new tables, new columns, variant columns), alongside the row counts.

**Absorb it in staging:**
- Sources read with `union_by_name = true`, so files with different column sets combine by name, and columns missing from older files come back as null.
- Explicit casts in staging pin every column's type, regardless of what was inferred on a given day.
- When a variant column appears, staging folds it back in (for example `coalesce(release_speed, cast(release_speed__v_text as double))`). This happens only once one is actually seen.
- If a column that staging selects disappears from every file, `dbt build` fails loudly. That's the intended signal to update the staging model.

The schema export also answers "what changed and when" without querying the lake.

### 6.6 Incremental loading

Runs are started by hand, so the pipelines must not assume they ran yesterday. Each pipeline remembers how far it has loaded and catches up from there.

**Watermark.** Each pipeline stores `loaded_through`, the last date for which every day loaded successfully, in dlt source state (`dlt.current.source_state()`). dlt saves state together with the load (and syncs it to the lake), so the watermark only moves forward when the data it describes has actually landed. A failed load leaves it unchanged.

**Choosing the window:**

| Invocation | Window loaded | Watermark afterwards |
|---|---|---|
| No flags, watermark exists | `loaded_through − LOOKBACK_DAYS` through yesterday | Advanced to the last day before the first failed day (or to yesterday if none failed) |
| No flags, no watermark (first run) | `yesterday − LOOKBACK_DAYS` through yesterday, with a warning suggesting a backfill | As above |
| No flags, gap larger than `MAX_CATCHUP_DAYS` | Nothing. Exit non-zero and print the backfill command to run. | Unchanged |
| `--start D1 --end D2` (backfill) | Exactly `D1` through `D2` (`D2` no later than yesterday) | Advanced only if the range starts on or before `loaded_through + 1` (or no watermark exists yet) and every day succeeded. A disconnected backfill doesn't move it. |

The watermark never moves backward: a failure inside the lookback days, or a backfill of older history, leaves it where it was.

Defaults: `LOOKBACK_DAYS=4` (Savant revises recent games) and `MAX_CATCHUP_DAYS=30` (bigger gaps are deliberate backfills). Both are env vars.

**Why not dlt's cursor-based `dlt.sources.incremental`:** it filters out rows at or below the last cursor value it saw. The lookback re-pull exists precisely to reload those rows so revised data lands, and staging already deduplicates by latest load. A stored date watermark gives catch-up without dropping revisions.

**Per resource:**
- **Statcast `pitches`, MLB `schedule`, `boxscore`:** every day in the window. Boxscores only for games that are Final.
- **`standings`, `rosters`:** one snapshot as of the window's end date, not one per day. A multi-day catch-up or backfill doesn't produce historical daily rosters. That's out of scope for this plan.
- **`teams`:** one snapshot for each season the window touches, appended.
- **`people`:** the set of player IDs already fetched is kept in source state. Only new IDs from rosters and boxscores are requested. Existing bios aren't refreshed in this plan.

**Detecting gaps anyway:** a dbt test (§7.5) flags Final games that have no Statcast pitches, so a hole in the lake shows up in `dbt build` even if a watermark is wrong.

**Inspecting and resetting:** `docs/usage.md` shows how to print each pipeline's watermark and how to reset it with `dlt pipeline <name> drop --state-paths loaded_through --state-only` (never without `--state-only`, which would delete lake files), and explains what a reset triggers on the next run. Deleting `data/dlt_pipelines/` doesn't reset anything: each run calls `pipeline.sync_destination()` before reading the watermark, which restores state from the lake.

---

## 7. Transform (dbt staging layer)

### 7.1 What staging does and doesn't do

**Does:** select from one raw table, keep only rows from completed loads, deduplicate to the natural key (latest load wins), cast types, rename to snake_case, drop deprecated or junk columns, and add small row-level flags that come straight from documentation (for example `location_reference`, §11).

**Doesn't:** join raw tables to each other, aggregate, compute stats (BA, OPS, wOBA), map events to outcomes, or filter by team or game type. Those belong to a later modeling layer outside this plan.

### 7.2 Configuration

`profiles.yml` (checked in, env-driven):

```yaml
mlb:
  target: duckdb
  outputs:
    duckdb:
      type: duckdb
      path: "{{ env_var('MLB_DATA_DIR', '../data') }}/warehouse/mlb.duckdb"
      threads: 4
```

`dbt_project.yml`: all staging models `materialized: table`, schema `staging`, and `packages-install-path: /opt/dbt_packages` so installed packages live outside the bind-mounted repo (§9). A `generate_schema_name` override in `dbt/macros/` makes the schema exactly `staging` (dbt's default would be `main_staging`). dbt's anonymous usage stats are off (`flags: send_anonymous_usage_stats: false`), like dlt's telemetry. At this data size (a few million pitches per season) a full rebuild takes seconds to minutes, and it avoids incremental-logic bugs. Revisit incremental only if rebuilds become slow.

Sources point at the lake through dbt-duckdb's `external_location`. Each source's `schema` matches the dlt dataset name, so on another warehouse the same `source()` calls would resolve to real tables (§8):

```yaml
# models/staging/statcast/_statcast__sources.yml
sources:
  - name: raw_statcast
    schema: raw_statcast
    meta:
      external_location: >
        read_parquet('{{ env_var("MLB_DATA_DIR", "../data") }}/lake/raw_statcast/{name}/*.parquet',
                     union_by_name = true)
    tables:
      - name: pitches
```

`union_by_name = true` matters because columns drift across seasons (§6.5). `env_var()` renders inside source `meta` (verified with dbt-core 1.12 and dbt-duckdb 1.11).

DuckDB won't create a missing parent folder for the database file, so the Compose `dbt` service runs `mkdir -p "$MLB_DATA_DIR/warehouse"` before `dbt` (§9). That keeps "delete `data/warehouse`, then `dbt build`" working.

### 7.3 The staging pattern

Every staging model over an append-only raw table follows this shape. Deduplication is a standard-SQL `row_number()` window, which runs unchanged on DuckDB and Postgres. Don't use `dbt_utils.deduplicate`: it has no DuckDB implementation, and its default one natural-joins on every column, which silently drops any row with a null in any column (most Statcast rows).

```sql
-- models/staging/statcast/stg_statcast__pitches.sql
with completed_loads as (
    select load_id
    from {{ ref('stg_dlt__completed_loads') }}
    where dataset = 'raw_statcast'
),

raw_rows as (
    select r.*
    from {{ source('raw_statcast', 'pitches') }} as r
    inner join completed_loads as l
        on r._dlt_load_id = l.load_id
),

ranked as (
    select
        *,
        row_number() over (
            partition by game_pk, at_bat_number, pitch_number
            order by _dlt_load_id desc
        ) as load_rank
    from raw_rows
),

deduped as (
    select *
    from ranked
    where load_rank = 1
),

renamed as (
    select
        cast(game_pk as integer)                as game_pk,
        cast(game_date as date)                 as game_date,
        cast(batter as integer)                 as batter_id,
        cast(type as varchar)                   as pitch_result_type,
        cast(release_speed as double precision) as release_speed,
        -- ... every other non-deprecated column, each cast explicitly
        case
            when cast(game_year as integer) >= 2026 then 'middle_of_plate'
            else 'front_of_plate'
        end                                     as location_reference,
        cast(_dlt_load_id as varchar)           as _dlt_load_id
    from deduped
)

select * from renamed
```

Every column is cast explicitly, with standard type names (`integer`, `double precision`, `varchar`, `date`, `timestamp with time zone`, `boolean`) that DuckDB and Postgres both accept. Physical measurements are `double precision` even where Savant sends whole numbers, so a future decimal is never truncated. Values in baseball notation (innings pitched `6.1`, games back `-`) stay text. The Statcast model keeps every column in the CSV docs except the deprecated ones (§11). List columns explicitly rather than using `dbt_utils.star`, which needs a real relation and doesn't work on file-backed sources.

MLB API models select only fields the API sends for every row. Fields that appear only on some games (for example `status.reason`, present only on postponed games) would be missing from every file of a day without such a game, and `dbt build` would fail. dlt load ids are Unix timestamps with a fractional part (`1790615044.628745`). The integer part is fixed width (10 digits until 2286) and the fraction compares correctly as a string even though its length varies, so ordering them as strings gives load order.

### 7.4 Staging models

| Model | Source | Grain | Unique key (tested) |
|---|---|---|---|
| `stg_dlt__completed_loads` | `_dlt_loads` marker files, read with DuckDB `glob()` | dataset × load | `dataset, load_id` |
| `stg_statcast__pitches` | `raw_statcast.pitches` | pitch | `game_pk, at_bat_number, pitch_number` |
| `stg_mlb__games` | `raw_mlb.schedule` | game | `game_pk` |
| `stg_mlb__boxscore_batters` | `raw_mlb.boxscore__players`, rows with batting stats | player × game | `game_pk, player_id` |
| `stg_mlb__boxscore_pitchers` | `raw_mlb.boxscore__players`, rows with pitching stats | player × game | `game_pk, player_id` |
| `stg_mlb__standings` | `raw_mlb.standings__team_records` | team × as-of date | `team_id, as_of_date` |
| `stg_mlb__teams` | `raw_mlb.teams` | team × season | `team_id, season` |
| `stg_mlb__rosters` | `raw_mlb.rosters` | player × team × roster date | `player_id, team_id, roster_date` |
| `stg_mlb__people` | `raw_mlb.people` | player | `player_id` |

`stg_dlt__completed_loads` also exposes `loaded_at` (the load id converted to a timestamp) for the recency test.

Boxscore batters and pitchers are the `boxscore__players` rows whose `stats__batting__games_played` (or `stats__pitching__games_played`) is not null: the API sends an empty stats object for a player who didn't bat (or pitch). A two-way player appears in both.

Raw child-table names come from dlt's normalization (§6.4). As of M2: `boxscore__players` holds one row per player per game, with `game_pk`, `team_id`, `side`, `person__id`, and the game's stats flattened into `stats__batting__*` / `stats__pitching__*` columns (a player who didn't bat or pitch has nulls there). `standings__team_records` holds one row per team with `as_of_date` and `team__id`. Both carry `_dlt_load_id`. Other boxscore child tables (`boxscore__teams__{away,home}__{batters,pitchers,batting_order,info}`, `boxscore__officials`, `boxscore__info`, `boxscore__players__all_positions`) have no staging model in this plan. Check `schemas/export/mlb_api.schema.yaml` after a live run before writing the models.

### 7.5 Tests

Use `dbt_utils` generic tests where one fits, and singular SQL tests only where none does.

- Every model: `not_null` on its key columns, plus `unique` or `dbt_utils.unique_combination_of_columns` on its grain.
- `accepted_values` on `game_type` (codes from the Savant CSV docs) and `location_reference`.
- `dbt_utils.accepted_range` on `launch_angle` (-90 to 90) and `launch_speed` (0 to 125), with `where: "... is not null"`.
- `dbt_utils.recency` on `stg_dlt__completed_loads.loaded_at`: severity `warn` at 30 hours and `error` at 54 hours (two tests, `completed_loads_recent_warn` and `completed_loads_recent_error`). **Off by default** (var `check_freshness: false`, which sets each test's `enabled`), so offseason builds and CI's fixture data don't fail. Turn it on during the season with `dbt build --vars '{check_freshness: true}'`.
- Singular tests in `dbt/tests/`:
  - `assert_staging_rows_match_distinct_raw_keys`: for every model, staging row count equals the distinct natural-key count among raw rows from completed loads, which proves dedup is exact.
  - `assert_final_games_have_pitches`, a gap check (severity `warn`): games in `stg_mlb__games` with status Final and a regular-season or postseason `game_type`, dated on or before the newest pitch date, that have no rows in `stg_statcast__pitches`.

---

## 8. Warehouse portability (a guideline, not a requirement)

DuckDB is the warehouse. Postgres, or another warehouse, is a possibility to keep in mind, not a planned phase. The goal is that a move would mean **some code changes in the dbt layer, not a redesign**. Avoiding every DuckDB-specific line isn't a goal.

**Guidelines:**
1. **Prefer `dbt_utils`, dbt's cross-database macros, and standard SQL when they're as simple as the native SQL.** Today that's `unique_combination_of_columns`, `accepted_range`, and `recency`, deduplication with a standard `row_number()` window (§7.3), and standard type names in casts (`double precision`, not DuckDB's `double`).
2. **DuckDB-specific SQL is fine where it's the simplest option.** Examples are `read_parquet` in source config and `glob()` over load markers in `stg_dlt__completed_loads`. Keep it in the places that need it and add a one-line comment. Don't wrap it in dispatch macros or add adapter branches ahead of time.
3. **Keep the design choices that make a move cheap:** the lake as system of record, source `schema` names that match dlt dataset names, and staging models that each read one raw table.
4. **Lowercase snake_case identifiers everywhere.**

For reference only: a move would mean loading lake Parquet into the new warehouse with dlt, pointing sources at those tables, rewriting `stg_dlt__completed_loads` to read dlt's `_dlt_loads` table, and fixing whatever DuckDB-specific SQL the comments flag.

---

## 9. Environment (Docker)

**Goal:** anyone with Docker and git runs the pipeline, dbt, and tests with identical versions and no local Python setup. The container is the supported way to run the project.

**`Dockerfile`:**
- Base `python:3.12-slim` (multi-arch: Apple Silicon and x86).
- Copy the `uv` binary from `ghcr.io/astral-sh/uv`, pinned to a version tag.
- Install `git` (for `dbt deps`) and nothing else at the system level.
- `UV_PROJECT_ENVIRONMENT=/opt/venv`, then `uv sync --frozen` from `pyproject.toml` + `uv.lock` **before** copying source (layer caching). Put `/opt/venv/bin` on `PATH`. Keeping the venv outside `/app` stops the bind-mounted repo from hiding it.
- Non-root user with `UID`/`GID` build args (default 1000), so files written to the bind-mounted `data/` are owned by the host user on Linux.
- Run `dbt deps` at image build. Packages install to `/opt/dbt_packages` (the `packages-install-path` in §7.2). Installing them under `/app/dbt/` would be hidden by the bind mount of the repo, and `dbt build` would fail to find `dbt_utils`.

**`.dockerignore`:** `data/`, `.venv/`, `dbt/target/`, `dbt/logs/`, `.git/`, `__pycache__/`, `.env`.

**`docker-compose.yml`:**

```yaml
x-mlb-base: &mlb-base
  build:
    context: .
    args:
      UID: ${UID:-1000}
      GID: ${GID:-1000}
  image: mlb-pipeline:dev
  env_file: .env
  environment:
    MLB_DATA_DIR: /data
    DBT_PROFILES_DIR: /app/dbt
    DBT_PROJECT_DIR: /app/dbt
    TZ: America/Los_Angeles
  volumes:
    - .:/app          # live source code
    - ./data:/data    # lake, warehouse, dlt state, caches, logs

services:
  pipeline:           # docker compose run --rm pipeline python -m mlb.pipelines.statcast
    <<: *mlb-base

  dbt:                # raw dbt: docker compose run --rm dbt build --select staging
    <<: *mlb-base
    working_dir: /app/dbt
    # DuckDB won't create a missing folder, so make data/warehouse first (§7.2).
    entrypoint: ["sh", "-c", 'mkdir -p "$$MLB_DATA_DIR/warehouse" && exec dbt "$$@"', "dbt"]
    command: ["build"]
```

**Rules:**
- Every path comes from `MLB_DATA_DIR`. Never hard-code `./data` or `/data`.
- One-shot commands only: `docker compose run --rm`. No long-running services.
- No data or secrets in the image. `.env` is read at runtime.
- `.env.example` documents `MLB_DATA_DIR`, `LOOKBACK_DAYS`, `MAX_CATCHUP_DAYS`, `GIANTS_TEAM_ID`, `LOG_LEVEL`, and the `UID`/`GID` note for Linux.
- Only one process may write `mlb.duckdb` at a time.
- Outbound HTTPS needed: `statsapi.mlb.com`, `baseballsavant.mlb.com`, plus PyPI and the dbt package hub at build time. No inbound ports.

**Commands.** There is no CLI wrapper or Makefile. These are the commands, and they're what `README.md` and `docs/usage.md` document:

| Task | Command |
|---|---|
| Build the image | `docker compose build` |
| Ingest Statcast (catch up since last load) | `docker compose run --rm pipeline python -m mlb.pipelines.statcast` |
| Ingest Statcast (backfill) | `docker compose run --rm pipeline python -m mlb.pipelines.statcast --start 2025-04-01 --end 2025-04-30` |
| Ingest MLB Stats API | `docker compose run --rm pipeline python -m mlb.pipelines.mlb_api` (same `--start` / `--end` flags) |
| Build and test staging | `docker compose run --rm dbt build` |
| Rebuild from scratch | `docker compose run --rm dbt build --full-refresh` |
| Run only some models | `docker compose run --rm dbt build --select stg_statcast__pitches` |
| Unit tests | `docker compose run --rm pipeline pytest` |
| Lint and format check | `docker compose run --rm pipeline ruff check .` and `docker compose run --rm pipeline ruff format --check .` |
| Auto-fix and format | `docker compose run --rm pipeline ruff check --fix .` and `docker compose run --rm pipeline ruff format .` |
| Shell in the container | `docker compose run --rm pipeline bash` |
| Reset the warehouse | `rm -rf data/warehouse` (safe: rebuilt from the lake) |

Each pipeline module exits non-zero on failure and logs to stdout and `data/logs/`, with per-step durations and row counts.

**CI (GitHub Actions, on PR and on push to `main`):** `.github/workflows/ci.yml` builds the same image with `docker compose build`, runs both dlt pipelines against the live sources for one fixed day (`2025-09-01`), then runs `dbt build` on that day's data, which checks the staging column lists and tests against real responses. A pipeline whose module doesn't exist yet is skipped with a warning, so the workflow is usable from M0. Logs from `data/logs/` and `dbt/logs/` are uploaded as an artifact. Planned for M4: add `ruff check`, `ruff format --check`, `pytest`, and a `dbt build` against the fixture lake. That job copies `tests/fixtures/lake/` into `lake/` under a temporary directory and sets `MLB_DATA_DIR` to that directory, so the warehouse never lands inside `tests/`. The fixture lake covers one date and includes a duplicate pitch across two loads, an incomplete load with no marker file, and a doubleheader.

### 9.1 Documentation (`docs/`)

Docs live in the repo, in Markdown, and change in the same PR as the code they describe.

**`docs/setup.md`: getting a working environment.** Written for someone who has never seen the repo.
- Prerequisites: Docker (Desktop or Engine + Compose v2) and git. Minimum versions, and a note that no local Python is needed.
- Steps: clone → `cp .env.example .env` → edit `.env` (link to `configuration.md`) → `docker compose build` → a small first ingest (two days) → `dbt build` → how to confirm it worked (row counts, where the files are).
- Platform notes: Linux `UID`/`GID`, Apple Silicon, Windows via WSL2.
- Troubleshooting: permission errors on `data/`, DuckDB lock errors, Savant timeouts or rate limiting, stale pybaseball cache (`cache.purge()`), and resetting (deleting `data/warehouse`; how to wipe the lake and what that costs).
- Pointer to `docs/usage.md` for day-to-day commands.

**`docs/usage.md`: command reference.** Everything in the Commands table (§9), explained in depth.
- Each pipeline module: its flags, how the catch-up window and watermark work (§6.6), `LOOKBACK_DAYS` and `MAX_CATCHUP_DAYS`, how to print or reset a watermark, and what a backfill looks like (season-sized chunks, resuming after a failure, being polite to Savant).
- dbt: `build`, `--select`, `--full-refresh`, running a single test, `dbt docs generate`.
- Tests and lint: pytest and ruff (check, auto-fix, format), and running them before pushing, since CI enforces them.
- Running an update by hand: the two ingest commands (which catch up from their watermarks), then `dbt build`; where logs go; and how to check that the run worked.
- Inspecting data: opening `mlb.duckdb` read-only, and listing lake folders and load markers.

**`docs/configuration.md`: configuring the project.**
- A table of every environment variable: name, purpose, default, required or optional, and example. It must match `.env.example` exactly.
- Where each kind of setting lives: `.env` (runtime settings, read by Compose), `.dlt/config.toml` (non-secret dlt settings, checked in), `.dlt/secrets.toml` (secrets, gitignored), `dbt/profiles.yml` (reads env vars).
- Secrets handling: nothing secret is committed; `.env` and `.dlt/secrets.toml` are gitignored; dlt's env-var naming for secrets (for example `DESTINATION__POSTGRES__CREDENTIALS`). The current sources need **no API keys or tokens**, so today `.env` holds only settings. The doc should still show where credentials would go when a future source or warehouse needs them.
- How to change the data directory (`MLB_DATA_DIR`).

**`docs/roadmap/`: future development phases.** `ARCHITECTURE.md` describes what is built or agreed: this plan, which is **Phase 0: Data Pipeline**. Each later phase gets one design doc here, describing what is **proposed**. A phase can bundle several related features.
- `README.md`: an index table with phase, one-line goal, status (`proposed` / `accepted` / `in progress` / `shipped` / `dropped`), and link. Phase 0 is listed first and points to `ARCHITECTURE.md`.
- `_template.md`, which every phase doc copies:
  - **Summary:** one paragraph on what the phase delivers.
  - **Motivation:** the problem, and why this phase comes next.
  - **Scope:** features in and out of the phase.
  - **Dependencies:** which earlier phases must ship first.
  - **Design:** changes to pipelines, dbt models, Docker, config, and docs.
  - **Impact on `ARCHITECTURE.md`:** which sections change.
  - **Milestones:** ordered, each with runnable acceptance checks, in the same style as §10.
  - **Open questions**, and **Status / decision log**.
- Initial phase docs, grouping the items in §12. This grouping and order is a starting proposal to revise in the docs themselves:
  - `phase-1-modeling-layer.md`: intermediate models, facts and dimensions, rolling metrics, plus the additional sources (FanGraphs, Baseball Reference, Chadwick) that enrich them.
  - `phase-2-cloud-and-scale.md`: scheduling and orchestration (cron to start, Dagster if needed), cloud lake, and the Delta Lake table format.

  Start each at `proposed`, with Summary, Motivation, Scope, and Dependencies filled in and the rest stubbed.
- Workflow: write or update the phase doc → mark it `accepted` → build its milestones → fold the result into `ARCHITECTURE.md` → mark it `shipped`. Point Claude Code at a phase doc and one of its milestones, the same way it is pointed at a milestone here.

**`README.md`**: what the pipeline does, then a **Quick Start** that lists every command from the Commands table (§9) in the order a new user needs them (build → ingest → `dbt build` → tests), then links to `docs/setup.md`, `docs/usage.md`, `docs/configuration.md`, `docs/roadmap/README.md`, and `ARCHITECTURE.md`.

### 9.2 Coding conventions (`CLAUDE.md`)

`CLAUDE.md` points to this file, says to work one milestone at a time, and lists these conventions:
- **Python:** type hints on all functions, and small functions with one job each. Configuration comes from `config.py` (which reads env vars), never from `os.environ` scattered through the code.
- **Logging:** the `logging` module, never `print`. Every run logs its date window, row counts per resource, schema changes, and duration.
- **Errors:** fail loudly with a clear message. Don't catch and continue except for the per-day isolation in §6.3 / §6.4.
- **Tests:** new code comes with pytest tests in the same change. Tests never touch the network; they use fixtures in `tests/fixtures/`.
- **SQL:** lowercase keywords and identifiers, one CTE per step, and a one-line comment on any DuckDB-specific SQL (§8). Every model has a description and tests in its YAML.
- **Scope:** don't add features, abstractions, or dependencies beyond the current milestone. If the spec is ambiguous or wrong, say so and propose an edit to this file rather than guessing.
- **Docs:** update `README.md` and `docs/` in the same change as the code (§9.1).

---

## 10. Build milestones

Complete in order. Each milestone ends with its checks passing. All commands run through `docker compose` as listed in §9.

### M0 — Scaffold + Docker
- Repo layout (§5), `pyproject.toml` + `uv.lock`, `.gitignore`, `.env.example`, `CLAUDE.md`, `README.md`, ruff config (in `pyproject.toml`).
- `Dockerfile`, `.dockerignore`, `docker-compose.yml` (§9).
- `config.py` reads env vars and creates the `data/` subfolders. `CLAUDE.md` with the conventions in §9.2.
- `README.md` with a Quick Start, plus `docs/setup.md`, `docs/usage.md`, and `docs/configuration.md` (§9.1), covering everything that exists at this point. `docs/roadmap/README.md` and `_template.md`.
- **Done when:** `docker compose build` works from a clean clone on macOS (arm64) and Linux (x86_64); `docker compose run --rm dbt --version` shows dbt-core and dbt-duckdb; the test and lint commands pass; on Linux, files under `data/` are owned by the host user; every variable in `.env.example` appears in `docs/configuration.md`; `import pybaseball, dlt, duckdb` succeeds in the container; `docker compose run --rm dbt deps` isn't needed before `dbt build`.

### M1 — Statcast → lake
- §6.3, with a recorded one-day fixture (trimmed).
- **Done when:** `docker compose run --rm pipeline python -m mlb.pipelines.statcast --start 2025-09-01 --end 2025-09-02` writes Parquet to `data/lake/raw_statcast/pitches/`, a marker file per load, and an updated schema in `schemas/export/`. Running it twice creates a second load. Deleting `data/dlt_pipelines/` and re-running still works, with the watermark restored from the lake. pytest covers every row of the window table in §6.6 (first run, normal catch-up, over-limit gap, connected and disconnected backfills, a failed day) and the empty-day skip, without network access.

### M2 — MLB Stats API → lake
- §6.4, with recorded fixtures for each endpoint.
- **Done when:** `docker compose run --rm pipeline python -m mlb.pipelines.mlb_api --start 2025-09-01 --end 2025-09-02` produces the expected tables and child tables, with one `schedule` row per game in a single load and boxscores only for Final games. pytest covers the unnesting, the Final-only filter, snapshots taken as of the window's end date, and `people` fetching only new IDs.

### M3 — dbt staging
- `dbt_project.yml`, `profiles.yml`, `packages.yml` (dbt_utils), sources, all models in §7.4, and the tests in §7.5.
- **Done when:** `docker compose run --rm dbt build` passes all tests. The duplicate loads from M1 collapse to one row per pitch, and rows from a load without a marker are excluded. Deleting `data/warehouse` and re-running `dbt build` gives identical row counts. Any DuckDB-specific SQL carries the one-line comment required by §8.

### M4 — Operate
- A historical backfill (2015 → present) run in season-sized chunks that can resume after failure.
- CI workflow with the fixture lake (§9).
- Docs: the update-by-hand section of `docs/usage.md` and the troubleshooting section of `docs/setup.md` are complete, and the two initial phase docs (§9.1) exist at `proposed`.
- **Done when:** running the ingest commands and `dbt build` by hand succeeds end to end for yesterday, full history is in the lake, deleting `data/warehouse` and running `dbt build` rebuilds it, and CI is green on a PR. Someone new to the repo can follow `docs/setup.md` from a clean machine to a successful `dbt build` without asking questions.

Every milestone updates `docs/` for anything it adds or changes (commands, flags, variables, folders), including the README Quick Start. A milestone isn't done if the docs are stale.

---

## 11. Known gotchas (read before coding)

- **Schema drift:** pybaseball's columns and inferred types change across seasons, and sometimes day to day. Extract/load accepts it; staging absorbs it (§6.5). Check `schemas/export/` diffs after ingests.
- **Late revisions:** Savant updates recent (and occasionally older) games. That's why every catch-up re-pulls `LOOKBACK_DAYS` before the watermark (§6.6) and staging keeps the latest load.
- **Incomplete loads:** only use rows whose `_dlt_load_id` has a completed-load marker.
- **2026 measurement change:** per the Savant CSV docs, `plate_x` / `plate_z` are measured at the front of the plate through 2025 and at the middle of the plate from 2026 (to align with ABS). `sz_top` / `sz_bot` switch from operator-set values to the batter's ABS-defined zone. Staging keeps the raw values and adds `location_reference` so downstream users can't miss it.
- **Deprecated Statcast columns:** `spin_dir`, `spin_rate_deprecated`, `break_angle_deprecated`, `break_length_deprecated`, `tfs_deprecated`, `tfs_zulu_deprecated`, and `umpire` are legacy fields. Drop them in staging.
- **Doubleheaders:** `game_pk` identifies a game, not `game_date`. Staging keys on `game_pk`.
- **Pitch vs. plate appearance:** a Statcast row is a pitch. `events` is only populated on the last pitch of a plate appearance. Staging doesn't aggregate, but document this on the model for downstream users.
- **Batter vs. pitcher IDs:** Statcast has separate `batter` and `pitcher` columns, and `player_name` refers to only one of them. Staging renames them to `batter_id` / `pitcher_id`.
- **Team abbreviations:** Statcast's team abbreviations don't always match the MLB API's. Staging leaves both as-is. A mapping seed belongs to the future modeling layer.
- **DuckDB locking:** DuckDB allows one writer. Don't hold a connection to `mlb.duckdb` while the pipeline runs. For ad-hoc queries, connect with `read_only=True` between runs.

---

## 12. Future (not in this plan)

These items are grouped into development phases, each with a design doc in `docs/roadmap/` (§9.1). Details live there, not here.

- **Delta Lake table format** in dlt (`table_format="delta"`) for true merges in the lake.
- **Cloud lake:** change `bucket_url` to `s3://` / `gs://` / R2.
- **Modeling layer:** intermediate models, facts and dimensions, rolling metrics.
- **Additional sources:** FanGraphs, Baseball Reference, the Chadwick register.
- **Scheduling and orchestration:** cron for a daily run, then Dagster if that stops being enough.
- **More tooling, when the project grows:** SQL linting (sqlfluff) once the modeling layer adds real SQL volume, and pre-commit hooks once there's more than one contributor.

Not a phase, just kept in mind: **another warehouse (such as Postgres)**, via the guidelines in §8.

---

## 13. References (tooling)

Source documentation is in §6.1. When a doc and this spec disagree on API details, follow the doc and update this file. dlt docs were at v1.30 when this list was compiled.

**dlt**
- [Filesystem destination](https://dlthub.com/docs/dlt-ecosystem/destinations/filesystem): Parquet, file layout, write dispositions (`merge` → `append`), `_dlt_loads` markers, state sync
- [REST API source — basic](https://dlthub.com/docs/dlt-ecosystem/verified-sources/rest_api/basic): config, `data_selector`, paginators, dependent resources via `{resources.<parent>.<field>}`, `include_from_parent`, `processing_steps`
- [REST API source — advanced](https://dlthub.com/docs/dlt-ecosystem/verified-sources/rest_api/advanced): retries, timeouts, response actions
- [Incremental loading](https://dlthub.com/docs/general-usage/incremental-loading)
- [Schema](https://dlthub.com/docs/general-usage/schema): inference, variant columns, exporting and importing schema files
- [Schema contracts](https://dlthub.com/docs/general-usage/schema-contracts): `evolve` / `freeze` / `discard_*` modes, including for DataFrame input

**dbt + DuckDB**
- [dbt-duckdb README](https://github.com/duckdb/dbt-duckdb): profiles, [reading external files](https://github.com/duckdb/dbt-duckdb#reading-from-external-files) (`external_location`, `{name}`, function calls)
- [dbt sources](https://docs.getdbt.com/docs/build/sources) · [Cross-database macros](https://docs.getdbt.com/reference/dbt-jinja-functions/cross-database-macros)
- [dbt_utils README](https://github.com/dbt-labs/dbt-utils): `deduplicate`, `unique_combination_of_columns`, `accepted_range`, `recency`, `star` · [latest version on dbt Hub](https://hub.getdbt.com/dbt-labs/dbt_utils/latest/)
- [DuckDB: reading Parquet](https://duckdb.org/docs/current/data/parquet/overview.html) · [combining schemas](https://duckdb.org/docs/current/data/multiple_files/combining_schemas.html) · [concurrency](https://duckdb.org/docs/current/connect/concurrency)

**Environment**
- [uv in Docker](https://docs.astral.sh/uv/guides/integration/docker/)
- [Docker Compose](https://docs.docker.com/compose/)
