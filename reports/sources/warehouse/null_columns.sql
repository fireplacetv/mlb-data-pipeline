-- Columns with no values at all in each staging model. A column that is suddenly 100% null
-- usually means the source renamed or dropped a field.
-- duckdb: summarize profiles every column of a table in one pass
with profile as (
    select 'stg_statcast__pitches' as model, column_name, null_percentage
    from (summarize staging.stg_statcast__pitches)
    union all
    select 'stg_mlb__games', column_name, null_percentage
    from (summarize staging.stg_mlb__games)
    union all
    select 'stg_mlb__boxscore_batters', column_name, null_percentage
    from (summarize staging.stg_mlb__boxscore_batters)
    union all
    select 'stg_mlb__boxscore_pitchers', column_name, null_percentage
    from (summarize staging.stg_mlb__boxscore_pitchers)
    union all
    select 'stg_mlb__standings', column_name, null_percentage
    from (summarize staging.stg_mlb__standings)
    union all
    select 'stg_mlb__teams', column_name, null_percentage
    from (summarize staging.stg_mlb__teams)
    union all
    select 'stg_mlb__rosters', column_name, null_percentage
    from (summarize staging.stg_mlb__rosters)
    union all
    select 'stg_mlb__people', column_name, null_percentage
    from (summarize staging.stg_mlb__people)
)

select model, column_name
from profile
where null_percentage = 100
