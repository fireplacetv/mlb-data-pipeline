-- Each staging model has exactly one row per distinct natural key among raw rows from
-- completed loads: deduplication neither drops nor duplicates rows. Returns mismatches.
with completed_loads as (
    select dataset, load_id
    from {{ ref('stg_dlt__completed_loads') }}
),

pitches as (
    select
        'stg_statcast__pitches' as model,
        (select count(*) from {{ ref('stg_statcast__pitches') }}) as staging_rows,
        count(distinct (r.game_pk, r.at_bat_number, r.pitch_number)) as raw_keys
    from {{ source('raw_statcast', 'pitches') }} as r
    inner join completed_loads as l
        on r._dlt_load_id = l.load_id and l.dataset = 'raw_statcast'
),

games as (
    select
        'stg_mlb__games' as model,
        (select count(*) from {{ ref('stg_mlb__games') }}) as staging_rows,
        count(distinct r.game_pk) as raw_keys
    from {{ source('raw_mlb', 'schedule') }} as r
    inner join completed_loads as l
        on r._dlt_load_id = l.load_id and l.dataset = 'raw_mlb'
),

batters as (
    select
        'stg_mlb__boxscore_batters' as model,
        (select count(*) from {{ ref('stg_mlb__boxscore_batters') }}) as staging_rows,
        count(distinct (r.game_pk, r.person__id)) as raw_keys
    from {{ source('raw_mlb', 'boxscore__players') }} as r
    inner join completed_loads as l
        on r._dlt_load_id = l.load_id and l.dataset = 'raw_mlb'
    where r.stats__batting__games_played is not null
),

pitchers as (
    select
        'stg_mlb__boxscore_pitchers' as model,
        (select count(*) from {{ ref('stg_mlb__boxscore_pitchers') }}) as staging_rows,
        count(distinct (r.game_pk, r.person__id)) as raw_keys
    from {{ source('raw_mlb', 'boxscore__players') }} as r
    inner join completed_loads as l
        on r._dlt_load_id = l.load_id and l.dataset = 'raw_mlb'
    where r.stats__pitching__games_played is not null
),

standings as (
    select
        'stg_mlb__standings' as model,
        (select count(*) from {{ ref('stg_mlb__standings') }}) as staging_rows,
        count(distinct (r.team__id, r.as_of_date)) as raw_keys
    from {{ source('raw_mlb', 'standings__team_records') }} as r
    inner join completed_loads as l
        on r._dlt_load_id = l.load_id and l.dataset = 'raw_mlb'
),

teams as (
    select
        'stg_mlb__teams' as model,
        (select count(*) from {{ ref('stg_mlb__teams') }}) as staging_rows,
        count(distinct (r.id, r.season)) as raw_keys
    from {{ source('raw_mlb', 'teams') }} as r
    inner join completed_loads as l
        on r._dlt_load_id = l.load_id and l.dataset = 'raw_mlb'
),

rosters as (
    select
        'stg_mlb__rosters' as model,
        (select count(*) from {{ ref('stg_mlb__rosters') }}) as staging_rows,
        count(distinct (r.person__id, r.team_id, r.roster_date)) as raw_keys
    from {{ source('raw_mlb', 'rosters') }} as r
    inner join completed_loads as l
        on r._dlt_load_id = l.load_id and l.dataset = 'raw_mlb'
),

people as (
    select
        'stg_mlb__people' as model,
        (select count(*) from {{ ref('stg_mlb__people') }}) as staging_rows,
        count(distinct r.id) as raw_keys
    from {{ source('raw_mlb', 'people') }} as r
    inner join completed_loads as l
        on r._dlt_load_id = l.load_id and l.dataset = 'raw_mlb'
),

all_models as (
    select * from pitches
    union all select * from games
    union all select * from batters
    union all select * from pitchers
    union all select * from standings
    union all select * from teams
    union all select * from rosters
    union all select * from people
)

select *
from all_models
where staging_rows != raw_keys
