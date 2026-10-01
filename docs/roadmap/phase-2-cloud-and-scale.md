# Phase 2 — Cloud and Scale

**Status:** in progress

## Summary

Move the data lake to Cloudflare R2 for production (with local filesystem preserved for CI testing), add daily automated runs via GitHub Actions, and implement secrets management with GitHub Actions Secrets. Enable persistent cloud storage and reliable scheduled updates without manual intervention.

## Motivation

Phase 0 delivers a working pipeline but requires manual runs and local storage. Phase 2 solves two problems: (1) persistent, shared cloud storage so Phase 1's modeling layer can build on stable, automatically-refreshed data, and (2) daily scheduled runs so the pipeline operates without manual intervention. This unlocks Phase 1's value: Phase 1 models are far more interesting when data is fresh and accessible without re-downloads.

## Scope

### In scope
- **Cloud lake:** dlt destination changes to R2 (Cloudflare R2) for production; local filesystem preserved for CI/testing.
- **Orchestration:** GitHub Actions for daily scheduled runs (cron via Actions workflow).
- **Secrets management:** GitHub Actions Secrets for R2 credentials (access key, secret key).
- **Flexibility:** Environment variables (`BUCKET_URL`, `IS_PROD`) allow swapping storage per environment (R2 in prod, local in CI) without code changes.
- **DuckDB warehouse:** the warehouse stays env-driven the same way the lake does, not a shared cloud database. In CI, `mlb.duckdb` is built and read fully locally under `data/warehouse/` — no network, per the project's no-network-in-tests rule. In the GitHub Actions prod run, `dbt build` still runs against a local `.duckdb` file on the runner (DuckDB has no server mode to write to remotely) — rebuilt from the R2 lake each run, per principle 2 (the warehouse is disposable). After `dbt build`, the workflow uploads that file to the R2 bucket as a single object (e.g. `aws s3 cp --endpoint-url <r2-endpoint>`), so the built warehouse persists past the ephemeral runner and is available for the Evidence report build and any other consumer, without needing R2 read access itself. This stays single-writer, one build at a time; no concurrent-access warehouse is introduced.

### Out of scope
- Delta Lake table format (deferred; Parquet + staging dedup remains sufficient).
- Multi-user access or shared cloud warehouse (Postgres, Snowflake).
- Data governance, lineage, or cataloging tools.
- BI tools or dashboards (separate phase).
- Cost optimization or monitoring.

## Dependencies

Requires Phase 0 (pipeline and staging) to be complete. Phase 1 (modeling layer) is independent and can proceed in parallel.

## Design

### Changes to pipelines

- **dlt destination config:** move from hardcoded `filesystem` with local paths to environment-driven config (built in P2M1).
  - `BUCKET_URL` env var controls destination: empty (the default) for the local lake at `<MLB_DATA_DIR>/lake` (dev, CI), `file://<path>` for another local folder, `s3://bucket/path` pointing to R2 for prod. Other schemes are refused.
  - dlt's S3-compatible destination config (R2 uses S3 API) takes `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` env vars, plus `R2_ACCOUNT_ID` for the endpoint `https://<account id>.r2.cloudflarestorage.com` (region `auto`).
  - `IS_PROD` gates it both ways: an `s3://` lake needs `IS_PROD=true`, and `IS_PROD=true` with a local lake is refused. Either mismatch, or missing keys, exits `2` with a message naming the setting.
  - `config.py` reads all of these (`resolve_lake`) and `pipelines/common.py` builds the dlt destination and credentials in code, the same way `bucket_url` was already set in code. `.dlt/config.toml` doesn't change: no new sections are needed.
  - `dlt[parquet]` becomes `dlt[parquet,s3]` (adds `s3fs`).
  - Every run logs its destination first (`Lake destination: ...`), never the credentials.

### Changes to dbt models

- None in P2M1. Correction found while building P2M1: the sources don't read `BUCKET_URL`. Their `external_location` is `<MLB_DATA_DIR>/lake/...`, and `stg_dlt__completed_loads` globs `<MLB_DATA_DIR>/lake/*/_dlt_loads/*`, so today dbt only sees the local lake. P2M2 has to bridge that for the prod run: either copy the R2 lake to the runner's `<MLB_DATA_DIR>/lake` before `dbt build` (no dbt change), or point the sources at `s3://` through DuckDB's `httpfs` with R2 credentials (a dbt change; check that `glob()` over the marker files works on S3). See Open Questions.

