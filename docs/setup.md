# Environment Setup

This guide takes you from a clean machine to a working pipeline in about 10 minutes.

## Prerequisites

You need only two things:

- **Docker:** [Docker Desktop](https://www.docker.com/products/docker-desktop) (macOS/Windows) or [Docker Engine + Compose v2](https://docs.docker.com/engine/install/) (Linux)
- **git**

**No local Python setup is required.** The pipeline runs entirely in a Docker container with pinned versions of every dependency.

## First Run

### 1. Clone the repository

```bash
git clone <repo-url>
cd mlb-data-pipeline
```

### 2. Copy and configure `.env`

```bash
cp .env.example .env
```

The defaults in `.env.example` work for most users. Open `.env` and review:

- **`MLB_DATA_DIR`:** Where the pipeline stores data (lake, warehouse, logs, caches). Default `./data` is fine.
- **`LOOKBACK_DAYS` and `MAX_CATCHUP_DAYS`:** How the pipelines handle incremental loads. Defaults are good.
- **`GIANTS_TEAM_ID`:** Not yet used; kept for reference.
- **`LOG_LEVEL`:** Set to `INFO` for normal runs, `DEBUG` for troubleshooting.
- **`UID` and `GID` (Linux only):** See the note in `.env.example`. On macOS, leave as-is.

See [`docs/configuration.md`](./configuration.md) for detailed explanations of every variable.

### 3. On Linux, set your user ID

If you're on Linux, set your UID and GID before running docker-compose. This ensures files written to `data/` are owned by your user, not root:

```bash
export UID=$(id -u) GID=$(id -g)
```

You can add these to your shell profile (`~/.zshrc`, `~/.bashrc`, etc.) to keep them set permanently:

```bash
echo 'export UID=$(id -u)' >> ~/.zshrc
echo 'export GID=$(id -g)' >> ~/.zshrc
```

On macOS, this doesn't matter (Docker Desktop handles ownership differently).

### 4. Build the Docker image

```bash
docker compose build
```

This pulls the base image, installs Python 3.12, and installs all dependencies from `uv.lock`. It also runs `dbt deps` to install dbt packages. Takes 2–5 minutes on the first run.

**If pybaseball doesn't install on Python 3.12:** You may see an error about version conflicts. If this happens, `ARCHITECTURE.md` (§3) describes fallback options: pin compatible versions of pandas/numpy, or drop to Python 3.11. The error message will guide you. Update `pyproject.toml` and re-run `docker compose build`, then document your choice in `.env` with a comment.

### 5. Ingest two days of data (quick test)

```bash
# Statcast: pitch-level data from Baseball Savant
docker compose run --rm pipeline python -m mlb.pipelines.statcast --start 2025-09-01 --end 2025-09-02

# MLB Stats API: schedule, boxscores, standings, teams, rosters, people
docker compose run --rm pipeline python -m mlb.pipelines.mlb_api --start 2025-09-01 --end 2025-09-02
```

Each ingest logs the date window, row counts, and duration. Data lands in `data/lake/` as Parquet files.

### 6. Build the staging layer

```bash
docker compose run --rm dbt build
```

dbt reads the lake, deduplicates, casts types, and builds staging models in DuckDB (`data/warehouse/mlb.duckdb`). All tests run automatically, and the last line should read `Done. PASS=... ERROR=0`. A `WARN` from `assert_final_games_have_pitches` means some Final games have no Statcast pitches; with the two days above it shouldn't appear. Logs go to `dbt/logs/`.

### 7. Verify success

Check that the staging tables exist:

```bash
docker compose run --rm pipeline python -c \
  "import duckdb; db = duckdb.connect('data/warehouse/mlb.duckdb', read_only=True); print(db.execute('SELECT * FROM information_schema.tables WHERE table_schema = \\'staging\\';').fetchall())"
```

You should see nine tables, such as `stg_statcast__pitches` and `stg_mlb__games`. To see row counts:

```bash
docker compose run --rm pipeline python -c \
  "import duckdb; db = duckdb.connect('data/warehouse/mlb.duckdb', read_only=True); db.sql('select count(*) from staging.stg_statcast__pitches').show()"
```

### 8. Run tests and linting

```bash
docker compose run --rm pipeline pytest
docker compose run --rm pipeline ruff check .
docker compose run --rm pipeline ruff format --check .
```

All should pass.

---

## Troubleshooting

### Docker build fails on macOS / "image not found"

Make sure Docker Desktop is running. You'll see a `Docker is not installed or not running` error if it's not.

### `DuckDB Error: Cannot lock database file`

Only one process can write to `mlb.duckdb` at a time. If you have a read-only connection open (e.g., in a notebook), close it before running pipelines or `dbt build`.

### `FileNotFoundError: data/` doesn't exist

The pipelines create `data/` subdirectories automatically, but the `data/` folder itself should exist or be created by `mkdir data`. If using a custom `MLB_DATA_DIR`, ensure its parent directory exists.

### Statcast ingest times out or rate-limits

Baseball Savant asks for politeness. The pipeline sleeps 2 seconds between days and retries a failed day 3 times with backoff. A day that still fails is logged and skipped; the other days load, and the run exits with code `1` and lists the failed days in its last log line. Re-run just those days with `--start`/`--end`. With the cache on, days that already succeeded aren't downloaded again. A large backfill can take many hours; see `docs/usage.md`.

### `python: can't open file '/app/python'` or `No module named 'mlb'`

Your image predates the Dockerfile fix that removed its `python` entrypoint and put `/app/src` on `PYTHONPATH`. Rebuild with `docker compose build`.

### pybaseball cache is stale

Statcast backfills (runs with `--start`/`--end`) cache each day's download under `data/cache/pybaseball/` for a year. Catch-up runs don't use the cache. If you re-backfill a recent range to pick up Savant's revisions, clear the cache first:

```bash
rm -rf data/cache/pybaseball/
```

The next ingest will re-download data for the requested dates.

### Reset the warehouse

To delete the staging models and rebuild from the lake (safe, doesn't touch `data/lake/`):

```bash
rm -rf data/warehouse
docker compose run --rm dbt build
```

### Reset everything

To start fresh (deletes lake and warehouse):

```bash
rm -rf data/
docker compose run --rm dbt build  # This will fail (no lake) but is okay; see "done when" in ARCHITECTURE.md
```

Then re-run the ingest commands.

---

## Next Steps

- Read [`docs/usage.md`](./usage.md) for the full command reference.
- Read [`ARCHITECTURE.md`](../ARCHITECTURE.md) for the design and data model.
- Read [`CLAUDE.md`](../CLAUDE.md) for coding conventions if you're contributing.
