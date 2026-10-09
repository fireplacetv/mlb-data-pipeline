# Phase 1 — Modeling Layer

**Status:** proposed (milestones drafted 2026-10-03, exploration milestone added as P1M1 on 2026-10-08, awaiting review before `accepted`)

## Summary

Build intermediate models, facts, and dimensions on top of the staging layer. Add standard batting and pitching metrics (AVG, OBP, SLG, OPS, wOBA, ERA, WHIP, K%, BB%) at game, season, career, and rolling grains, so the warehouse answers baseball questions directly instead of only holding cleaned raw tables. Before any of that, a local exploration tool ([Rill](https://www.rilldata.com/)) over the full-history staging layer, used to check the design's assumptions against the real data (P1M1).

## Motivation

Phases 0 and 2 deliver a thin staging layer, refreshed daily and backfilled to 2015 in R2. Staging is deliberately free of business logic (`ARCHITECTURE.md` §7.1): no joins, no aggregates, no metrics. Phase 1 adds that logic once, tested, in dbt, so dashboards, notebooks and reports don't each re-derive it. The data it needs is now fresh and complete, which is why this phase comes next.

Several of the decisions below are assumptions about the data that nobody has looked at across all of history yet: which Statcast `events` values occur (the `pa_events` seed), whether Statcast and boxscores agree on counts (decision 6), which game types show up and how (decision 7), and where team identities diverge (decision 5). CI's report covers one day, which can't answer these. An exploratory layer over the full backfill lets us check them by slicing the data before writing models on top of them, and keeps being useful for sanity-checking each mart as it lands.

## Scope

### In scope
- **Exploration tool (P1M1):** a local-only Rill project over the staging layer of the production warehouse, run as its own `docker compose` service. A development aid for building this phase, not a user-facing dashboard.
- **Intermediate models:** plate appearances from Statcast (one row per PA, with outcome flags), games with derived status, and other reusable steps.
- **Dimensions:** players, teams (per season), and plate-appearance outcomes (a seed with well-known definitions).
- **Facts:** one row per game; per player per game (batting, pitching); per player-season; per team-season.
- **Metrics:** AVG, OBP, SLG, OPS, wOBA, K%, BB%, ERA, WHIP.
- **Rolling metrics:** trailing 7-day and season-to-date per player per date, and career totals.
- **Staging additions:** the boxscore counting stats these metrics need but staging doesn't select yet (hit-by-pitch, sac flies, sac bunts, intentional walks, outs, batters faced). Only new columns from the same raw table; no new logic in staging.

### Out of scope
- **Additional sources (FanGraphs, Baseball Reference, Chadwick register).** Proposed to move to their own phase; see Open Questions. Nothing in this phase needs them.
- **dbt snapshots / SCD2 tables.** History comes from the append-only lake, not from the warehouse; see Decisions.
- User-facing dashboards, BI, or published reports beyond the existing CI report. The P1M1 exploration tool runs only on a developer's machine: it isn't built in CI, published, or deployed.
- Advanced metrics with external constants or park factors (wRC+, FIP, WAR), projections, ML.
- Incremental models. Full rebuilds stay cheap at this size (`ARCHITECTURE.md` §7.2); revisit only if `dbt build` gets slow.

## Dependencies

Phase 0 (pipeline and staging) and Phase 2 (daily refresh, history backfill) are shipped. No dependency on future phases. P1M1 relies on Phase 2's upload of the built warehouse to R2 after each scheduled run.

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

### Exploration tool

**Tool:** Rill Developer, the open-source local app (a single binary with DuckDB built in, serving a web UI). Its metrics views and explore dashboards slice a table by any dimension over any time range, and its profiler shows each column's distribution and nulls. Both fit "what's actually in here?" questions better than writing queries one at a time. Pin its version, the way `reports/` pins Evidence.

**What it reads: a copy of the production warehouse, not the local one.** The local `data/warehouse/mlb.duckdb` holds whatever days you've loaded locally, while the warehouse that `scheduled-ingest.yml` uploads to R2 after each run holds staging back to 2015. A helper downloads that object to `data/explore/mlb.duckdb`, and Rill reads only that copy. Two side benefits: it never contends with a local `dbt build` for DuckDB's file lock, and it reads `staging.*`, so rows are already deduplicated and incomplete loads are already excluded (the raw lake has both, §6.2).

**Layout:** an `explore/` folder holding the Rill project (its connector config, SQL models over `staging.*`, metrics views and dashboards, all YAML and SQL, checked in), and an `explore` service in `docker-compose.yml` with its UI port exposed, in the style of the `reports` service. Nothing in CI.

**What it starts with,** chosen to answer the questions in the Motivation:
- Statcast pitches: by season, game type, `events` and pitch type, with pitch and PA counts.
- Boxscore batting and pitching lines: by season, team and game type.
- A per-game comparison of Statcast PA counts with boxscore plate appearances, by season and game type.

Dashboards for each mart get added as P1M2–P1M5 land. Rill models here are for looking only. Anything worth keeping becomes a dbt model with tests, never logic that lives only in Rill.

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

- `docs/usage.md`: an "Exploring the data" section (download the warehouse copy, start Rill, add a dashboard) and a "Modeling layer" section (what each mart is for, its grain, example queries).
- `docs/setup.md` / `docs/configuration.md`: the R2 read access the download needs, and any Rill settings (port, telemetry).
- `README.md`: one line in the overview; Quick Start unchanged (`dbt build` builds everything).
- Model descriptions in YAML serve as the column-level reference (`dbt docs generate`).

## Impact on `ARCHITECTURE.md`

- §1: the modeling layer moves from out of scope to in scope.
- §2: principle 4 ("staging is thin") stays; add that business logic lives in `intermediate/` and `marts/`.
- §1: the out-of-scope line about BI and dashboards gains the same carve-out for the local exploration tool.
- §3: Rill in the tools table.
- §4, §5: the extra layers in the overview and layout, plus `explore/` and `data/explore/`.
- §9: the `explore` compose service.
- New §7.6 (or a new §8 with later sections renumbered): the modeling layer, its decisions above, and the metric definitions.
- §11: the PA vs. pitch and source-of-truth notes, and why there are no snapshots.

## Milestones

### P1M1 — Exploration tool

- `explore/`: the Rill project with the three starting dashboards above, reading `data/explore/mlb.duckdb`.
- A helper that downloads the production warehouse from R2 to `data/explore/mlb.duckdb`, using the existing `.env` R2 settings.
- An `explore` service in `docker-compose.yml`.

**Done when:**
- With R2 credentials in `.env`, one documented command downloads the warehouse copy, and one more starts Rill with all three dashboards loading without errors over 2015 to the present.
- Running Rill while `docker compose run --rm dbt build` runs causes no lock errors on either side.
- Without R2 credentials, the helper fails with a clear message naming the missing settings.
- **Findings, recorded in the decision log before P1M2 starts:** the `events` values seen (vs. the planned seed), game types seen, how far Statcast PAs and boxscore PAs disagree and where, and any change this forces to Decisions 5–7 or to P1M2–P1M5.
- `docs/usage.md` has the "Exploring the data" section.

### P1M2 — Staging additions and dimensions

- Add hit-by-pitch, sac flies, sac bunts, intentional walks and catcher's interference to `stg_mlb__boxscore_batters`, and outs, batters faced, hit-by-pitch and home runs to `stg_mlb__boxscore_pitchers` (all present in `schemas/export/mlb_api.schema.yaml`).
- `dbt/models/marts/` and `dbt/models/intermediate/` folders configured; `dim_players` (one row per player, from `stg_mlb__people`) and `dim_teams` (team × season, from `stg_mlb__teams`).

**Done when:**
- `docker compose run --rm dbt build` passes, in CI's one-day build and against the fixture lake.
- The new staging columns have `not_null` tests where the API always sends them, and the existing dedup test (`assert_staging_rows_match_distinct_raw_keys`) still passes.
- Deleting `data/warehouse` and rebuilding gives identical row counts.

### P1M3 — Plate appearances and games

- `seeds/pa_events.csv`: every `events` value in the Savant CSV docs, with outcome flags.
- `int_statcast__plate_appearances`: one row per PA (the pitch where `events` is set), with batter, pitcher, batting and fielding team ids (via `game_pk` + `inning_topbot`), outcome flags from the seed, and `woba_value` / `woba_denom`.
- `fct_games`: one row per game, with teams, final score, status, game type, and Statcast pitch and PA counts.

**Done when:**
- Unit tests cover the seed join (a walk is a PA but not an AB, a sac fly is neither an AB nor a hit, …).
- A test fails the build if a Statcast `events` value is missing from the seed (so a new event type is noticed, not silently dropped).
- The PA-vs-boxscore reconciliation test runs and passes (or warns with an explained exception) on CI's day.

### P1M4 — Player facts and metrics

- `macros/metrics.sql` with AVG, OBP, SLG, OPS, wOBA, K%, BB%, ERA, WHIP, IP-from-outs, and a null-safe divide.
- `fct_batting_games`, `fct_pitching_games` (player × game, with the team they played for), `fct_batting_seasons`, `fct_pitching_seasons` (player × season × team × game-type group, plus an all-teams row per season for traded players), and `fct_team_seasons`.

**Done when:**
- Every metric macro has a unit test with hand-checked expected values, including the zero-denominator case.
- Season facts reconcile with the sum of game facts (test).
- **Manual check, recorded in the decision log:** after a full backfill, three players' 2025 regular-season lines (one hitter, one starter, one reliever, one of them traded mid-season) match MLB.com's official stats.

### P1M5 — Rolling and career metrics

- `fct_batting_rolling`, `fct_pitching_rolling`: player × date, trailing 7-day and season-to-date counting stats and rates.
- `fct_batting_careers`, `fct_pitching_careers`: career totals and rates (regular season, from 2015, which is the lake's coverage; documented as such, not true career for veterans).

**Done when:**
- Unit tests cover a window with days off, the window crossing a season boundary (season-to-date resets, trailing 7-day doesn't reach into the off-season), and a player's first game.
- `docs/usage.md` has the Modeling layer section, and `ARCHITECTURE.md` is updated per "Impact" above.

## Open Questions

- **Production warehouse copy or the lake?** Reading the R2 Parquet lake directly would also show raw columns that staging drops, but it includes duplicate rows and incomplete loads, and Rill would have to redo staging's dedup. Proposed: the warehouse copy, and add a column to staging (P1M2 already does this) when exploration shows one is needed.
- **Download helper: a `python -m mlb.…` module or a compose one-liner?** A module keeps config in `config.py` and gets a pytest test, per CLAUDE.md. Proposed: a small module.
- **Move FanGraphs / Baseball Reference / Chadwick to their own phase?** Recommended: yes. They need new pipelines, a crosswalk and a scraping-tolerance story, and nothing in P1M2–P1M5 depends on them. Keeping them here would make the phase twice as big for metrics (wRC+, WAR) that are explicitly out of scope above.
- **Should the CI data report show any marts?** CI loads one day, so season and rolling numbers would be near-meaningless there. Proposed: no change to the report in this phase.
- **"Career" from 2015 only:** acceptable, or should careers be omitted until older history (pre-Statcast boxscores) is loaded? Proposed: keep, documented as "since 2015".

## Status / Decision Log

- **2026-09-27:** Phase marked as `proposed` with stub design.
- **2026-10-03:** Phase 2 shipped; Phase 1 is next. Drafted the design and milestones P1M1–P1M4 (renumbered P1M2–P1M5 on 2026-10-08). Answered the stub's four open questions (see Design → Decisions): no crosswalk needed (shared MLBAM ids and `game_pk`); rolling metrics are marts; daily cadence via the existing `scheduled-ingest.yml`; no dbt snapshots, because the warehouse is rebuilt from the lake every run, and team-at-time-of-game comes from boxscores. Proposed moving the additional sources to their own phase. Stays `proposed` until reviewed.
- **2026-10-08:** Added a local Rill exploration tool as the new first milestone, P1M1, and renumbered the rest to P1M2–P1M5. It reads a copy of the production warehouse, to check the design's data assumptions across full history before building models. The out-of-scope line now allows it as a local-only development aid. Still `proposed`.
- **2026-10-09:** Built P1M1's pieces: `mlb.explore` (the `data/explore/mlb.duckdb` download helper, with unit tests against a fake filesystem — no network, per `CLAUDE.md`), the `explore/` Rill project (three models/metrics views/dashboards matching "What it starts with" above), the `explore` `docker-compose.yml` service, and the `docs/usage.md` "Exploring the Data" section. The three models' SQL and the metrics views' measure expressions were run against a hand-built in-memory DuckDB with the staging schema (not the real lake), which caught and fixed two `GROUP BY`-on-alias errors in `pa_reconciliation.sql`; that is as far as this could be checked here.
  **Not done, because this session had no R2 credentials, no network path to rilldata.com, and no Rill binary to run:** the actual `python -m mlb.explore` download against production R2, `docker compose up explore` against the real downloaded warehouse, and the four other acceptance checks that depend on both (dashboards loading cleanly over 2015–present, no DuckDB lock errors against a concurrent `dbt build`, the clear-failure-without-credentials check against the real CLI, and the **data findings this milestone exists to produce**: which `events` values and game types actually occur, and how far Statcast and boxscore PA counts disagree, with any resulting change to Decisions 5–7 or P1M2–P1M5). The Rill project file syntax (`rill.yaml`, `connectors/duckdb.yaml`'s `init_sql` ATTACH, the metrics-view/explore YAML) is written from Rill's documented reference as found via search, not confirmed against a running `rill start`; `explore/connectors/duckdb.yaml` and the `explore` service in `docker-compose.yml` both say so and point back here. **Before P1M2 starts:** someone with R2 read access and a working Rill install must run the two commands above, fix whatever the untested connector/YAML syntax gets wrong, and record the actual findings in a follow-up entry here.

## References

- [Rill docs](https://docs.rilldata.com/) (DuckDB connector, metrics views, explore dashboards); verify details against these when building P1M1
- [Statcast Search CSV documentation](https://baseballsavant.mlb.com/csv-docs) (`events`, `woba_value`, `woba_denom`)
- [FanGraphs Baseball Glossary](https://www.fangraphs.com/library/index.php/main-page/)
- [Baseball Reference Glossary](https://www.baseball-reference.com/about/glossary.shtml)
- [Chadwick Bureau Register](https://baseballregister.chadwicks.com/)
- [dbt unit tests](https://docs.getdbt.com/docs/build/unit-tests)
- [dbt Metric Layer](https://docs.getdbt.com/docs/build/metrics) (considered; plain macros are simpler for one warehouse and no BI tool)
