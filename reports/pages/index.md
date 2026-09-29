---
title: Staging data smell test
---

What landed in the dbt staging layer. CI builds this page from the day it ingests; locally it
shows whatever is in `data/warehouse/mlb.duckdb`. The dbt tests are the pass/fail gate; this
page is for eyeballing.

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

**Games from <Value data={window} column=first_date /> to <Value data={window} column=last_date />**

<BigValue data={totals} value=pitches title="Pitches" />
<BigValue data={totals} value=games title="Games" />
<BigValue data={totals} value=final_games title="Final games" />
<BigValue data={totals} value=completed_loads title="Loads" />

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

## Games

A regular nine-inning game has roughly 250 to 350 pitches, at least 9 batters and 2 pitchers
per team, so about 20+ batters and 4+ pitchers in total.

```sql games
select
    matchup
        || case when coded_game_state in ('F', 'O') then '' else ' (' || detailed_state || ')' end
        as game,
    score,
    pitches,
    batters,
    pitchers
from warehouse.games
order by game_date, matchup
```

<BarChart
    data={games}
    x=game
    y=pitches
    swapXY=true
    sort=false
    yAxisTitle="pitches"
>
    <ReferenceArea yMin={250} yMax={350} label="typical" />
</BarChart>

Only games that aren't final show a status.

<DataTable data={games} rows=all>
    <Column id=game />
    <Column id=score align=center />
    <Column id=pitches />
    <Column id=batters />
    <Column id=pitchers />
</DataTable>

## Pitches

```sql locations
select
    plate_x,
    plate_z,
    case pitch_result_type
        when 'B' then 'Ball'
        when 'S' then 'Strike'
        when 'X' then 'In play'
    end as result
from warehouse.pitches
where plate_x is not null
    and plate_z is not null
```

Where each pitch crossed the plate, from the catcher's view (feet). The box is a typical strike
zone. Balls (hollow circles) should mostly land outside it, and strikes (dots) and balls in
play (triangles) inside or near it.

<ScatterPlot
    data={locations}
    x=plate_x
    y=plate_z
    series=result
    seriesOrder={['Ball', 'Strike', 'In play']}
    echartsOptions={{
        series: [{ symbol: 'emptyCircle' }, { symbol: 'circle' }, { symbol: 'triangle' }],
        legend: { data: [
            { name: 'Ball', icon: 'emptyCircle' },
            { name: 'Strike', icon: 'circle' },
            { name: 'In play', icon: 'triangle' }
        ] }
    }}
    xMin={-2.5}
    xMax={2.5}
    yMin={-0.5}
    yMax={5.5}
    pointSize={4}
    opacity={0.7}
    chartAreaHeight={380}
    xAxisTitle="horizontal (ft)"
    yAxisTitle="height (ft)"
    xFmt="0.0"
    yFmt="0.0"
>
    <ReferenceArea xMin={-0.83} xMax={0.83} yMin={1.5} yMax={3.5} label="zone" opacity={0.3} border={true} borderType=solid borderWidth={2} borderColor="#1f1f1f" />
</ScatterPlot>

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

Fastballs should sit in the mid 90s and curveballs near 80. Each box is the middle half of the
pitches; the whiskers are the slowest and fastest.

<BoxPlot
    data={velocity}
    name=pitch_name
    min=min_mph
    intervalBottom=p25_mph
    midpoint=median_mph
    intervalTop=p75_mph
    max=max_mph
    swapXY=true
    yAxisTitle="release speed (mph)"
/>

```sql pitch_mix
select coalesce(pitch_name, '(none)') as pitch_name, count(*) as pitches
from warehouse.pitches
group by 1
order by pitches desc
```

<BarChart data={pitch_mix} x=pitch_name y=pitches swapXY=true sort=false yAxisTitle="pitches" />

## Batted balls

```sql batted_balls
select
    launch_angle,
    launch_speed,
    hc_x,
    -- flip Savant's y so home plate is at the bottom of the spray chart
    250 - hc_y as hc_y_up,
    case bb_type
        when 'ground_ball' then 'Ground ball'
        when 'line_drive' then 'Line drive'
        when 'fly_ball' then 'Fly ball or popup'
        when 'popup' then 'Fly ball or popup'
    end as batted_ball
from warehouse.pitches
where pitch_result_type = 'X'
    and bb_type is not null
```

