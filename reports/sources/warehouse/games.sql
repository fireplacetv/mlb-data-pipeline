-- One row per scheduled game, with how many Statcast pitches and boxscore players landed for it.
with pitches as (
    select game_pk, count(*) as pitches
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
),

teams as (
    select team_id, season, abbreviation
    from staging.stg_mlb__teams
)

select
    g.game_date,
    g.game_type,
    coalesce(away.abbreviation, g.away_team_name)
        || ' @ ' || coalesce(home.abbreviation, g.home_team_name) as matchup,
    g.detailed_state,
    g.coded_game_state,
    coalesce(p.pitches, 0) as pitches,
    coalesce(b.batters, 0) as batters,
    coalesce(pp.pitchers, 0) as pitchers
from staging.stg_mlb__games as g
left join teams as away on g.away_team_id = away.team_id and g.season = away.season
left join teams as home on g.home_team_id = home.team_id and g.season = home.season
left join pitches as p on g.game_pk = p.game_pk
left join batters as b on g.game_pk = b.game_pk
left join pitchers as pp on g.game_pk = pp.game_pk
order by g.game_date, g.game_pk
