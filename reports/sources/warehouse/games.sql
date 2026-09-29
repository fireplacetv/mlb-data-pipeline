-- One row per scheduled game, with how many Statcast pitches and boxscore players landed for it.
with pitches as (
    select game_pk, count(*) as pitches, max(inning) as innings_with_pitches
    from staging.stg_statcast__pitches
    group by game_pk
),

batters as (
    select game_pk, count(*) as batters
    from staging.stg_mlb__boxscore_batters
    group by game_pk
),

pitchers as (
    select game_pk, count(*) as pitchers
    from staging.stg_mlb__boxscore_pitchers
    group by game_pk
)

select
    g.game_pk,
    g.game_date,
    g.game_type,
    g.away_team_name || ' @ ' || g.home_team_name as matchup,
    g.away_score,
    g.home_score,
    g.detailed_state,
    g.coded_game_state,
    coalesce(p.pitches, 0) as pitches,
    p.innings_with_pitches,
    coalesce(b.batters, 0) as batters,
    coalesce(pp.pitchers, 0) as pitchers
from staging.stg_mlb__games as g
left join pitches as p on g.game_pk = p.game_pk
left join batters as b on g.game_pk = b.game_pk
left join pitchers as pp on g.game_pk = pp.game_pk
order by g.game_date, g.game_pk
