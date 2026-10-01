# MLB Data Pipeline

A containerized **ELT data pipeline** for MLB Statcast (Baseball Savant) and MLB Stats API data.

- **Extract + Load:** [dlt](https://dlthub.com/) pulls raw data from Baseball Savant (via [pybaseball](https://github.com/jldbc/pybaseball)) and the MLB Stats API, landing it as Parquet files in a local data lake.
- **Transform (minimal):** [dbt](https://www.getdbt.com/) builds a thin staging layer in [DuckDB](https://duckdb.org/): typed, renamed, deduplicated. One model per raw table, no business logic.
- **Warehouse:** DuckDB, local.

On a clean machine with only Docker and git, follow the Quick Start below to ingest two days of data and build the staging layer in under five minutes.

**Read [`ARCHITECTURE.md`](./ARCHITECTURE.md) for the full design and [`CLAUDE.md`](./CLAUDE.md) for coding conventions.**

---

## Quick Start

### 1. Prerequisites

- **Docker** (Desktop on macOS/Windows, or Engine + Compose v2 on Linux)
- **git**

No local Python setup needed.

### 2. Clone and configure

```bash
git clone <repo>
cd mlb-data-pipeline
cp .env.example .env
```

Edit `.env` if needed (the defaults work for most users). On Linux, set your user ID:

```bash
export UID=$(id -u) GID=$(id -g)
```

See [`docs/setup.md`](./docs/setup.md) for detailed setup instructions.

### 3. Build the Docker image

```bash
docker compose build
```

### 4. Ingest data (two days as a quick test)

```bash
# Statcast (Baseball Savant pitch-level data)
docker compose run --rm pipeline python -m mlb.pipelines.statcast --start 2025-09-01 --end 2025-09-02

# MLB Stats API (schedule, boxscores, standings, teams, rosters, people)
docker compose run --rm pipeline python -m mlb.pipelines.mlb_api --start 2025-09-01 --end 2025-09-02
```

Data lands in `data/lake/` as Parquet files. Production runs write the same lake to Cloudflare R2 instead, chosen with `S3_BUCKET_URL` and `IS_PROD` in `.env` (see [`docs/configuration.md`](./docs/configuration.md)); leave them unset to stay local.

### 5. Build and test the staging layer

```bash
docker compose run --rm dbt build
```

Nine staging models land in the `staging` schema of DuckDB at `data/warehouse/mlb.duckdb`: typed, renamed, deduplicated (latest load wins), and limited to loads that finished. Every model is tested as it builds. `dbt build` reads only the lake, so you can delete `data/warehouse` and rebuild at any time. See [`docs/usage.md`](./docs/usage.md#transformation-and-dbt) for the model list.

### 6. Run tests and linting

```bash
docker compose run --rm pipeline pytest
docker compose run --rm pipeline ruff check .
docker compose run --rm pipeline ruff format --check .
```

### 7. Look at the data (optional)

```bash
docker compose run --rm reports ci                          # once: install the report's packages
docker compose run --rm --service-ports reports run dev     # http://localhost:3000
```

An [Evidence](https://github.com/evidence-dev/evidence) page of scores, standings and row counts over the staging layer, for a quick smell test of what landed. CI builds the same report for every PR and links it from the PR. See [`docs/usage.md`](./docs/usage.md#data-report).

---

## Commands Reference

See [`docs/usage.md`](./docs/usage.md) for full details on each command.

| Task | Command |
|---|---|
| Build the image | `docker compose build` |
| Ingest Statcast (catch up since last load) | `docker compose run --rm pipeline python -m mlb.pipelines.statcast` |
| Ingest Statcast (backfill a date range) | `docker compose run --rm pipeline python -m mlb.pipelines.statcast --start YYYY-MM-DD --end YYYY-MM-DD` |
| Ingest MLB Stats API | `docker compose run --rm pipeline python -m mlb.pipelines.mlb_api` (same flags) |
| Build and test staging | `docker compose run --rm dbt build` |
| Rebuild from scratch | `docker compose run --rm dbt build --full-refresh` |
| Run only some models | `docker compose run --rm dbt build --select stg_statcast__pitches` |
| Unit tests | `docker compose run --rm pipeline pytest` |
| Lint | `docker compose run --rm pipeline ruff check .` |
| Format check | `docker compose run --rm pipeline ruff format --check .` |
| Auto-fix and format | `docker compose run --rm pipeline ruff check --fix . && docker compose run --rm pipeline ruff format .` |
| Shell in the container | `docker compose run --rm pipeline bash` |
| Reset the warehouse | `rm -rf data/warehouse` (safe: rebuilt from the lake) |
| Install report dependencies | `docker compose run --rm reports ci` |
| Build the data report | `docker compose run --rm reports run build` |
| Data report dev server | `docker compose run --rm --service-ports reports run dev` |

---

## Documentation

- **[`docs/setup.md`](./docs/setup.md):** Environment setup, first run, troubleshooting.
- **[`docs/usage.md`](./docs/usage.md):** Detailed command reference, including what CI (`.github/workflows/ci.yml`) runs on PRs and merges to `main`, and the daily production run (`.github/workflows/scheduled-ingest.yml`).
- **[`docs/configuration.md`](./docs/configuration.md):** Every environment variable and config option.
- **[`docs/roadmap/`](./docs/roadmap/):** Design docs for future development phases.
- **[`ARCHITECTURE.md`](./ARCHITECTURE.md):** Full design spec, data sources, extract/load, staging, warehouse, Docker setup.
- **[`CLAUDE.md`](./CLAUDE.md):** Coding conventions and how to work here.

---

## Tech Stack

| Layer | Tool |
|---|---|
| Language | Python 3.12, [`uv`](https://docs.astral.sh/uv/) |
| Environment | Docker + Docker Compose |
| Extract + Load | [dlt](https://dlthub.com/) with Parquet destination |
| Warehouse | [DuckDB](https://duckdb.org/) |
| Transform | [dbt-core](https://www.getdbt.com/) + [dbt-duckdb](https://github.com/duckdb/dbt-duckdb) |
| Quality | dbt tests, [pytest](https://pytest.org/), [ruff](https://docs.astral.sh/ruff/) |
| Data report | [Evidence](https://github.com/evidence-dev/evidence) (open-source static build), published to GitHub Pages |

---

## License

This project is personal and non-commercial. Data is MLB's. See [`ARCHITECTURE.md`](./ARCHITECTURE.md#61-data-sources) for data terms of use.
