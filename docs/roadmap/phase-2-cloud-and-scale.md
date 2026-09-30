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
- **DuckDB warehouse:** stays local for now (shared cloud warehouse deferred to later phase).

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

- **dlt destination config:** move from hardcoded `filesystem` with local paths to environment-driven config.
  - `BUCKET_URL` env var controls destination: `file://data/lake` for local (CI), `s3://bucket/path` format pointing to R2 for prod.
  - dlt's S3-compatible destination config (R2 uses S3 API) requires `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` env vars.
  - `.dlt/config.toml` includes a new `[destination.filesystem_or_s3]` section (conditional on `BUCKET_URL`), and a `[destination.s3]` section for R2 credentials.
  - Pipelines read `BUCKET_URL` from env at runtime; code changes only in `config.py` to pass it to dlt.

### Changes to dbt models

- None. dbt continues to read from `BUCKET_URL/raw_*` via `external_location` in sources, which already uses `MLB_DATA_DIR` env var. On R2, the mount point is a read-only DuckDB S3 mount.

### Changes to Docker / environment

- `docker-compose.yml`: add env vars `BUCKET_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (read from `.env` or GitHub Actions Secrets when running in CI).
- `.env.example`: document new vars and their defaults (local paths).
- CI workflow (`.github/workflows/scheduled-ingest.yml`): new job, runs daily at 2 AM UTC (after games end, before US morning).
  - Sets `BUCKET_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` from GitHub Actions Secrets.
  - Sets `IS_PROD=true` (gates R2 writes; dev runs omit this to stay local).
  - Runs ingest commands, then `dbt build`, then updates the data report.
  - On failure, logs to `data/logs/` and posts to GitHub Issues or email (optional).

### Changes to configuration

- `.env.example` additions:
  ```
  BUCKET_URL=file://./data/lake
  AWS_ACCESS_KEY_ID=
  AWS_SECRET_ACCESS_KEY=
  R2_ACCOUNT_ID=
  R2_BUCKET_NAME=
  IS_PROD=false
  ```
- `.dlt/config.toml`: add S3-compatible destination section for R2 (or keep simple and let env vars drive it in code).
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
- `BUCKET_URL=file://./data/lake` allows local ingest to work (existing behavior).
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

**Notes:** Skip for now. Parquet + staging deduplication is sufficient at current scale. Revisit when cloud lake grows or multi-writer scenarios arise.

## Open Questions

- What time should the daily scheduled run happen? (Currently 2 AM UTC; adjust based on game schedule and user timezone preference.)
- Should off-season runs skip or continue? (Tentatively: continue; MVP loads yesterday's games if any, or 0 rows.)
- Failure notification: silent, GitHub Issues, Slack, email? (Tentatively: GitHub workflow shows red; logs in artifacts; optional Slack for team.)
- R2 cost: acceptable for personal project? (Tentatively: yes; ~$5/month for storage at typical ingestion rate.)

## Status / Decision Log

- **2026-09-27:** Phase marked as `proposed` with stub design.
- **2026-09-30:** Phase `accepted` and `in progress`. Decisions finalized: R2 + local, GitHub Actions, GitHub Actions Secrets. Milestones P2M1 and P2M2 defined. P2M3 (Delta Lake) deferred. Phase 1 (modeling) marked independent and on hold.

## References

- [dlt Cloud Deployment](https://dlthub.com/docs/general-usage/setup-a-dlt-project)
- [Delta Lake](https://delta.io/)
- [Dagster Cloud](https://docs.dagster.io/cloud)
- [DuckDB on AWS S3](https://duckdb.org/docs/guides/io/s3.html)
