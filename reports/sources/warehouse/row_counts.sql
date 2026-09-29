-- Rows and date coverage per staging model. Dates are text so models without a date show
-- blank (a null date would render as 1970-01-01).
with counts as (
    select 'stg_statcast__pitches' as model, count(*) as row_count, min(game_date) as min_date, max(game_date) as max_date
    from staging.stg_statcast__pitches
    union all
    select 'stg_mlb__games', count(*), min(game_date), max(game_date)
    from staging.stg_mlb__games
    union all
    select 'stg_mlb__boxscore_batters', count(*), null, null
    from staging.stg_mlb__boxscore_batters
    union all
    select 'stg_mlb__boxscore_pitchers', count(*), null, null
    from staging.stg_mlb__boxscore_pitchers
    union all
    select 'stg_mlb__standings', count(*), min(as_of_date), max(as_of_date)
    from staging.stg_mlb__standings
    union all
    select 'stg_mlb__teams', count(*), null, null
    from staging.stg_mlb__teams
    union all
    select 'stg_mlb__rosters', count(*), min(roster_date), max(roster_date)
    from staging.stg_mlb__rosters
    union all
    select 'stg_mlb__people', count(*), null, null
    from staging.stg_mlb__people
    union all
    select 'stg_dlt__completed_loads', count(*), min(cast(loaded_at as date)), max(cast(loaded_at as date))
    from staging.stg_dlt__completed_loads
)

select
    model,
    row_count,
    cast(min_date as varchar) as min_date,
    cast(max_date as varchar) as max_date
from counts
