-- One row per player per game per stat type (batting or pitching), joined to the game's season,
-- type and date, for P1M1's check of boxscore stat coverage by season, team and game type
-- (docs/roadmap/phase-1-modeling-layer.md). Batting-only and pitching-only columns are null on
-- the other stat type's rows, so the two can share one metrics view.
with games as (
    select game_pk, game_date, season, game_type
    from staging.stg_mlb__games
),

teams as (
    select team_id, season, abbreviation
    from staging.stg_mlb__teams
),

batting as (
    select
        b.game_pk,
        g.game_date,
        g.season,
        g.game_type,
        b.team_id,
        t.abbreviation as team_abbreviation,
        b.player_id,
        b.player_name,
        'batting' as stat_type,
        b.plate_appearances,
        b.at_bats,
        b.hits,
        b.doubles,
        b.triples,
        b.home_runs,
        b.rbi,
        b.base_on_balls,
        b.strike_outs,
        cast(null as integer) as runs,
        cast(null as integer) as earned_runs,
        cast(null as varchar) as innings_pitched,
        cast(null as integer) as number_of_pitches
    from staging.stg_mlb__boxscore_batters as b
    inner join games as g on b.game_pk = g.game_pk
    left join teams as t on b.team_id = t.team_id and g.season = t.season
),

pitching as (
    select
        p.game_pk,
        g.game_date,
        g.season,
        g.game_type,
        p.team_id,
        t.abbreviation as team_abbreviation,
        p.player_id,
        p.player_name,
        'pitching' as stat_type,
        cast(null as integer) as plate_appearances,
        cast(null as integer) as at_bats,
        p.hits,
        cast(null as integer) as doubles,
        cast(null as integer) as triples,
        cast(null as integer) as home_runs,
        cast(null as integer) as rbi,
        p.base_on_balls,
        p.strike_outs,
        p.runs,
        p.earned_runs,
        p.innings_pitched,
        p.number_of_pitches
    from staging.stg_mlb__boxscore_pitchers as p
    inner join games as g on p.game_pk = g.game_pk
    left join teams as t on p.team_id = t.team_id and g.season = t.season
)

select * from batting
union all
select * from pitching
