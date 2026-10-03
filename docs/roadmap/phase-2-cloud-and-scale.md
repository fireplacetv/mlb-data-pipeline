# Phase 2 — Cloud and Scale

**Status:** shipped (2026-10-03). The shipped design is in `ARCHITECTURE.md` (§6.2, §6.6, §7.2, §9.4, §10); this doc keeps the design history and decision log.

## Summary

Move the data lake to Cloudflare R2 for production (with local filesystem preserved for CI testing), add daily automated runs via GitHub Actions, and implement secrets management with GitHub Actions Secrets. Enable persistent cloud storage and reliable scheduled updates without manual intervention.

## Motivation

Phase 0 delivers a working pipeline but requires manual runs and local storage. Phase 2 solves two problems: (1) persistent, shared cloud storage so Phase 1's modeling layer can build on stable, automatically-refreshed data, and (2) daily scheduled runs so the pipeline operates without manual intervention. This unlocks Phase 1's value: Phase 1 models are far more interesting when data is fresh and accessible without re-downloads.

## Scope

### In scope
- **Cloud lake:** dlt destination changes to R2 (Cloudflare R2) for production; local filesystem preserved for CI/testing.
- **Orchestration:** GitHub Actions for daily scheduled runs (cron via Actions workflow).
- **Secrets management:** GitHub Actions Secrets for R2 credentials (access key, secret key).
- **Flexibility:** Environment variables (`S3_BUCKET_URL`, `IS_PROD`) allow swapping storage per environment (R2 in prod, local in CI) without code changes.
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
  - `S3_BUCKET_URL` env var controls destination: empty (the default) for the local lake at `<MLB_DATA_DIR>/lake` (dev, CI), or for R2 in prod the bucket's S3 API URL exactly as Cloudflare shows it on the bucket's Settings page, optionally with a folder: `https://<account id>.r2.cloudflarestorage.com/<bucket>[/<folder>]`. `config.py` splits it into the endpoint and an `s3://<bucket>[/<folder>]` URL for dlt. The account endpoint alone (no bucket) and any non-`https://` value are refused.
  - dlt's S3-compatible destination config (R2 uses S3 API) takes `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` env vars, with the endpoint from `S3_BUCKET_URL` (region `auto`).
  - `IS_PROD` gates it both ways: an R2 lake needs `IS_PROD=true`, and `IS_PROD=true` with a local lake is refused. Either mismatch, or missing keys, exits `2` with a message naming the setting.
  - `config.py` reads all of these (`resolve_lake`) and `pipelines/common.py` builds the dlt destination and credentials in code, the same way `bucket_url` was already set in code. `.dlt/config.toml` doesn't change: no new sections are needed.
  - `dlt[parquet]` becomes `dlt[parquet,s3]` (adds `s3fs`).
  - Every run logs its destination first (`Lake destination: ...`), never the credentials.

### Changes to dbt models

