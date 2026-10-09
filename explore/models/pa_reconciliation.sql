-- Per game per team (side), Statcast plate-appearance counts vs. boxscore plate appearances,
-- for P1M1's check of Decision 6 (ARCHITECTURE.md): how far the two sources disagree, and
-- where, before int_statcast__plate_appearances and its reconciliation test land in P1M3.
with games as (
    select game_pk, game_date, season, game_type, away_team_id, home_team_id
    from staging.stg_mlb__games
),

statcast_sides as (
    -- The pitch where `events` is set is the plate appearance's last pitch (one per PA).
    select
        p.game_pk,
        case when p.inning_topbot = 'Top' then 'away' else 'home' end as side,
        count(*) as statcast_pa_count
    from staging.stg_statcast__pitches as p
    where p.events is not null
    group by p.game_pk, case when p.inning_topbot = 'Top' then 'away' else 'home' end
),

boxscore_sides as (
    select
        b.game_pk,
        case when b.team_id = g.away_team_id then 'away' else 'home' end as side,
        sum(b.plate_appearances) as boxscore_pa_count
    from staging.stg_mlb__boxscore_batters as b
    inner join games as g on b.game_pk = g.game_pk
    group by b.game_pk, case when b.team_id = g.away_team_id then 'away' else 'home' end
),

-- A game can have one source's rows but not the other's (e.g. no Statcast pitches landed
-- yet), so union the (game_pk, side) pairs instead of inner-joining the two sources.
sides as (
    select game_pk, side from statcast_sides
    union
    select game_pk, side from boxscore_sides
)

select
    g.game_pk,
    g.game_date,
    g.season,
    g.game_type,
    sd.side,
    coalesce(st.statcast_pa_count, 0) as statcast_pa_count,
    coalesce(bx.boxscore_pa_count, 0) as boxscore_pa_count,
    coalesce(st.statcast_pa_count, 0) - coalesce(bx.boxscore_pa_count, 0) as pa_diff
from sides as sd
inner join games as g on sd.game_pk = g.game_pk
left join statcast_sides as st on sd.game_pk = st.game_pk and sd.side = st.side
left join boxscore_sides as bx on sd.game_pk = bx.game_pk and sd.side = bx.side
