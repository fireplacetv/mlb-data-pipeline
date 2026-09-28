with completed_loads as (
    select load_id
    from {{ ref('stg_dlt__completed_loads') }}
    where dataset = 'raw_statcast'
),

raw_rows as (
    select r.*
    from {{ source('raw_statcast', 'pitches') }} as r
    inner join completed_loads as l
        on r._dlt_load_id = l.load_id
),

-- Load ids are unix timestamps with a fraction; as strings they sort in load order (§7.3).
ranked as (
    select
        *,
        row_number() over (
            partition by game_pk, at_bat_number, pitch_number
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
        -- game and plate appearance
        cast(game_pk as integer)                                as game_pk,
        cast(game_date as date)                                 as game_date,
        cast(game_year as integer)                              as game_year,
        cast(game_type as varchar)                              as game_type,
        cast(home_team as varchar)                              as home_team,
        cast(away_team as varchar)                              as away_team,
        cast(at_bat_number as integer)                          as at_bat_number,
        cast(pitch_number as integer)                           as pitch_number,
        cast(inning as integer)                                 as inning,
        cast(inning_topbot as varchar)                          as inning_topbot,
        cast(balls as integer)                                  as balls,
        cast(strikes as integer)                                as strikes,
        cast(outs_when_up as integer)                           as outs_when_up,
        cast(n_thruorder_pitcher as integer)                    as n_thruorder_pitcher,
        cast(n_priorpa_thisgame_player_at_bat as integer)       as n_priorpa_thisgame_player_at_bat,

        -- players
        cast(batter as integer)                                 as batter_id,
        cast(pitcher as integer)                                as pitcher_id,
        cast(player_name as varchar)                            as player_name,
        cast(stand as varchar)                                  as stand,
        cast(p_throws as varchar)                               as p_throws,
        cast(age_bat as integer)                                as age_bat,
        cast(age_pit as integer)                                as age_pit,
        cast(age_bat_legacy as integer)                         as age_bat_legacy,
        cast(age_pit_legacy as integer)                         as age_pit_legacy,
        cast(batter_days_since_prev_game as integer)            as batter_days_since_prev_game,
        cast(batter_days_until_next_game as integer)            as batter_days_until_next_game,
        cast(pitcher_days_since_prev_game as integer)           as pitcher_days_since_prev_game,
        cast(pitcher_days_until_next_game as integer)           as pitcher_days_until_next_game,
        cast(on_1b as integer)                                  as on_1b_id,
        cast(on_2b as integer)                                  as on_2b_id,
        cast(on_3b as integer)                                  as on_3b_id,
        cast(fielder_2 as integer)                              as fielder_2_id,
        cast(fielder_3 as integer)                              as fielder_3_id,
        cast(fielder_4 as integer)                              as fielder_4_id,
        cast(fielder_5 as integer)                              as fielder_5_id,
        cast(fielder_6 as integer)                              as fielder_6_id,
        cast(fielder_7 as integer)                              as fielder_7_id,
        cast(fielder_8 as integer)                              as fielder_8_id,
        cast(fielder_9 as integer)                              as fielder_9_id,
        cast(if_fielding_alignment as varchar)                  as if_fielding_alignment,
        cast(of_fielding_alignment as varchar)                  as of_fielding_alignment,

        -- pitch
        cast(pitch_type as varchar)                             as pitch_type,
        cast(pitch_name as varchar)                             as pitch_name,
        cast(release_speed as double precision)                 as release_speed,
        cast(effective_speed as double precision)               as effective_speed,
        cast(release_spin_rate as double precision)             as release_spin_rate,
        cast(spin_axis as double precision)                     as spin_axis,
        cast(release_extension as double precision)             as release_extension,
        cast(release_pos_x as double precision)                 as release_pos_x,
        cast(release_pos_y as double precision)                 as release_pos_y,
        cast(release_pos_z as double precision)                 as release_pos_z,
        cast(arm_angle as double precision)                     as arm_angle,
        cast(vx0 as double precision)                           as vx0,
        cast(vy0 as double precision)                           as vy0,
        cast(vz0 as double precision)                           as vz0,
        cast(ax as double precision)                            as ax,
        cast(ay as double precision)                            as ay,
        cast(az as double precision)                            as az,
        cast(pfx_x as double precision)                         as pfx_x,
        cast(pfx_z as double precision)                         as pfx_z,
        cast(api_break_z_with_gravity as double precision)      as api_break_z_with_gravity,
        cast(api_break_x_arm as double precision)               as api_break_x_arm,
        cast(api_break_x_batter_in as double precision)         as api_break_x_batter_in,
        cast(plate_x as double precision)                       as plate_x,
        cast(plate_z as double precision)                       as plate_z,
        cast(sz_top as double precision)                        as sz_top,
        cast(sz_bot as double precision)                        as sz_bot,
        cast(zone as integer)                                   as zone,
        -- plate_x/plate_z and sz_top/sz_bot changed meaning in 2026 (ARCHITECTURE.md §11)
        case
            when cast(game_year as integer) >= 2026 then 'middle_of_plate'
            else 'front_of_plate'
        end                                                     as location_reference,

        -- result of the pitch
        cast(type as varchar)                                   as pitch_result_type,
        cast(description as varchar)                            as description,
        cast(events as varchar)                                 as events,
        cast(des as varchar)                                    as play_description,
        cast(sv_id as varchar)                                  as sv_id,

        -- swing
        cast(bat_speed as double precision)                     as bat_speed,
        cast(swing_length as double precision)                  as swing_length,
        cast(attack_angle as double precision)                  as attack_angle,
        cast(attack_direction as double precision)              as attack_direction,
        cast(swing_path_tilt as double precision)               as swing_path_tilt,
        cast(intercept_ball_minus_batter_pos_x_inches as double precision)
                                                                as intercept_ball_minus_batter_pos_x_inches,
        cast(intercept_ball_minus_batter_pos_y_inches as double precision)
                                                                as intercept_ball_minus_batter_pos_y_inches,

        -- batted ball
        cast(bb_type as varchar)                                as bb_type,
        cast(launch_speed as double precision)                  as launch_speed,
        cast(launch_angle as double precision)                  as launch_angle,
        cast(launch_speed_angle as integer)                     as launch_speed_angle,
        cast(hyper_speed as double precision)                   as hyper_speed,
        cast(hit_distance_sc as double precision)               as hit_distance_sc,
        cast(hit_location as integer)                           as hit_location,
        cast(hc_x as double precision)                          as hc_x,
        cast(hc_y as double precision)                          as hc_y,

        -- expected stats and run values
        cast(estimated_ba_using_speedangle as double precision)     as xba,
        cast(estimated_woba_using_speedangle as double precision)   as xwoba,
        cast(estimated_slg_using_speedangle as double precision)    as xslg,
        cast(woba_value as double precision)                    as woba_value,
        cast(woba_denom as integer)                             as woba_denom,
        cast(babip_value as integer)                            as babip_value,
        cast(iso_value as integer)                              as iso_value,
        cast(delta_run_exp as double precision)                 as delta_run_exp,
        cast(delta_pitcher_run_exp as double precision)         as delta_pitcher_run_exp,
        cast(delta_home_win_exp as double precision)            as delta_home_win_exp,
        cast(home_win_exp as double precision)                  as home_win_exp,
        cast(bat_win_exp as double precision)                   as bat_win_exp,

        -- score
        cast(home_score as integer)                             as home_score,
        cast(away_score as integer)                             as away_score,
        cast(bat_score as integer)                              as bat_score,
        cast(fld_score as integer)                              as fld_score,
        cast(post_home_score as integer)                        as post_home_score,
        cast(post_away_score as integer)                        as post_away_score,
        cast(post_bat_score as integer)                         as post_bat_score,
        cast(post_fld_score as integer)                         as post_fld_score,
        cast(home_score_diff as integer)                        as home_score_diff,
        cast(bat_score_diff as integer)                         as bat_score_diff,

        cast(_dlt_load_id as varchar)                           as _dlt_load_id
    from deduped
)

select * from renamed