- Built in P2M1 (decided in PR #12 review, after the first real R2 ingest left dbt reading an empty local lake): dbt reads the lake where dlt wrote it. The sources' `external_location` and `stg_dlt__completed_loads` use a lake root of `s3://<bucket>[/<folder>]` from `S3_BUCKET_URL`, or `<MLB_DATA_DIR>/lake` when it's empty (`dbt/macros/lake_root.sql`, inlined in the source YAML). `profiles.yml` sets DuckDB's `s3_endpoint` / `s3_url_style` / `s3_region` from `S3_BUCKET_URL`; DuckDB reads the `AWS_*` keys from the environment, so no secret is in dbt's SQL or logs. `glob()` over the load markers works on S3 (checked against an S3 emulator, with identical staging row counts to a local build). The image installs `httpfs` from the locked `duckdb-extension-httpfs` PyPI package, so no run downloads it. Only the warehouse file stays local; P2M2 uploads it.

### Changes to Docker / environment

- `docker-compose.yml`: no change. `env_file: .env` already passes every variable in `.env` to the containers, and `.env.example` is the one list of variables (decided in P2M1 review, instead of repeating each one under `environment:`). The scheduled workflow (P2M2) builds `.env` from `.env.example`, as CI does, and appends `S3_BUCKET_URL`, `IS_PROD=true`, and the R2 secrets to it.
- `.env.example`: document new vars and their defaults (local paths).
- CI workflow (`.github/workflows/scheduled-ingest.yml`): new job, runs daily at 9 AM UTC (1–2 AM Pacific, after West Coast games end; originally 2 AM UTC, moved in PR #17).
  - Sets `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `S3_BUCKET_URL` from GitHub Actions Secrets, by writing them into the runner's `.env`.
  - Sets `IS_PROD=true` (gates R2 writes; dev runs omit this to stay local).
  - Runs ingest commands, then `dbt build`, then updates the data report.
  - On failure, logs to `data/logs/` and posts to GitHub Issues or email (optional).

### Changes to configuration

- `.env.example` additions:
  ```
  AWS_ACCESS_KEY_ID=
  AWS_SECRET_ACCESS_KEY=
  S3_BUCKET_URL=
  IS_PROD=false
  ```
  In the order Cloudflare shows them. `S3_BUCKET_URL` (originally `BUCKET_URL`) defaults to empty, not `file://./data/lake`: dlt reads `file://./data/lake` as the absolute path `/data/lake` (the `.` is taken as a host), which only matches the container's `MLB_DATA_DIR` by coincidence, and empty keeps the lake tied to `MLB_DATA_DIR`, where dbt reads it. `R2_BUCKET_NAME` and `R2_ACCOUNT_ID` are dropped: the bucket and account ID are both in `S3_BUCKET_URL`. No `R2_API_TOKEN`: the token value is for Cloudflare's own API, and the S3 API uses only the access key pair.
- `.dlt/config.toml`: no change; env vars drive the destination in code.
- GitHub Actions Secrets (in repo settings):
  - `AWS_ACCESS_KEY_ID`
  - `AWS_SECRET_ACCESS_KEY`

### Changes to documentation

- **`docs/configuration.md`:** add env vars table rows for `S3_BUCKET_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `IS_PROD`. Explain R2 bucket setup (create bucket, API token, URL format).
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
- An empty `S3_BUCKET_URL` (the default) allows local ingest to work (existing behavior).
- `S3_BUCKET_URL=https://<account id>.r2.cloudflarestorage.com/<bucket>` with `IS_PROD=true`, `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` set allows ingest to R2 (verified with test credentials or dry-run).
- `docker compose run --rm pipeline python -m mlb.pipelines.statcast` runs against local lake (existing CI behavior).
- `docker compose run --rm pipeline python -m mlb.pipelines.statcast` (with R2 env vars) runs against R2 (manual test or CI secret test).
- Logs show which destination is being used.
- `.env.example` documents all new vars with examples.
- `docs/configuration.md` lists and explains `S3_BUCKET_URL`, `AWS_*`, `IS_PROD`.

### P2M2 — GitHub Actions scheduled daily runs

**Goal:** pipeline runs automatically every day, ingests yesterday's data, builds dbt, generates report. No manual intervention needed.

**Acceptance checks:**
- `.github/workflows/scheduled-ingest.yml` exists and is triggered daily at 2 AM UTC (off-season or seasonal?). (Since moved to 9 AM UTC, PR #17.)
- Workflow reads R2 credentials from GitHub Actions Secrets (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`).
- Workflow sets `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `S3_BUCKET_URL`, `IS_PROD=true`.
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

### P2M4 — Automatic historical backfill

**Goal:** steadily backfill history (`BACKFILL_START`, default 2015-04-01, through yesterday) into the R2 lake over many unattended firings, each making real forward progress, with no manual babysitting.

**Why a naive cron + timeout doesn't work:** scheduling the existing `--start`/`--end` backfill on cron, bounding it with `timeout-minutes`, and relying on GitHub to kill it mid-run fails for different reasons in each pipeline. Statcast's whole requested window is one `pipeline.run()`; the watermark is only written after every day has been attempted, so a kill commits nothing (no completed-load marker, no watermark) and the next firing restarts from scratch, forever re-hammering Savant for days it already spent time on. `mlb_api` commits each day's data as it goes, but the watermark only advances in the final `people` step after the whole window's days, snapshots, and people load finish — a kill before that leaves it unmoved, so the next firing wastefully re-requests the same days without converging any faster from a longer timeout.

**The fix:** size each firing to one bounded chunk (`--chunk-days`), computed from a progress mark, and let the run complete and commit normally, well inside the timeout. Every firing then genuinely advances that mark, so the next firing picks up right after it.

**Why the chunk's progress needs its own mark, not `loaded_through`:** the watermark never moves backward — by design, so a disconnected manual backfill is safe. But `scheduled-ingest.yml` (P2M2) already runs nightly and sets `loaded_through` to yesterday; once it has run even once, any backfill chunk into 2015 is permanently "disconnected" from that watermark, and `next_watermark` would correctly refuse to ever move it. A backfill tracked through `loaded_through` would make zero progress, forever, after the first nightly catch-up. The fix tracks backfill progress through a separate state key, `backfilled_through`, reusing `next_watermark`'s existing connected/failed-day rules against that key instead (there's precedent for a second state key: `mlb_api` already keeps `people_fetched` alongside `loaded_through`). This also means the daily catch-up and the automatic backfill can run on completely independent schedules without either resetting the other's progress.

**Acceptance checks:**
- `config.next_backfill_window` chooses the next contiguous chunk from `backfilled_through` (or `BACKFILL_START` on the first run), capped at yesterday, returning no window once the backfill is caught up — unit tested without network, including a chunk spanning the off-season and a failed day holding the mark for a retry.
- `--chunk-days [N]` on both `statcast.py` and `mlb_api.py`: loads `N` days (or `BACKFILL_CHUNK_DAYS` if `N` is omitted) from `backfilled_through`, advances only that mark (never `loaded_through`), and can't be combined with `--start`/`--end`.
- `.github/workflows/backfill.yml` fires on its own daily schedule (and `workflow_dispatch`), runs both pipelines with `--chunk-days`, shares `scheduled-ingest`'s concurrency group, and does not run `dbt build` or upload the warehouse (that stays on `scheduled-ingest.yml`'s own schedule).
- A simulated multi-firing run (chaining `next_backfill_window` through `next_watermark`) covers every day from `BACKFILL_START` to yesterday exactly once, with no gaps or repeats, and converges to a terminus.
- `docs/usage.md`, `docs/configuration.md`, and `ARCHITECTURE.md` §6.6 document `--chunk-days`, `backfilled_through`, `BACKFILL_START`, and `BACKFILL_CHUNK_DAYS`.

## Open Questions

- ~~What time should the daily scheduled run happen?~~ Decided in P2M2: 2 AM UTC, unchanged from the stub proposal. Moved to 9 AM UTC in PR #17: 2 AM UTC is 7 PM Pacific in season, before West Coast games end.
- ~~Should off-season runs skip or continue?~~ Decided in P2M2: continue year-round. The cron doesn't pause itself; the existing season-window skip (`SEASON_START`/`SEASON_END`) already makes an off-season run a no-op (0 rows), so there's nothing extra to build.
- ~~Failure notification: silent, GitHub Issues, Slack, email?~~ Decided in P2M2: none beyond GitHub's own red run and the uploaded logs artifact. Slack/email was optional scope; revisit if a failure is ever missed because no one was watching the Actions tab.
- ~~P2M2: how does the prod `dbt build` read the R2 lake?~~ Decided in P2M1: dbt reads `s3://` directly through DuckDB `httpfs` (see Changes to dbt models).
- ~~R2 cost: acceptable for personal project?~~ Closed at ship: accepted as tentatively proposed (estimated ~$5/month at most for storage at typical ingestion rate). Cost monitoring stays out of scope; revisit if the bill surprises.
- Disabling `backfill.yml` once it reaches yesterday: not built. After completion each firing is a fast no-op; disable the workflow from the Actions tab if wanted. Not worth code.

## Status / Decision Log

- **2026-09-27:** Phase marked as `proposed` with stub design.
- **2026-09-30:** Phase `accepted` and `in progress`. Decisions finalized: R2 + local, GitHub Actions, GitHub Actions Secrets. Milestones P2M1 and P2M2 defined. P2M3 (Delta Lake) deferred. Phase 1 (modeling) marked independent and on hold.
- **2026-09-30:** P2M1 built. `BUCKET_URL` / `IS_PROD` / `AWS_*` / `R2_ACCOUNT_ID` choose the lake in `config.py`; no code change swaps local and R2. Decisions: empty `BUCKET_URL` is the local default (not `file://./data/lake`, which dlt misreads), `IS_PROD` is enforced in both directions, `R2_BUCKET_NAME` dropped, dbt reading the R2 lake moved to P2M2. Verified with unit tests (no network) and an end-to-end Statcast run against a local S3 emulator (moto), configured only through env vars: Parquet, load markers and state landed in the bucket, and the watermark was restored from the bucket after deleting `dlt_pipelines/`. A run against a real R2 bucket is still to do once the bucket and token exist.
- **2026-10-01:** First real R2 setup pasted the account endpoint (`https://<id>.r2.cloudflarestorage.com/`) into `BUCKET_URL` and was refused. `BUCKET_URL` now also takes the bucket's S3 API URL from Cloudflare's bucket Settings page, and an account endpoint with no bucket gets an error saying where to find the right URL. Then renamed in PR #12 review: `BUCKET_URL` → `S3_BUCKET_URL` (it holds the bucket's URL, not the account endpoint), taking only that URL. The `s3://` and `file://` forms and `R2_ACCOUNT_ID` are gone, so there is one way to write it, and the variables follow Cloudflare's order. Decided against `R2_API_TOKEN` (unused by the S3 API).
- **2026-10-01:** dbt reads the lake from R2 too (option 1 of the open question): same `S3_BUCKET_URL`, keys from the environment, `httpfs` baked into the image.
- **2026-10-01:** P2M2 built. `.github/workflows/scheduled-ingest.yml` runs daily at 2 AM UTC (`workflow_dispatch` also allows a manual run, with optional `start`/`end` inputs for a one-off backfill). It builds `.env` from `.env.example` with `R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY` (GitHub Actions Secrets) mapped to `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, `S3_BUCKET_URL` from a repository **variable** (not a secret — it decided this isn't a credential, following the `CI_INGEST_DATE` precedent), and `IS_PROD=true`; runs both pipelines, `dbt build`, then uploads `data/warehouse/mlb.duckdb` to R2 with `aws s3 cp --endpoint-url` (the preinstalled `aws` CLI on `ubuntu-latest`, no new dependency). Logs upload as an artifact on every run, including failures. Decided against a Slack/email failure notification (listed as optional in this doc's scope): a red Actions run plus the logs artifact is the whole alerting story for now; revisit if a silent failure is ever missed. Off-season runs are not special-cased — they load zero games via the existing season-window skip, rather than being paused. **PR #13 review:** the workflow does not build or publish the data report — that's CI-only (built from CI's own fixed-day data), not something the production pipeline needs to do on every run — so the report/publish/`.nojekyll` steps and the `gh-pages` concurrency group were dropped in favor of a `scheduled-ingest` group that just keeps this workflow's own runs from overlapping each other. Also confirmed: each pipeline's per-day politeness (sleep between days, retry with backoff) is hardcoded in `src/mlb/pipelines/statcast.py`/`mlb_api.py`, so it applies the same way on every invocation of this workflow, scheduled or manual — nothing here needs to set it separately.
- **2026-10-01:** First scheduled run failed: `needs AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY`. The repo had never had `R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY` Secrets set — the R2 keys were pasted into repo **Variables** named `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` instead (wrong mechanism, and unmasked in a public repo). Renamed the workflow's secret references from `R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY` to `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` so the Secret name matches the env var it maps to (no more R2→AWS rename step to get wrong); updated `docs/usage.md`, `docs/setup.md`, `docs/configuration.md` to match. Separately, and not part of this PR: the leaked token must be rotated in Cloudflare and the plaintext Variables deleted before the new Secrets are set.
- **2026-10-01:** P2M4 designed and built. Starting from "schedule the existing backfill on cron and let a timeout kill it," empirically checked `next_watermark(loaded_through=yesterday, window=<a 2015 chunk>, failed_days=[])` against the live `scheduled-ingest.yml` watermark and confirmed it returns `loaded_through` unchanged — the watermark's never-moves-backward rule, already relied on for safe manual backfills, also means a disconnected backfill chunk can never advance it once the nightly catch-up has run even once. Ruled out computing the next chunk in workflow shell from `dlt pipeline ... info -v` for the same reason: the quantity it would parse (`loaded_through`) is the wrong one, not just an untestable-shell-logic problem. Decided: track backfill progress through a new, separate state key, `backfilled_through` (precedent: `mlb_api`'s `people_fetched`), with its own pure chooser `config.next_backfill_window` reusing `next_watermark`'s existing connected/failed-day rules against that key; a new `--chunk-days [N]` flag on both pipelines (defaulting `N` to `BACKFILL_CHUNK_DAYS` when omitted, mutually exclusive with `--start`/`--end`); and a new workflow, `.github/workflows/backfill.yml`, on its own daily schedule, using `secrets.AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` per the rename above, sharing `scheduled-ingest`'s concurrency group (both write the same R2 lake and pipeline state; a long chunk can push that day's catch-up out of the queue, which self-heals via `LOOKBACK_DAYS` the next day) and skipping `dbt build`/warehouse upload (redundant with `scheduled-ingest.yml`'s own daily rebuild). Verified with a unit test chaining `next_backfill_window` through `next_watermark` across simulated firings: every day from `BACKFILL_START` to yesterday is covered exactly once, with no gaps or repeats, converging to a terminus that then no-ops.
- **2026-10-02:** CI (`ci.yml`, alongside the Evidence report) and both scheduled workflows (`scheduled-ingest.yml`, `backfill.yml`) gained a final **Box scores (last day loaded)** step, for eyeballing each run in its Actions log. It runs `python -m mlb.box_scores`, which reads the box scores back from the lake (not the API, so it verifies what landed) for the last day the run's `mlb_api` step loaded. The pipeline records that day in `<MLB_DATA_DIR>/mlb_api_last_day.txt` (cleared at the start of each run, so a run that loads no day — e.g. an off-season-only backfill chunk — shows nothing rather than a stale day). Reading uses dlt's `pipeline.dataset()`, so it needs no lake/credential setup of its own. The step runs on `!cancelled()`, after the ingest steps, so a failed day elsewhere in the window doesn't hide the box scores of the days that did load, and a display problem can't block the warehouse upload.
- **2026-10-01:** `scheduled-ingest.yml` moved from `0 2 * * *` to `0 9 * * *` (PR #17): 2 AM UTC is 7 PM Pacific during the regular season, before West Coast games finish. `backfill.yml` stays at `0 4 * * *`, still offset from it.
- **2026-10-03:** Phase `shipped`. P2M1, P2M2 and P2M4 done, and production verified end to end: the scheduled catch-up, a manual catch-up, and the first automatic backfill chunk all ran green against the real R2 bucket. P2M3 (Delta Lake) stays deferred. Folded into `ARCHITECTURE.md` as a new §9.4 (production operation) plus updates to §1, §3, §4, §5, §6.2, §6.6, §7.2, §9, §10, §11 and §12, rather than the new §13 proposed above (§13 is already the tooling references). Phase 1 (modeling layer) is next.

## References

- [dlt Cloud Deployment](https://dlthub.com/docs/general-usage/setup-a-dlt-project)
- [Delta Lake](https://delta.io/)
- [Dagster Cloud](https://docs.dagster.io/cloud)
- [DuckDB on AWS S3](https://duckdb.org/docs/guides/io/s3.html)
