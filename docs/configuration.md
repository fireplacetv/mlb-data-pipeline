# Configuration Reference

All settings live in `.env` (read by Docker Compose at runtime) and `.dlt/config.toml` (static dlt configuration). No secrets are committed; `.env` is gitignored.

## Environment Variables (`.env`)

Every variable in this table is documented in `.env.example`. Copy that file, edit as needed, and check it in to git **only if you've removed all secrets**.

| Variable | Purpose | Default | Required? | Example |
|---|---|---|---|---|
| `MLB_DATA_DIR` | Root directory for all pipeline data: lake, warehouse, logs, caches | `./data` | Required | `/tmp/mlb_data` or `~/mlb_data` |
| `LOOKBACK_DAYS` | Days to re-pull on every catch-up run, to pick up Savant revisions | `4` | Optional | `7` (larger = more careful, slower) |
| `MAX_CATCHUP_DAYS` | Refuse to run catch-up if this many days have elapsed; ask for explicit backfill instead | `30` | Optional | `14` or `60` |
| `GIANTS_TEAM_ID` | Reference ID for the Giants (not yet used in filtering) | `137` | Optional | Keep as-is |
| `LOG_LEVEL` | Python logging level for all pipeline runs | `INFO` | Optional | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `UID` | Linux user ID (for file ownership in `data/`). On macOS, leave as-is | `1000` | macOS: optional; Linux: **highly recommended** | Output of `id -u` on your machine |
| `GID` | Linux group ID. On macOS, leave as-is | `1000` | macOS: optional; Linux: **highly recommended** | Output of `id -g` on your machine |

### Environment Variable Details

**`MLB_DATA_DIR`**

The pipeline stores everything here:
- `<MLB_DATA_DIR>/lake/` — Parquet files from dlt (raw data)
- `<MLB_DATA_DIR>/warehouse/` — DuckDB database file (`mlb.duckdb`)
- `<MLB_DATA_DIR>/dlt_pipelines/` — dlt pipeline state (watermarks, schemas)
- `<MLB_DATA_DIR>/cache/` — pybaseball cache (Statcast downloads)
- `<MLB_DATA_DIR>/logs/` — pipeline logs

The directory is created automatically if it doesn't exist. Parent directories must exist.

**`LOOKBACK_DAYS`**

When catching up (no `--start` / `--end` flags), the pipeline loads from `loaded_through − LOOKBACK_DAYS` through yesterday. This re-pulls recent games, which Savant revises for a few days after they're played.

Keep this small (default 4). Larger values mean more downloads and slower runs but more careful revision handling.

**`MAX_CATCHUP_DAYS`**

If more than this many days have passed since the last load, the pipeline refuses to run and prints a backfill command instead. Prevents silently skipping large gaps.

Default 30 is reasonable. Increase it if you expect long gaps between runs (e.g., off-season).

**`GIANTS_TEAM_ID`**

The MLB Stats API ID for the San Francisco Giants (`137`). Kept here for reference; not yet used in filtering, but available for future features.

**`LOG_LEVEL`**

Controls verbosity of pipeline logs. Set to `DEBUG` for detailed diagnostics when troubleshooting.

**`UID` and `GID` (Linux only)**

When the Docker container writes files to the bind-mounted `data/` directory on Linux, those files are owned by the user and group IDs specified here. If they're wrong, you'll get permission errors when trying to read the files from your host machine.

On macOS, Docker Desktop handles ownership differently, so these don't matter. Leave them at the defaults.

On Linux, set them to your user:

```bash
export UID=$(id -u) GID=$(id -g)
docker compose build  # Use the env vars above
docker compose run --rm pipeline python -m mlb.pipelines.statcast
```

Or add to your shell profile to make them permanent:

```bash
echo 'export UID=$(id -u)' >> ~/.zshrc
echo 'export GID=$(id -g)' >> ~/.zshrc
source ~/.zshrc
```

---

## dlt Configuration (`.dlt/config.toml`)

Non-secret dlt settings. Checked in to git. Secrets go in `.dlt/secrets.toml` (gitignored).

```toml
[runtime]
log_level = "WARNING"
dlthub_telemetry = false

[destination.filesystem]
layout = "{table_name}/{load_id}.{file_id}.{ext}"

[normalize.parquet_normalizer]
add_dlt_load_id = true
```

