---
title: Staging data smell test
---

A quick look at what landed in the dbt staging layer (`data/warehouse/mlb.duckdb`). CI builds
this page from the day it ingests; locally it shows whatever is in your warehouse. Hard checks
live in the dbt tests; this page is for eyeballing the data.

```sql window
select
    min(min_date) as first_date,
    max(max_date) as last_date
from warehouse.row_counts
where model in ('stg_statcast__pitches', 'stg_mlb__games')
```

```sql totals
select
    (select count(*) from warehouse.pitches) as pitches,
    (select count(*) from warehouse.games) as games,
    (select count(*) from warehouse.games where coded_game_state in ('F', 'O')) as final_games,
    (select count(*) from warehouse.loads) as completed_loads
```

Games from <Value data={window} column=first_date /> to <Value data={window} column=last_date />.

<BigValue data={totals} value=pitches title="Pitches" />
<BigValue data={totals} value=games title="Games" />
<BigValue data={totals} value=final_games title="Final games" />
<BigValue data={totals} value=completed_loads title="Completed loads" />

```sql empty_models
select model
from warehouse.row_counts
where row_count = 0
```

```sql final_games_without_pitches
select game_pk, game_date, matchup, detailed_state
from warehouse.games
where coded_game_state in ('F', 'O')
    and game_type in ('R', 'F', 'D', 'L', 'W')
    and pitches = 0
```

{#if empty_models.length > 0}
<Alert status=negative>
Empty staging models: {empty_models.map((d) => d.model).join(', ')}
</Alert>
{/if}

{#if final_games_without_pitches.length > 0}
<Alert status=warning>
{final_games_without_pitches.length} final game(s) have no Statcast pitches. See the games table below.
</Alert>
{/if}

## Rows per model

```sql row_counts
select model, row_count, min_date, max_date
from warehouse.row_counts
order by row_count desc
```

<BarChart data={row_counts} x=model y=row_count swapXY=true sort=false yAxisTitle="rows" />

<DataTable data={row_counts} rows=all />

## Loads

Completed dlt loads that staging read. Rows from a load without a completion marker are ignored.

```sql loads
select dataset, schema_name, load_id, loaded_at
from warehouse.loads
order by loaded_at
```

<DataTable data={loads} rows=all />

## Games

Each game with the Statcast pitches and boxscore players that landed for it. A regular game has
roughly 250 to 350 pitches, 9 or more batters and 2 or more pitchers per team.

```sql games
select *
from warehouse.games
order by game_date, game_pk
```

<BarChart data={games} x=matchup y=pitches swapXY=true sort=false title="Pitches per game" />

<DataTable data={games} rows=all />

## Null rates: pitches

Percent null for every column of `stg_statcast__pitches`, worst first. Some columns are null by
design (batted-ball columns on pitches not put in play, `events` except on the last pitch of a
plate appearance). A column at 100% that shouldn't be usually means the source changed.

```sql pitch_nulls
select column_name, null_percentage
from warehouse.column_profile
where model = 'stg_statcast__pitches'
order by null_percentage desc, column_name
```

<BarChart
    data={pitch_nulls}
    x=column_name
    y=null_percentage
    swapXY=true
    sort=false
    yMax=100
    chartAreaHeight=1800
    yAxisTitle="% null"
/>

## Pitches

```sql pitch_mix
select coalesce(pitch_name, '(none)') as pitch_name, count(*) as pitches
from warehouse.pitches
group by 1
order by pitches desc
```

<BarChart data={pitch_mix} x=pitch_name y=pitches swapXY=true sort=false title="Pitch mix" />

```sql velocity
select
    pitch_name,
    count(*) as pitches,
    min(release_speed) as min_mph,
    quantile_cont(release_speed, 0.25) as p25_mph,
    median(release_speed) as median_mph,
    quantile_cont(release_speed, 0.75) as p75_mph,
    max(release_speed) as max_mph
from warehouse.pitches
where release_speed is not null
    and pitch_name is not null
group by pitch_name
order by median_mph desc
```

<BoxPlot
    data={velocity}
    name=pitch_name
    min=min_mph
    intervalBottom=p25_mph
    midpoint=median_mph
    intervalTop=p75_mph
    max=max_mph
    swapXY=true
    title="Velocity by pitch type (mph)"
/>

```sql locations
select plate_x, plate_z, pitch_name
from warehouse.pitches
where plate_x is not null
    and plate_z is not null
```

Pitch locations from the catcher's view, in feet. The box is an approximate strike zone; most
pitches should cluster around it.

<ScatterPlot
    data={locations}
    x=plate_x
    y=plate_z
    series=pitch_name
    xMin=-3
    xMax=3
    yMin=-1
    yMax=6
    pointSize=4
    title="Pitch location"
>
    <ReferenceArea xMin=-0.83 xMax=0.83 yMin=1.5 yMax=3.5 label="zone" color=negative opacity=0.15 />
</ScatterPlot>

## Batted balls

```sql batted_balls
select
    launch_angle,
    launch_speed,
    hc_x,
    -- flip Savant's y so home plate is at the bottom of the spray chart
    250 - hc_y as hc_y_up,
    coalesce(bb_type, '(none)') as bb_type
from warehouse.pitches
where pitch_result_type = 'X'
```

<ScatterPlot
    data={batted_balls}
    x=launch_angle
    y=launch_speed
    series=bb_type
    pointSize=5
    title="Exit velocity (mph) by launch angle (degrees)"
/>

<ScatterPlot
    data={batted_balls}
    x=hc_x
    y=hc_y_up
    series=bb_type
    pointSize=5
    title="Spray chart (Savant hit coordinates)"
/>

## Column profiles

Type and null percentage of every column in every staging model.

```sql column_profile
select model, column_name, column_type, null_percentage, approx_unique
from warehouse.column_profile
order by model, column_name
```

<DataTable data={column_profile} rows=25 search=true />

## Sample pitches

```sql sample_pitches
select *
from warehouse.pitches
order by game_pk, at_bat_number, pitch_number
limit 50
```

<DataTable data={sample_pitches} rows=10 />
