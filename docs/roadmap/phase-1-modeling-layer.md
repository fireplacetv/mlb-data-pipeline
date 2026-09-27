# Phase 1 — Modeling Layer

**Status:** proposed

## Summary

Build a semantic layer of intermediate models, facts, and dimensions on top of the staging layer. Add calculated metrics (batting average, OPS, wOBA), enrich with data from additional sources (FanGraphs, Baseball Reference), and organize the warehouse into a clear analytics-ready structure.

## Motivation

Phase 0 delivers a thin staging layer—raw data, lightly cleaned. Phase 1 adds the business logic: derived metrics, cross-table relationships, and historical snapshots. This unlocks downstream use cases: dashboards, analysis notebooks, and reports.

## Scope

### In scope
- Intermediate models: events, games with derived status, seasons with aggregated stats
- Facts: one row per game, per team-season, per player-season
- Dimensions: players, teams, game statuses, pitch outcomes with well-known definitions
- Calculated metrics: batting average, on-base percentage, slugging, wOBA, ERA, strikeout rate, WHIP
- Rolling metrics: trailing 7-day, season-to-date, career
- Additional sources: FanGraphs season stats via pybaseball, Baseball Reference, Chadwick Bureau player ID register
- Snapshot and SCD2 handling for dimensional changes (e.g., a player's team changes mid-season)

### Out of scope
- Dashboards, BI tools, or user-facing reports (separate tooling phase)
- Real-time or low-latency updates (scheduling phase, Phase 2)
- Player projections or ML models

## Dependencies

Requires Phase 0 (pipeline scaffold and staging layer) to be complete.

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

New section on the modeling layer and available metrics. Addition of FanGraphs and Baseball Reference to the data sources table.

## Milestones

[To be filled in when moving to `accepted`]

## Open Questions

- How should we handle player ID crosswalks? Chadwick Bureau register, or a simpler mapping?
- Should rolling metrics be facts or intermediate models?
- How often do we update (daily catch-up fine, or need hourly/real-time)?
- Snapshot vs. SCD2 for dimension changes: which patterns matter first?

## Status / Decision Log

- **2026-09-27:** Phase marked as `proposed` with stub design.

## References

- [FanGraphs Baseball Glossary](https://www.fangraphs.com/library/index.php/main-page/)
- [Baseball Reference Glossary](https://www.baseball-reference.com/about/glossary.shtml)
- [Chadwick Bureau Register](https://baseballregister.chadwicks.com/)
- [dbt Metric Layer](https://docs.getdbt.com/docs/build/metrics) (might inform metrics design)
