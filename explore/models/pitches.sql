-- Statcast pitches, as staged, for P1M1's check of which `events` values and pitch types
-- actually occur across the full history backfill, before building int_statcast__plate_appearances
-- and its pa_events seed in P1M3 (ARCHITECTURE.md Decisions 5-7, docs/roadmap/phase-1-modeling-layer.md).
select
    game_pk,
    game_date,
    game_year,
    game_type,
    home_team,
    away_team,
    at_bat_number,
    pitch_number,
    inning_topbot,
    pitch_type,
    pitch_name,
    events,
    woba_value,
    woba_denom
from staging.stg_statcast__pitches
