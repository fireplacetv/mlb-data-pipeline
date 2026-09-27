# Roadmap

This index shows the development phases: current status, one-line goal, and a link to each phase's design doc.

| Phase | Goal | Status | Design Doc |
|---|---|---|---|
| **Phase 0** | Data pipeline scaffold, dlt extract/load, dbt staging layer, Docker setup | **Shipped** | [`ARCHITECTURE.md`](../../ARCHITECTURE.md) |
| **Phase 1** | Modeling layer: intermediate models, facts, dimensions, rolling metrics; additional sources (FanGraphs, Baseball Reference, Chadwick) | Proposed | [`phase-1-modeling-layer.md`](./phase-1-modeling-layer.md) |
| **Phase 2** | Cloud and scale: scheduling and orchestration (Dagster), cloud lake (S3/GCS), Delta Lake table format | Proposed | [`phase-2-cloud-and-scale.md`](./phase-2-cloud-and-scale.md) |

## How to Propose a New Phase

1. Copy [`_template.md`](./_template.md) to `phase-N-<name>.md`.
2. Fill in the Summary, Motivation, Scope, and Dependencies sections. Stub the rest.
3. Mark the phase as `proposed` in this table.
4. Start design discussions there (open questions, tradeoffs).
5. When accepted, write the Milestones section, update `ARCHITECTURE.md`, and shift the status to `accepted` or `in progress`.
6. As you ship each milestone, update the status.

## Shipping a Phase

1. Build each milestone in the phase's design doc.
2. Once all milestones pass their checks, fold the design into `ARCHITECTURE.md`.
3. Mark the phase as `shipped`.
4. The design doc stays for reference, historical context, and open-questions tracking.
