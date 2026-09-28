with completed_loads as (
    select load_id
    from {{ ref('stg_dlt__completed_loads') }}
    where dataset = 'raw_mlb'
),

raw_rows as (
    select r.*
    from {{ source('raw_mlb', 'teams') }} as r
    inner join completed_loads as l
        on r._dlt_load_id = l.load_id
),

ranked as (
    select
        *,
        row_number() over (
            partition by id, season
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
        cast(id as integer)                                     as team_id,
        cast(season as integer)                                 as season,
        cast(name as varchar)                                   as team_full_name,
        cast(team_name as varchar)                              as team_name,
        cast(location_name as varchar)                          as location_name,
        cast(short_name as varchar)                             as short_name,
        cast(franchise_name as varchar)                         as franchise_name,
        cast(club_name as varchar)                              as club_name,
        cast(abbreviation as varchar)                           as abbreviation,
        cast(team_code as varchar)                              as team_code,
        cast(file_code as varchar)                              as file_code,
        cast(first_year_of_play as integer)                     as first_year_of_play,
        cast(active as boolean)                                 as active,
        cast(league__id as integer)                             as league_id,
        cast(league__name as varchar)                           as league_name,
        cast(division__id as integer)                           as division_id,
        cast(division__name as varchar)                         as division_name,
        cast(venue__id as integer)                              as venue_id,
        cast(venue__name as varchar)                            as venue_name,
        cast(sport__id as integer)                              as sport_id,
        cast(_dlt_load_id as varchar)                           as _dlt_load_id
    from deduped
)

select * from renamed
