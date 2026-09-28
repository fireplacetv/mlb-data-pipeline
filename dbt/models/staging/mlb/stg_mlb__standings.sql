with completed_loads as (
    select load_id
    from {{ ref('stg_dlt__completed_loads') }}
    where dataset = 'raw_mlb'
),

raw_rows as (
    select r.*
    from {{ source('raw_mlb', 'standings__team_records') }} as r
    inner join completed_loads as l
        on r._dlt_load_id = l.load_id
),

ranked as (
    select
        *,
        row_number() over (
            partition by team__id, as_of_date
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
        cast(team__id as integer)                               as team_id,
        cast(as_of_date as date)                                as as_of_date,
        cast(team__name as varchar)                             as team_name,
        cast(season as integer)                                 as season,
        cast(wins as integer)                                   as wins,
        cast(losses as integer)                                 as losses,
        cast(winning_percentage as double precision)            as winning_percentage,
        cast(games_played as integer)                           as games_played,
        cast(runs_scored as integer)                            as runs_scored,
        cast(runs_allowed as integer)                           as runs_allowed,
        cast(run_differential as integer)                       as run_differential,
        cast(division_rank as integer)                          as division_rank,
        cast(league_rank as integer)                            as league_rank,
        cast(sport_rank as integer)                             as sport_rank,
        -- Games back and elimination numbers use "-" for leaders and "E" for eliminated.
        cast(games_back as varchar)                             as games_back,
        cast(wild_card_games_back as varchar)                   as wild_card_games_back,
        cast(league_games_back as varchar)                      as league_games_back,
        cast(elimination_number as varchar)                     as elimination_number,
        cast(wild_card_elimination_number as varchar)           as wild_card_elimination_number,
        cast(division_leader as boolean)                        as division_leader,
        cast(division_champ as boolean)                         as division_champ,
        cast(has_wildcard as boolean)                           as has_wildcard,
        cast(clinched as boolean)                               as clinched,
        cast(streak__streak_code as varchar)                    as streak_code,
        cast(last_updated as timestamp with time zone)          as last_updated_at,
        cast(_dlt_load_id as varchar)                           as _dlt_load_id
    from deduped
)

select * from renamed