### Changes to Docker / environment

- `docker-compose.yml`: no change. `env_file: .env` already passes every variable in `.env` to the containers, and `.env.example` is the one list of variables (decided in P2M1 review, instead of repeating each one under `environment:`). The scheduled workflow (P2M2) builds `.env` from `.env.example`, as CI does, and appends `BUCKET_URL`, `IS_PROD=true`, and the R2 secrets to it.
- `.env.example`: document new vars and their defaults (local paths).
- CI workflow (`.github/workflows/scheduled-ingest.yml`): new job, runs daily at 2 AM UTC (after games end, before US morning).
  - Sets `BUCKET_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` from GitHub Actions Secrets, by writing them into the runner's `.env`.
  - Sets `IS_PROD=true` (gates R2 writes; dev runs omit this to stay local).
  - Runs ingest commands, then `dbt build`, then updates the data report.
  - On failure, logs to `data/logs/` and posts to GitHub Issues or email (optional).

### Changes to configuration

- `.env.example` additions:
  ```
  BUCKET_URL=
  IS_PROD=false
  AWS_ACCESS_KEY_ID=
  AWS_SECRET_ACCESS_KEY=
  R2_ACCOUNT_ID=
  ```
  `BUCKET_URL` defaults to empty, not `file://./data/lake`: dlt reads `file://./data/lake` as the absolute path `/data/lake` (the `.` is taken as a host), which only matches the container's `MLB_DATA_DIR` by coincidence. Empty keeps the lake tied to `MLB_DATA_DIR`, where dbt reads it; `file://` paths are resolved against the working directory by `config.py`. `R2_BUCKET_NAME` is dropped: the bucket is already in `BUCKET_URL`.
- `.dlt/config.toml`: no change; env vars drive the destination in code.
- GitHub Actions Secrets (in repo settings):
  - `R2_ACCESS_KEY_ID`
  - `R2_SECRET_ACCESS_KEY`

### Changes to documentation

- **`docs/configuration.md`:** add env vars table rows for `BUCKET_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `IS_PROD`. Explain R2 bucket setup (create bucket, API token, URL format).
- **`docs/usage.md`:** add section "Scheduled runs and GitHub Actions" explaining the daily workflow, how to check logs, how to trigger manual runs.
- **`docs/setup.md`:** add optional section for R2 setup (creating bucket, generating API token, setting GitHub Actions Secrets).

## Impact on `ARCHITECTURE.md`

New section (§13) on cloud and scheduled operation:
- Where data lives (R2 vs. local).
- How env vars control destination.
- GitHub Actions workflow schedule and failure handling.
- Secrets management approach.

Update §3 (Stack) to note "dlt destination: S3-compatible (R2) for prod, filesystem for local/CI".

Update §9 (Environment) to include the scheduled-ingest workflow and GitHub Actions Secrets setup.

## Milestones

### P2M1 — dlt → R2 + local flexibility

**Goal:** dlt pipelines work with both local filesystem (CI) and R2 (prod) via environment variables. No code changes needed to swap.

**Acceptance checks:**
- An empty `BUCKET_URL` (the default), or `BUCKET_URL=file://./data/lake`, allows local ingest to work (existing behavior).
- `BUCKET_URL=s3://r2-bucket-url` with `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` set allows ingest to R2 (verified with test credentials or dry-run).
- `docker compose run --rm pipeline python -m mlb.pipelines.statcast` runs against local lake (existing CI behavior).
- `docker compose run --rm pipeline python -m mlb.pipelines.statcast` (with R2 env vars) runs against R2 (manual test or CI secret test).
- Logs show which destination is being used.
- `.env.example` documents all new vars with examples.
- `docs/configuration.md` lists and explains `BUCKET_URL`, `AWS_*`, `IS_PROD`.

### P2M2 — GitHub Actions scheduled daily runs

**Goal:** pipeline runs automatically every day, ingests yesterday's data, builds dbt, generates report. No manual intervention needed.

