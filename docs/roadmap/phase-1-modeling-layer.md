# Phase 1 — Modeling Layer

**Status:** proposed (milestones drafted 2026-10-03, awaiting review before `accepted`)

## Summary

Build intermediate models, facts, and dimensions on top of the staging layer. Add standard batting and pitching metrics (AVG, OBP, SLG, OPS, wOBA, ERA, WHIP, K%, BB%) at game, season, career, and rolling grains, so the warehouse answers baseball questions directly instead of only holding cleaned raw tables.

## Motivation

Phases 0 and 2 deliver a thin staging layer, refreshed daily and backfilled to 2015 in R2. Staging is deliberately free of business logic (`ARCHITECTURE.md` §7.1): no joins, no aggregates, no metrics. Phase 1 adds that logic once, tested, in dbt, so dashboards, notebooks and reports don't each re-derive it. The data it needs is now fresh and complete, which is why this phase comes next.

## Scope

### In scope
- **Intermediate models:** plate appearances from Statcast (one row per PA, with outcome flags), games with derived status, and other reusable steps.
- **Dimensions:** players, teams (per season), and plate-appearance outcomes (a seed with well-known definitions).
- **Facts:** one row per game; per player per game (batting, pitching); per player-season; per team-season.
- **Metrics:** AVG, OBP, SLG, OPS, wOBA, K%, BB%, ERA, WHIP.
- **Rolling metrics:** trailing 7-day and season-to-date per player per date, and career totals.
- **Staging additions:** the boxscore counting stats these metrics need but staging doesn't select yet (hit-by-pitch, sac flies, sac bunts, intentional walks, outs, batters faced). Only new columns from the same raw table; no new logic in staging.

### Out of scope
- **Additional sources (FanGraphs, Baseball Reference, Chadwick register).** Proposed to move to their own phase; see Open Questions. Nothing in this phase needs them.
- **dbt snapshots / SCD2 tables.** History comes from the append-only lake, not from the warehouse; see Decisions.
- Dashboards, BI tools, or user-facing reports beyond the existing CI report.
- Advanced metrics with external constants or park factors (wRC+, FIP, WAR), projections, ML.
- Incremental models. Full rebuilds stay cheap at this size (`ARCHITECTURE.md` §7.2); revisit only if `dbt build` gets slow.

## Dependencies

Phase 0 (pipeline and staging) and Phase 2 (daily refresh, history backfill) are shipped. No dependency on future phases.

## Design

### Decisions (answers to the stub's open questions)

1. **Player ID crosswalk: not needed for this phase.** Statcast's `batter`/`pitcher` and the MLB Stats API's person ids are the same MLBAM ids, and both sources share `game_pk`. Every join in this phase uses those. A crosswalk (the Chadwick register) is only needed to join FanGraphs or Baseball Reference, so it ships with those sources.
2. **Rolling metrics are marts (facts), not intermediate models.** They're end products someone queries directly. Each is built from the player-game facts with window functions, rebuilt in full every run like everything else. Intermediate models are only for reusable steps (for example the plate-appearance table) and are never queried by consumers.
3. **Update cadence: daily is enough.** `scheduled-ingest.yml` already runs `dbt build` daily, which will build the new models too. No new workflow, no change to Phase 2.
4. **No dbt snapshots, no SCD2 tables.** A dbt snapshot keeps its history inside the warehouse, but the warehouse is disposable and rebuilt from the lake on a fresh runner every run (principle 2), so snapshot history would be lost every day. Instead:
   - **A player's team at the time of a game** comes from the boxscore, which records `team_id` per player per game. That is exact, including mid-season trades, with no effective-date logic.
   - **Team attributes** (name, league, division) are already per season in `stg_mlb__teams`; `dim_teams` keys on team × season.
   - **Player bios** are latest-only, which matches the pipeline today (`people` bios are not refreshed, `ARCHITECTURE.md` §6.6).
   - If a true change history is ever needed, derive it from the lake's load history (every load is kept), not from snapshots.
