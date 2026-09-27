# Phase N — [Phase Name]

**Status:** [proposed / accepted / in progress / shipped / dropped]

## Summary

One paragraph describing what this phase delivers and the key outcomes.

## Motivation

The problem this phase solves, and why it comes next in the roadmap.

## Scope

### In scope
- Feature / capability A
- Feature / capability B

### Out of scope
- What's explicitly **not** being built in this phase and why

## Dependencies

Which earlier phases (if any) must ship before this one can start.

## Design

### Changes to pipelines

What new dlt sources, resources, or pipeline logic?

### Changes to dbt models

New models, transformations, or organizational changes?

### Changes to Docker / environment

Any new services, volumes, or configuration?

### Changes to configuration (`.env`, `.dlt/config.toml`)

New environment variables or settings?

### Changes to documentation

Which docs files are added or updated?

## Impact on `ARCHITECTURE.md`

Which sections of the main spec change or need updating after this phase ships?

## Milestones

Each milestone builds incrementally and ends with acceptance checks that must all pass. List them in order.

### M[N].[0] — [Milestone Title]

[Brief description of what this milestone delivers]

- [Key output 1]
- [Key output 2]

**Done when:**
- [Check 1]
- [Check 2]

---

## Open Questions

Questions, unknowns, or design decisions still to be made. These can be resolved during `in progress` or before `accepted`.

## Status / Decision Log

Decisions made and their dates. Examples:

- **2026-09-27:** Decided on Dagster over Airflow because [reasons].
- **2026-10-01:** Deferred cloud lake to Phase 3 because [reasons].

## References

Links to related documentation, tools, or prior discussions.