Ground balls (dots) should have negative launch angles, line drives (triangles) roughly 10 to
25 degrees, and fly balls (hollow circles) higher. Exit velocities top out around 115 mph.

<ScatterPlot
    data={batted_balls}
    x=launch_angle
    y=launch_speed
    series=batted_ball
    seriesOrder={['Ground ball', 'Line drive', 'Fly ball or popup']}
    echartsOptions={{
        series: [{ symbol: 'circle' }, { symbol: 'triangle' }, { symbol: 'emptyCircle' }],
        legend: { data: [
            { name: 'Ground ball', icon: 'circle' },
            { name: 'Line drive', icon: 'triangle' },
            { name: 'Fly ball or popup', icon: 'emptyCircle' }
        ] }
    }}
    pointSize={5}
    xAxisTitle="launch angle (degrees)"
    yAxisTitle="exit velocity (mph)"
/>

Where balls in play were fielded (Savant's hit coordinates, home plate at the bottom). It should
look like a baseball field: ground balls close in, fly balls to the outfield.

<ScatterPlot
    data={batted_balls}
    x=hc_x
    y=hc_y_up
    series=batted_ball
    seriesOrder={['Ground ball', 'Line drive', 'Fly ball or popup']}
    echartsOptions={{
        series: [{ symbol: 'circle' }, { symbol: 'triangle' }, { symbol: 'emptyCircle' }],
        legend: { data: [
            { name: 'Ground ball', icon: 'circle' },
            { name: 'Line drive', icon: 'triangle' },
            { name: 'Fly ball or popup', icon: 'emptyCircle' }
        ] }
    }}
    xMin={0}
    xMax={250}
    yMin={0}
    yMax={250}
    pointSize={5}
    chartAreaHeight={380}
    xAxisTitle="left to right"
    yAxisTitle="out from home plate"
/>

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

## Columns

Every column of every staging model, most-null first. Some columns are null by design:
batted-ball columns on pitches not put in play, `events` except on a plate appearance's last
pitch, runner ids with the bases empty. A column at 100% that shouldn't be, or a changed type,
usually means the source renamed or dropped a field. Search to filter by model or column.

```sql column_profile
select
    replace(model, 'stg_', '') as model,
    column_name,
    null_percentage / 100.0 as pct_null,
    lower(column_type) as type
from warehouse.column_profile
order by null_percentage desc, model, column_name
```

<DataTable data={column_profile} rows=15 search=true>
    <Column id=column_name title="Column" />
    <Column id=pct_null title="Null" fmt=pct0 contentType=bar barColor="#a8c9f0" />
    <Column id=model />
    <Column id=type />
</DataTable>

## Loads and sample rows

```sql loads
select dataset, load_id, strftime(loaded_at, '%Y-%m-%d %H:%M') as loaded_at_utc
from warehouse.loads
order by loaded_at
```

<Details title="Completed dlt loads">

Loads with a completion marker. Staging ignores rows from any other load.

<DataTable data={loads} rows=all>
    <Column id=dataset />
    <Column id=load_id title="Load id" />
    <Column id=loaded_at_utc title="Loaded (UTC)" />
</DataTable>

</Details>

```sql sample_pitches
select
    cast(game_pk as varchar) as game_pk,
    at_bat_number,
    pitch_number,
    player_name,
    pitch_name,
    release_speed,
    description,
    events
from warehouse.pitches
order by game_pk, at_bat_number, pitch_number
limit 100
```

<Details title="Sample pitches">

<DataTable data={sample_pitches} rows=10>
    <Column id=game_pk title="Game" />
    <Column id=at_bat_number title="PA" />
    <Column id=pitch_number title="Pitch" />
    <Column id=player_name title="Pitcher" />
    <Column id=pitch_name title="Type" />
    <Column id=release_speed title="mph" fmt="0.0" />
    <Column id=description title="Result" />
    <Column id=events title="PA outcome" />
</DataTable>

</Details>
