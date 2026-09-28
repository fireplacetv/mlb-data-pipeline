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

-- Players who didn't pitch have an empty stats.pitching object, so null pitching columns.
pitching_rows as (
    select *
    from raw_rows
    where stats__pitching__games_played is not null
),

ranked as (
    select
        *,
        row_number() over (
            partition by game_pk, person__id
            order by _dlt_load_id desc
        ) as load_rank
    from pitching_rows
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
        cast(jersey_number as varchar)                          as jersey_number,
        cast(stats__pitching__games_played as integer)          as games_played,
        cast(stats__pitching__games_started as integer)         as games_started,
        -- Baseball notation: ".1" and ".2" are thirds of an inning, so kept as text.
        cast(stats__pitching__innings_pitched as varchar)       as innings_pitched,
        cast(stats__pitching__hits as integer)                  as hits,
        cast(stats__pitching__runs as integer)                  as runs,
        cast(stats__pitching__earned_runs as integer)           as earned_runs,
        cast(stats__pitching__base_on_balls as integer)         as base_on_balls,
        cast(stats__pitching__strike_outs as integer)           as strike_outs,
        cast(stats__pitching__number_of_pitches as integer)     as number_of_pitches,
        cast(stats__pitching__pitches_thrown as integer)        as pitches_thrown,
        cast(stats__pitching__summary as varchar)               as summary,
        cast(_dlt_load_id as varchar)                           as _dlt_load_id
    from deduped
)

select * from renamed
