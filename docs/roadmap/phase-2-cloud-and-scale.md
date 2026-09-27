# Phase 2 — Cloud and Scale

**Status:** proposed

## Summary

Move the data lake to cloud storage (S3, GCS, or similar), switch to Delta Lake table format for true ACID merges in the lake, and add scheduling and orchestration (cron to start, Dagster if it scales beyond cron). Enable multi-user, on-demand access and automatic daily updates.

## Motivation

Phase 0–1 assume a single-user, manually-triggered pipeline on a local machine. Phase 2 lifts those constraints: automatic scheduling, shared cloud storage, and audited access.

## Scope

### In scope
- Cloud lake: change `bucket_url` to `s3://`, `gs://`, or R2.
- Delta Lake table format in dlt (`table_format="delta"`) for true merges (no staging dedup needed).
- Scheduling: cron-based daily runs as a minimum; Dagster if cron becomes insufficient.
- Multi-user access: shared data warehouse (Postgres, Snowflake, or DuckDB in the cloud).
- Secrets management: API keys and credentials vault (e.g., HashiCorp Vault, AWS Secrets Manager).

### Out of scope
- BI tools or dashboards (separate tooling phase)
- Data governance, lineage, or cataloging tools
- Cost optimization or resource monitoring

## Dependencies

Requires Phase 0 (pipeline and staging) and preferably Phase 1 (modeling layer) to be complete.

## Design

[To be filled in as the phase moves toward `accepted`]

### Changes to pipelines

TBD

### Changes to dbt models

TBD

### Changes to Docker / environment

TBD

### Changes to configuration

TBD

### Changes to documentation

TBD

## Impact on `ARCHITECTURE.md`

New section on cloud deployment, scheduled runs, and multi-user access. Warehouse portability section (§8) becomes concrete if moving off DuckDB.

## Milestones

[To be filled in when moving to `accepted`]

## Open Questions

- Which cloud provider (AWS, GCP, or vendor-agnostic)?
- DuckDB in cloud (e.g., DuckDB Cloud) or switch to Postgres/Snowflake?
- Dagster or simpler orchestration (e.g., GitHub Actions, cron + monitoring)?
- How to manage secrets and access control?
- Cost model and budget?

## Status / Decision Log

- **2026-09-27:** Phase marked as `proposed` with stub design.

## References

- [dlt Cloud Deployment](https://dlthub.com/docs/general-usage/setup-a-dlt-project)
- [Delta Lake](https://delta.io/)
- [Dagster Cloud](https://docs.dagster.io/cloud)
- [DuckDB on AWS S3](https://duckdb.org/docs/guides/io/s3.html)