**Acceptance checks:**
- `.github/workflows/scheduled-ingest.yml` exists and is triggered daily at 2 AM UTC (off-season or seasonal?).
- Workflow reads R2 credentials from GitHub Actions Secrets (`R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`).
- Workflow sets `BUCKET_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `IS_PROD=true`.
- Workflow runs: `docker compose run --rm pipeline python -m mlb.pipelines.statcast`, `docker compose run --rm pipeline python -m mlb.pipelines.mlb_api`, `docker compose run --rm dbt build`, `docker compose run --rm reports run build`.
- On success: data lands in R2, warehouse is built, report is generated and published to `gh-pages`.
- On failure: workflow fails visibly in GitHub; logs are available in artifacts; optional Slack/email notification.
- Manual trigger: can run workflow from GitHub UI for testing or backfills.
- Workflow runs and passes (dry-run with test R2 bucket or mock credentials, or real R2 for user to set up).

### P2M3 — Delta Lake (deferred)

**Goal:** switch dlt to Delta Lake table format for true ACID merges in the cloud lake (optional, deferred until Parquet + staging dedup shows a need).

**Benefit assessment: no meaningful benefit to this project today, so it stays deferred.** Delta Lake's value is atomic multi-file merges and time travel on the lake itself. This pipeline doesn't need that:
- Principle 3 (idempotent loads) is already satisfied by plain `append` + staging-level dedup (§7.3) — re-running a load for any date range is safe without a lake-level merge.
- `merge` on the filesystem destination already silently falls back to `append` (§6.2, "No merge on plain Parquet"), so there's no ACID-merge capability being left on the table by staying on Parquet.
- There's a single writer (one scheduled run at a time) and no concurrent-write scenario for Delta's transaction log to arbitrate.
- Delta Lake would add a dependency (`deltalake`/`delta-rs`) and a less mature DuckDB read path than native Parquet, for a capability (safe concurrent merges, time travel) this single-user, single-writer pipeline doesn't use.

Revisit only if the project moves to multiple concurrent writers, needs lake-level (not staging-level) deduplication, or wants time-travel/rollback on raw data — none of which are in scope for Phase 2.

**Notes:** Skip for now. Parquet + staging deduplication is sufficient at current scale. Revisit when cloud lake grows or multi-writer scenarios arise.

## Open Questions

- What time should the daily scheduled run happen? (Currently 2 AM UTC; adjust based on game schedule and user timezone preference.)
- Should off-season runs skip or continue? (Tentatively: continue; MVP loads yesterday's games if any, or 0 rows.)
- Failure notification: silent, GitHub Issues, Slack, email? (Tentatively: GitHub workflow shows red; logs in artifacts; optional Slack for team.)
- P2M2: how does the prod `dbt build` read the R2 lake? Copy the lake to the runner first (simple, no dbt change, downloads the whole lake each run) or read `s3://` through DuckDB `httpfs` (reads only what it needs, but changes the sources and `stg_dlt__completed_loads`)?
- R2 cost: acceptable for personal project? (Tentatively: yes; ~$5/month for storage at typical ingestion rate.)

## Status / Decision Log

- **2026-09-27:** Phase marked as `proposed` with stub design.
- **2026-09-30:** Phase `accepted` and `in progress`. Decisions finalized: R2 + local, GitHub Actions, GitHub Actions Secrets. Milestones P2M1 and P2M2 defined. P2M3 (Delta Lake) deferred. Phase 1 (modeling) marked independent and on hold.
- **2026-09-30:** P2M1 built. `BUCKET_URL` / `IS_PROD` / `AWS_*` / `R2_ACCOUNT_ID` choose the lake in `config.py`; no code change swaps local and R2. Decisions: empty `BUCKET_URL` is the local default (not `file://./data/lake`, which dlt misreads), `IS_PROD` is enforced in both directions, `R2_BUCKET_NAME` dropped, dbt reading the R2 lake moved to P2M2. Verified with unit tests (no network) and an end-to-end Statcast run against a local S3 emulator (moto), configured only through env vars: Parquet, load markers and state landed in the bucket, and the watermark was restored from the bucket after deleting `dlt_pipelines/`. A run against a real R2 bucket is still to do once the bucket and token exist.

## References

- [dlt Cloud Deployment](https://dlthub.com/docs/general-usage/setup-a-dlt-project)
- [Delta Lake](https://delta.io/)
- [Dagster Cloud](https://docs.dagster.io/cloud)
- [DuckDB on AWS S3](https://duckdb.org/docs/guides/io/s3.html)