5. **Team identity by `game_pk`, not abbreviations.** Statcast's team abbreviations don't always match the MLB API's (`ARCHITECTURE.md` §11). Plate appearances get their batting and fielding team ids by joining `stg_mlb__games` on `game_pk` and using `inning_topbot` (top = away team bats), so no abbreviation mapping seed is needed.
6. **Which source counts.** Official counting stats (H, HR, BB, ER, outs, …) come from the MLB API boxscores, which are the official record. Statcast supplies pitch- and PA-level detail and wOBA, using Savant's own `woba_value` / `woba_denom` per PA (so wOBA uses Savant's season weights with no constants in our code).
7. **Game types.** Season facts and metrics cover regular season (`R`) by default, with postseason (`F`, `D`, `L`, `W`) as a separate grain value. Spring training and exhibitions are excluded from season and career stats but stay in game-level facts.

### Changes to pipelines

None. All inputs are already in the lake.

### Changes to dbt models

```
dbt/
├── seeds/
│   └── pa_events.csv            # Statcast `events` value → is_pa, is_ab, is_hit, total_bases, is_walk, is_strikeout, …
├── models/
│   ├── staging/                 # + new boxscore columns only
│   ├── intermediate/            # schema `intermediate`
│   │   └── int_statcast__plate_appearances.sql
│   └── marts/                   # schema `marts`
│       ├── dim_players.sql, dim_teams.sql
│       ├── fct_games.sql
│       ├── fct_batting_games.sql, fct_pitching_games.sql
│       ├── fct_batting_seasons.sql, fct_pitching_seasons.sql, fct_team_seasons.sql
│       ├── fct_batting_rolling.sql, fct_pitching_rolling.sql
│       └── fct_batting_careers.sql, fct_pitching_careers.sql
└── macros/
    └── metrics.sql              # one definition per metric (avg, obp, slg, era, whip, …) and a null-safe divide
```

- All `table`, like staging. Same conventions: lowercase, one CTE per step, a one-line comment on any DuckDB-specific line, a description and tests on every model.
- **Metric definitions live in one macro file.** Season, career and rolling models all call the same macros, so OBP can't mean two things. Rates are null (not 0, not an error) when the denominator is 0.
- **Innings pitched:** computed from `outs` (outs / 3), never from the baseball-notation `innings_pitched` text (`6.1` means 6⅓).
- **Rolling windows** are calendar-day windows (`range between interval 6 days preceding and current row`) over a player × date spine, so days off count as days. That form is standard SQL (DuckDB and Postgres both accept it).

### Tests

- Every model: keys `not_null` + unique on its grain (`dbt_utils.unique_combination_of_columns` where composite).
- **dbt unit tests** (`unit_tests:` in YAML, dbt ≥ 1.8) for every metric macro and for the PA outcome flags, with hand-written inputs and expected outputs. They need no lake data, so they run in CI's fixture build and never touch the network.
- **Reconciliation tests** (singular, severity `warn`): per Final game, Statcast plate appearances vs. boxscore `plate_appearances` per team; season sums of `fct_batting_games` equal `fct_batting_seasons`.
- **Accepted ranges** on rates (AVG, OBP, SLG between 0 and 1 or a little above for SLG; ERA ≥ 0).

### Changes to Docker / environment

None.

### Changes to configuration

`dbt_project.yml`: `intermediate` and `marts` model folders (schema and materialization) and `seed-paths`. No new environment variables.

### Changes to documentation

- `docs/usage.md`: a "Modeling layer" section (what each mart is for, its grain, example queries).
- `README.md`: one line in the overview; Quick Start unchanged (`dbt build` builds everything).
- Model descriptions in YAML serve as the column-level reference (`dbt docs generate`).

## Impact on `ARCHITECTURE.md`

- §1: the modeling layer moves from out of scope to in scope.
- §2: principle 4 ("staging is thin") stays; add that business logic lives in `intermediate/` and `marts/`.
- §4, §5: the extra layers in the overview and layout.
- New §7.6 (or a new §8 with later sections renumbered): the modeling layer, its decisions above, and the metric definitions.
- §11: the PA vs. pitch and source-of-truth notes, and why there are no snapshots.

## Milestones

### P1M1 — Staging additions and dimensions

- Add hit-by-pitch, sac flies, sac bunts, intentional walks and catcher's interference to `stg_mlb__boxscore_batters`, and outs, batters faced, hit-by-pitch and home runs to `stg_mlb__boxscore_pitchers` (all present in `schemas/export/mlb_api.schema.yaml`).
- `dbt/models/marts/` and `dbt/models/intermediate/` folders configured; `dim_players` (one row per player, from `stg_mlb__people`) and `dim_teams` (team × season, from `stg_mlb__teams`).

