with completed_loads as (
    select load_id
    from {{ ref('stg_dlt__completed_loads') }}
    where dataset = 'raw_mlb'
),

raw_rows as (
    select r.*
    from {{ source('raw_mlb', 'schedule') }} as r
    inner join completed_loads as l
        on r._dlt_load_id = l.load_id
),

-- Latest load wins: a postponed game keeps its game_pk and reappears on its makeup date.
ranked as (
    select
        *,
        row_number() over (
            partition by game_pk
            order by _dlt_load_id desc
        ) as load_rank
    from raw_rows
),

deduped as (
    select *
    from ranked
    where load_rank = 1
),

renamed as (
    select
        cast(game_pk as integer)                                as game_pk,
        cast(game_guid as varchar)                              as game_guid,
        cast(official_date as date)                             as game_date,
        cast(game_date as timestamp with time zone)             as scheduled_start_at,
        cast(season as integer)                                 as season,
        cast(game_type as varchar)                              as game_type,
        cast(double_header as varchar)                          as double_header,
        cast(game_number as integer)                            as game_number,
        cast(day_night as varchar)                              as day_night,
        cast(scheduled_innings as integer)                      as scheduled_innings,
        cast(series_description as varchar)                     as series_description,
        cast(games_in_series as integer)                        as games_in_series,
        cast(series_game_number as integer)                     as series_game_number,
        cast(if_necessary as varchar)                           as if_necessary,
        cast(tiebreaker as varchar)                             as tiebreaker,
        cast(is_tie as boolean)                                 as is_tie,

        cast(status__abstract_game_state as varchar)            as abstract_game_state,
        cast(status__coded_game_state as varchar)               as coded_game_state,
        cast(status__detailed_state as varchar)                 as detailed_state,
        cast(status__status_code as varchar)                    as status_code,

        cast(venue__id as integer)                              as venue_id,
        cast(venue__name as varchar)                            as venue_name,

        cast(teams__home__team__id as integer)                  as home_team_id,
        cast(teams__home__team__name as varchar)                as home_team_name,
        cast(teams__home__score as integer)                     as home_score,
        cast(teams__home__is_winner as boolean)                 as home_is_winner,
        cast(teams__home__league_record__wins as integer)       as home_wins,
        cast(teams__home__league_record__losses as integer)     as home_losses,

        cast(teams__away__team__id as integer)                  as away_team_id,
        cast(teams__away__team__name as varchar)                as away_team_name,
        cast(teams__away__score as integer)                     as away_score,
        cast(teams__away__is_winner as boolean)                 as away_is_winner,
        cast(teams__away__league_record__wins as integer)       as away_wins,
        cast(teams__away__league_record__losses as integer)     as away_losses,

        cast(_dlt_load_id as varchar)                           as _dlt_load_id
    from deduped
)

select * from renamed
