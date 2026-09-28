with completed_loads as (
    select load_id
    from {{ ref('stg_dlt__completed_loads') }}
    where dataset = 'raw_mlb'
),

raw_rows as (
    select r.*
    from {{ source('raw_mlb', 'people') }} as r
    inner join completed_loads as l
        on r._dlt_load_id = l.load_id
),

ranked as (
    select
        *,
        row_number() over (
            partition by id
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
        cast(id as integer)                                     as player_id,
        cast(full_name as varchar)                              as full_name,
        cast(first_name as varchar)                             as first_name,
        cast(last_name as varchar)                              as last_name,
        cast(use_name as varchar)                               as use_name,
        cast(use_last_name as varchar)                          as use_last_name,
        cast(boxscore_name as varchar)                          as boxscore_name,
        cast(name_slug as varchar)                              as name_slug,
        cast(primary_number as varchar)                         as primary_number,
        cast(primary_position__code as varchar)                 as primary_position_code,
        cast(primary_position__abbreviation as varchar)         as primary_position_abbreviation,
        cast(bat_side__code as varchar)                         as bat_side,
        cast(pitch_hand__code as varchar)                       as pitch_hand,
        cast(gender as varchar)                                 as gender,
        cast(active as boolean)                                 as active,
        cast(is_player as boolean)                              as is_player,
        cast(_dlt_load_id as varchar)                           as _dlt_load_id
    from deduped
)

select * from renamed
