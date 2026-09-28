with completed_loads as (
    select load_id
    from {{ ref('stg_dlt__completed_loads') }}
    where dataset = 'raw_mlb'
),

raw_rows as (
    select r.*
    from {{ source('raw_mlb', 'boxscore__players') }} as r
    inner join completed_loads as l
        on r._dlt_load_id = l.load_id
),

-- Players who didn't bat have an empty stats.batting object, so null batting columns.
batting_rows as (
    select *
    from raw_rows
    where stats__batting__games_played is not null
),

ranked as (
    select
        *,
        row_number() over (
            partition by game_pk, person__id
            order by _dlt_load_id desc
        ) as load_rank
    from batting_rows
),

deduped as (
    select *
    from ranked
    where load_rank = 1
),

renamed as (
    select
        cast(game_pk as integer)                                as game_pk,
        cast(person__id as integer)                             as player_id,
        cast(person__full_name as varchar)                      as player_name,
        cast(team_id as integer)                                as team_id,
        cast(side as varchar)                                   as side,
        cast(batting_order as varchar)                          as batting_order,
        cast(position__abbreviation as varchar)                 as position_abbreviation,
        cast(jersey_number as varchar)                          as jersey_number,
        cast(stats__batting__games_played as integer)           as games_played,
        cast(stats__batting__plate_appearances as integer)      as plate_appearances,
        cast(stats__batting__at_bats as integer)                as at_bats,
        cast(stats__batting__runs as integer)                   as runs,
        cast(stats__batting__hits as integer)                   as hits,
        cast(stats__batting__doubles as integer)                as doubles,
        cast(stats__batting__triples as integer)                as triples,
        cast(stats__batting__home_runs as integer)              as home_runs,
        cast(stats__batting__rbi as integer)                    as rbi,
        cast(stats__batting__base_on_balls as integer)          as base_on_balls,
        cast(stats__batting__strike_outs as integer)            as strike_outs,
        cast(stats__batting__summary as varchar)                as summary,
        cast(_dlt_load_id as varchar)                           as _dlt_load_id
    from deduped
)

select * from renamed
