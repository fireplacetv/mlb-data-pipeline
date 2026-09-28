with completed_loads as (
    select load_id
    from {{ ref('stg_dlt__completed_loads') }}
    where dataset = 'raw_mlb'
),

raw_rows as (
    select r.*
    from {{ source('raw_mlb', 'rosters') }} as r
    inner join completed_loads as l
        on r._dlt_load_id = l.load_id
),

ranked as (
    select
        *,
        row_number() over (
            partition by person__id, team_id, roster_date
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
        cast(person__id as integer)                             as player_id,
        cast(team_id as integer)                                as team_id,
        cast(roster_date as date)                               as roster_date,
        cast(person__full_name as varchar)                      as player_name,
        cast(jersey_number as varchar)                          as jersey_number,
        cast(position__code as varchar)                         as position_code,
        cast(position__abbreviation as varchar)                 as position_abbreviation,
        cast(position__type as varchar)                         as position_type,
        cast(status__code as varchar)                           as status_code,
        cast(status__description as varchar)                    as status_description,
        cast(parent_team_id as integer)                         as parent_team_id,
        cast(_dlt_load_id as varchar)                           as _dlt_load_id
    from deduped
)

select * from renamed