**Done when:**
- `docker compose run --rm dbt build` passes, in CI's one-day build and against the fixture lake.
- The new staging columns have `not_null` tests where the API always sends them, and the existing dedup test (`assert_staging_rows_match_distinct_raw_keys`) still passes.
- Deleting `data/warehouse` and rebuilding gives identical row counts.

### P1M2 — Plate appearances and games

- `seeds/pa_events.csv`: every `events` value in the Savant CSV docs, with outcome flags.
- `int_statcast__plate_appearances`: one row per PA (the pitch where `events` is set), with batter, pitcher, batting and fielding team ids (via `game_pk` + `inning_topbot`), outcome flags from the seed, and `woba_value` / `woba_denom`.
- `fct_games`: one row per game, with teams, final score, status, game type, and Statcast pitch and PA counts.

**Done when:**
- Unit tests cover the seed join (a walk is a PA but not an AB, a sac fly is neither an AB nor a hit, …).
- A test fails the build if a Statcast `events` value is missing from the seed (so a new event type is noticed, not silently dropped).
- The PA-vs-boxscore reconciliation test runs and passes (or warns with an explained exception) on CI's day.

### P1M3 — Player facts and metrics

- `macros/metrics.sql` with AVG, OBP, SLG, OPS, wOBA, K%, BB%, ERA, WHIP, IP-from-outs, and a null-safe divide.
- `fct_batting_games`, `fct_pitching_games` (player × game, with the team they played for), `fct_batting_seasons`, `fct_pitching_seasons` (player × season × team × game-type group, plus an all-teams row per season for traded players), and `fct_team_seasons`.

**Done when:**
- Every metric macro has a unit test with hand-checked expected values, including the zero-denominator case.
- Season facts reconcile with the sum of game facts (test).
- **Manual check, recorded in the decision log:** after a full backfill, three players' 2025 regular-season lines (one hitter, one starter, one reliever, one of them traded mid-season) match MLB.com's official stats.

### P1M4 — Rolling and career metrics

- `fct_batting_rolling`, `fct_pitching_rolling`: player × date, trailing 7-day and season-to-date counting stats and rates.
- `fct_batting_careers`, `fct_pitching_careers`: career totals and rates (regular season, from 2015, which is the lake's coverage; documented as such, not true career for veterans).

**Done when:**
- Unit tests cover a window with days off, the window crossing a season boundary (season-to-date resets, trailing 7-day doesn't reach into the off-season), and a player's first game.
- `docs/usage.md` has the Modeling layer section, and `ARCHITECTURE.md` is updated per "Impact" above.

## Open Questions

- **Move FanGraphs / Baseball Reference / Chadwick to their own phase?** Recommended: yes. They need new pipelines, a crosswalk and a scraping-tolerance story, and nothing in P1M1–P1M4 depends on them. Keeping them here would make the phase twice as big for metrics (wRC+, WAR) that are explicitly out of scope above.
- **Should the CI data report show any marts?** CI loads one day, so season and rolling numbers would be near-meaningless there. Proposed: no change to the report in this phase.
- **"Career" from 2015 only:** acceptable, or should careers be omitted until older history (pre-Statcast boxscores) is loaded? Proposed: keep, documented as "since 2015".

## Status / Decision Log

- **2026-09-27:** Phase marked as `proposed` with stub design.
- **2026-10-03:** Phase 2 shipped; Phase 1 is next. Drafted the design and milestones P1M1–P1M4. Answered the stub's four open questions (see Design → Decisions): no crosswalk needed (shared MLBAM ids and `game_pk`); rolling metrics are marts; daily cadence via the existing `scheduled-ingest.yml`; no dbt snapshots, because the warehouse is rebuilt from the lake every run, and team-at-time-of-game comes from boxscores. Proposed moving the additional sources to their own phase. Stays `proposed` until reviewed.

## References

- [Statcast Search CSV documentation](https://baseballsavant.mlb.com/csv-docs) (`events`, `woba_value`, `woba_denom`)
- [FanGraphs Baseball Glossary](https://www.fangraphs.com/library/index.php/main-page/)
- [Baseball Reference Glossary](https://www.baseball-reference.com/about/glossary.shtml)
- [Chadwick Bureau Register](https://baseballregister.chadwicks.com/)
- [dbt unit tests](https://docs.getdbt.com/docs/build/unit-tests)
- [dbt Metric Layer](https://docs.getdbt.com/docs/build/metrics) (considered; plain macros are simpler for one warehouse and no BI tool)
