# CLAUDE.md

ELT pipeline for MLB data: dlt → Parquet lake → dbt (DuckDB) → Evidence report.
**`ARCHITECTURE.md` is the spec.** Build milestones (§10) in order; every acceptance check must pass
before moving on. If it disagrees with a library's current docs, follow the docs and update the spec.
If the spec is ambiguous or wrong, propose an edit to it rather than guessing.

## Workspace layout

No monorepo manager; Docker Compose ties three toolchains together (`docker-compose.yml`).

| Path | Toolchain | Compose service |
|---|---|---|
| `src/mlb/` (pipelines in `pipelines/`, settings in `config.py`) | Python 3.12, uv (`pyproject.toml`, `uv.lock`) | `pipeline` |
| `tests/` (+ recorded `tests/fixtures/`) | pytest, ruff | `pipeline` |
| `dbt/` (staging models, macros, singular tests) | dbt-duckdb, `dbt_utils` | `dbt` |
| `reports/` | Evidence, npm (`package-lock.json`) | `reports` (node:22) |
| `.dlt/` | dlt `config.toml` (checked in), `secrets.toml` (gitignored) | — |
| `schemas/export/` | dlt schema exports; commit them so schema drift shows as a diff | — |
| `data/` | lake, warehouse, dlt state, caches, logs (gitignored, mounted at `/data`) | — |
| `docs/` | setup, usage, configuration, `roadmap/` | — |

## Commands (always via Docker Compose)

```bash
cp .env.example .env && docker compose build             # rebuild after pyproject/uv.lock/packages.yml change
docker compose run --rm pipeline pytest                  # tests (no network)
docker compose run --rm pipeline ruff check .            # lint; `ruff format .` to format
docker compose run --rm pipeline python -m mlb.pipelines.statcast [--start D --end D]
docker compose run --rm pipeline python -m mlb.pipelines.mlb_api  [--start D --end D]
docker compose run --rm dbt build                        # or `dbt run` / `dbt test`
docker compose run --rm reports ci && docker compose run --rm reports run build
```

Dependencies: Python via `uv` only (lockfile is `--frozen` in the image); dbt packages install to
`/opt/dbt_packages` at image build; never hand-edit `uv.lock` or `package-lock.json`.

## CI (`.github/workflows/ci.yml`)

On every PR and on push to `main`: build the image → ingest one known day (2025-09-01) with both
pipelines → `dbt run` → `dbt test` → build the Evidence report → publish it to `gh-pages`
(PR previews under `pr-preview/pr-<N>/`). Run tests, ruff and dbt locally before pushing.

## Shared conventions

- **Python:** type hints everywhere; small single-purpose functions; settings only from
  `config.py` (no scattered `os.environ`); `logging`, never `print()`; fail loudly — only per-day
  isolation in pipelines may catch and continue (§6.3/§6.4). Ruff: line length 100, rules E/F/W/I.
- **Tests:** new code ships with pytest tests; tests never hit the network (use fixtures).
- **dbt SQL:** lowercase everything; one CTE per logical step; a one-line comment on every
  DuckDB-specific line (§8); every model has a description and tests in its YAML.
- **Logging:** every run logs its date window, rows per resource, schema changes, and duration
  per step, to stdout and `data/logs/`.
- **Scope:** no features, abstractions or dependencies beyond the current milestone.
- **Docs:** update `README.md` and `docs/` in the same change as the code.
- **Telemetry off:** keep dlt and dbt anonymous usage stats disabled so runs stay offline.
