---
title: Staging data smell test
---

<script>
    // Set by CI when it builds the report; a local build has neither.
    const builtAt = import.meta.env.VITE_REPORT_BUILT_AT;
    const gitSha = import.meta.env.VITE_REPORT_GIT_SHA;
</script>

{#if builtAt && gitSha}
<small>Built {builtAt} from commit <code>{gitSha.slice(0, 7)}</code>.</small>
{:else}
<small>Local build: no build time or commit recorded.</small>
{/if}

What landed in the dbt staging layer. CI builds this page from the day it ingests; locally it
shows whatever is in `data/warehouse/mlb.duckdb`. The dbt tests are the pass/fail gate; this
page is for eyeballing volume, with the day's scores and standings for context.

```sql window
select
    min(min_date) as first_date,
    max(max_date) as last_date
from warehouse.row_counts
where model in ('stg_statcast__pitches', 'stg_mlb__games')
```

**Games from <Value data={window} column=first_date /> to <Value data={window} column=last_date />**

```sql empty_models
select model
from warehouse.row_counts
where row_count = 0
```

```sql final_games_without_pitches
select matchup
from warehouse.games
where coded_game_state in ('F', 'O')
    and game_type in ('R', 'F', 'D', 'L', 'W')
    and pitches = 0
```

{#if empty_models.length > 0}
<Alert status=negative>
<b>Empty staging models:</b> {empty_models.map((d) => d.model).join(', ')}
</Alert>
{/if}

{#if final_games_without_pitches.length > 0}
<Alert status=warning>
<b>Final games with no Statcast pitches:</b> {final_games_without_pitches.map((d) => d.matchup).join(', ')}
</Alert>
{/if}

{#if empty_models.length === 0 && final_games_without_pitches.length === 0}
<Alert status=positive>
Every staging model has rows, and every final game has Statcast pitches.
</Alert>
{/if}

## Scores

Every game on the schedule, with how much data landed for it. A regular nine-inning game has
roughly 250 to 350 pitches, at least 9 batters and 2 pitchers per team, so about 20+ batters and
4+ pitchers in total. Games that aren't final show their status instead of a score.

```sql games
select
    game,
    pitches,
    batters,
    pitchers
from warehouse.games
order by game_date, matchup
```

<DataTable data={games} rows=all>
    <Column id=game />
    <Column id=pitches contentType=bar barColor="#a8c9f0" />
    <Column id=batters />
    <Column id=pitchers />
</DataTable>

## Standings

```sql standings
select
    as_of_date,
    division_name as division,
    team_name as team,
    wins,
    losses,
    winning_percentage,
    games_back,
    streak_code
from warehouse.standings
```

As of <Value data={standings} column=as_of_date emptySet=pass emptyMessage="no standings loaded" />.

<DataTable data={standings} rows=all groupBy=division emptySet=pass emptyMessage="No standings loaded.">
    <Column id=team />
    <Column id=wins title="W" />
    <Column id=losses title="L" />
    <Column id=winning_percentage title="Pct" fmt="#.000" />
    <Column id=games_back title="GB" />
    <Column id=streak_code title="Strk" />
</DataTable>

## Rows per model

```sql row_counts
select
    replace(model, 'stg_', '') as model,
    row_count,
    case
        when min_date is null then ''
        when min_date = max_date then min_date
        else min_date || ' to ' || max_date
    end as dates
from warehouse.row_counts
order by row_count desc
```

<DataTable data={row_counts} rows=all>
    <Column id=model />
    <Column id=row_count title="Rows" contentType=bar barColor="#a8c9f0" />
    <Column id=dates />
</DataTable>

## Columns with no values

Columns that are null in every row. Some can be null by design on a given day, but since CI
loads the same day every time, this list should stay the same from one report to the next. A
new entry usually means the source renamed or dropped a field.

```sql null_columns
select replace(model, 'stg_', '') as model, column_name
from warehouse.null_columns
order by model, column_name
```

<DataTable data={null_columns} rows=all emptySet=pass emptyMessage="Every column has at least one value.">
    <Column id=model />
    <Column id=column_name title="Column" />
</DataTable>