dlt reads this file from the working directory, so run pipelines from the repo root (the container's `/app`, which is the default).

**Settings:**

- **`runtime.log_level`:** dlt's own verbosity. Usually `WARNING`; set to `DEBUG` if investigating dlt issues. The pipelines' own logs follow `LOG_LEVEL`.
- **`runtime.dlthub_telemetry`:** Off, so runs and tests make no network calls beyond the data sources.
- **`destination.filesystem`:** File layout for the lake. The layout `{table_name}/{load_id}.{file_id}.{ext}` organizes Parquet files into folders per table, with one file per load. This is the default and is what staging expects. The lake location (`bucket_url`) isn't set here: the pipelines set it in code to `<MLB_DATA_DIR>/lake`.
- **`normalize.parquet_normalizer.add_dlt_load_id`:** The Statcast pipeline yields DataFrames, and dlt only adds the `_dlt_load_id` column to DataFrame/Arrow rows when this is on. Staging deduplicates on `_dlt_load_id`, so leave it on.

### Secrets (`.dlt/secrets.toml`, not checked in)

Today, the sources need no API keys:
- **Baseball Savant** is public (via pybaseball).
- **MLB Stats API** is public, no authentication required.

So `.dlt/secrets.toml` is empty or absent. When future sources or a warehouse transition require credentials, they would go here, e.g.:

```toml
[destination.postgres]
host = "db.example.com"
port = 5432
database = "mlb"
username = "pipeline"
password = "secret123"
```

Never commit this file. Add it to `.gitignore` if it doesn't exist there already.

---

## dbt Configuration

**`dbt/profiles.yml`** (checked in, env-driven):

```yaml
mlb:
  target: duckdb
  outputs:
    duckdb:
      type: duckdb
      path: "{{ env_var('MLB_DATA_DIR', '../data') }}/warehouse/mlb.duckdb"
      threads: 4
```

- **`type: duckdb`:** Use the DuckDB adapter.
- **`path`:** Location of the DuckDB database file. Reads `MLB_DATA_DIR` from `.env`; defaults to `../data` if not set.
- **`threads: 4`:** dbt parallelism. Increase on beefy machines, decrease if DuckDB complains about contention.

**`dbt/dbt_project.yml`** (defines project structure):

Staging models all:
- **Materialized as tables** (not views): fast to query, easy to rebuild.
- **In the `staging` schema.** `dbt/macros/generate_schema_name.sql` makes that the exact name; dbt's default would be `main_staging`.
- **Have descriptions and tests** in their YAML.

Other settings:
- **`packages-install-path: /opt/dbt_packages`:** `dbt_utils` is installed into the image at build time, outside the bind-mounted repo, so no `dbt deps` is needed before `dbt build`.
- **`flags: send_anonymous_usage_stats: false`:** no dbt telemetry.
- **`vars: check_freshness: false`:** the recency tests on completed loads are off by default. Pass `--vars '{check_freshness: true}'` to turn them on (see `docs/usage.md`).

**Sources** (`dbt/models/staging/*/_*__sources.yml`) read the lake directly with DuckDB's `read_parquet`, at `<MLB_DATA_DIR>/lake/<dataset>/<table>/*.parquet`. `stg_dlt__completed_loads` lists `<MLB_DATA_DIR>/lake/*/_dlt_loads/*`. Like `profiles.yml`, they read `MLB_DATA_DIR` (default `../data`, relative to `dbt/`).

---

## Python Configuration (`config.py`)

At runtime, the pipeline loads configuration from `config.py`:

```python
from pathlib import Path
import os

MLB_DATA_DIR = Path(os.getenv("MLB_DATA_DIR", "./data"))
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "4"))
MAX_CATCHUP_DAYS = int(os.getenv("MAX_CATCHUP_DAYS", "30"))
GIANTS_TEAM_ID = int(os.getenv("GIANTS_TEAM_ID", "137"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
```

Each has the default shown, so an unset variable falls back to it. `config.py` also defines fixed (non-env) settings:

- **`SCHEMA_EXPORT_DIR`:** `schemas/export/` in the repo. dlt schemas are exported here after every run and committed.
- **`SEASON_START` / `SEASON_END`:** February 15 and November 15. Days outside this range are skipped without calling the source.
- The load-window and watermark rules (`choose_window`, `next_watermark`), described in `docs/usage.md`.

The Statcast pipeline also has fixed politeness settings at the top of `src/mlb/pipelines/statcast.py`: 3 attempts per day, backoff starting at 5 seconds, and 2 seconds between days.

The pybaseball cache (used by Statcast backfills) lives in `<MLB_DATA_DIR>/cache/pybaseball/`. The pipeline sets this directory in code; pybaseball's own `PYBASEBALL_CACHE` variable isn't needed.

---

## Docker Compose Environment

`docker-compose.yml` sets these automatically for every container:

```yaml
environment:
  MLB_DATA_DIR: /data
  DBT_PROFILES_DIR: /app/dbt
  DBT_PROJECT_DIR: /app/dbt
  TZ: America/Los_Angeles
```

- **`MLB_DATA_DIR: /data`:** Inside the container, the bind-mounted `./data/` is at `/data`. Pipelines write there.
- **`DBT_PROFILES_DIR` and `DBT_PROJECT_DIR`:** dbt looks here for `profiles.yml` and `dbt_project.yml`.

The `dbt` service's entrypoint runs `mkdir -p "$MLB_DATA_DIR/warehouse"` and then `dbt` with your arguments (`build` when none are given). DuckDB won't create a missing folder for its database file, and `data/warehouse` is meant to be deletable.
- **`TZ: America/Los_Angeles`:** For consistency (MLB games are in various US timezones, but most operations treat times as Pacific). Override if needed.

You can add more variables to `.env` and they'll be picked up by `docker-compose.yml` automatically.

### The `reports` service

The data report (`reports/`, see `docs/usage.md`) runs in its own `node:22-bookworm-slim` container, not the Python image, and doesn't read `.env`. It mounts the repo at `/app`, runs as your `UID`/`GID`, and sets:

- **`HOME` and `npm_config_cache`:** `/tmp`, so npm has somewhere writable as a non-root user.
- **`SEND_ANONYMOUS_USAGE_STATS: "no"`:** turns off Evidence's usage telemetry, the same way dbt's is off.

Evidence settings live in `reports/evidence.config.yaml` (plugins, theme) and `reports/sources/warehouse/connection.yaml` (the DuckDB file, as a path relative to that folder: `../../../data/warehouse/mlb.duckdb`). To read a different file, set `EVIDENCE_SOURCE__warehouse__filename`, also relative to `reports/sources/warehouse/`, for example with `docker compose run --rm -e EVIDENCE_SOURCE__warehouse__filename=... reports run build`. The report needs no secrets.

---

## Troubleshooting Configuration

### "No such file or directory: ./data/lake"

The pipeline tried to write to `MLB_DATA_DIR` but it doesn't exist or is inaccessible. Check that:
1. `MLB_DATA_DIR` is set in `.env`.
2. The parent directory exists (e.g., `/tmp/` if you use `MLB_DATA_DIR=/tmp/mlb_data`).
3. You have write permission.

### "Permission denied" on Linux

The container created files owned by `root` or the wrong user. Usually means `UID`/`GID` weren't set:

```bash
export UID=$(id -u) GID=$(id -g)
docker compose build
docker compose run --rm pipeline python -m mlb.pipelines.statcast --start 2025-09-01 --end 2025-09-02
```

Then fix ownership:

```bash
sudo chown -R $(id -u):$(id -g) data/
```

### "LOOKBACK_DAYS must be an integer"

`LOOKBACK_DAYS` is set but not a number. Check `.env`:

```bash
# Bad:
LOOKBACK_DAYS=four

# Good:
LOOKBACK_DAYS=4
```

### dbt can't find the warehouse

`dbt/profiles.yml` references `MLB_DATA_DIR` from `.env`, but `.env` isn't loaded. Verify:
1. `.env` exists and is readable.
2. You're running `docker compose` (which reads `.env`) not `dbt` directly.
3. The path resolves correctly: `docker compose run --rm pipeline echo $MLB_DATA_DIR` should print the path.
