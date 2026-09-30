-- Division standings as of the latest date loaded, with each team's division from stg_mlb__teams.
with latest as (
    select max(as_of_date) as as_of_date
    from staging.stg_mlb__standings
),

standings as (
    select s.*
    from staging.stg_mlb__standings as s
    inner join latest as l
        on s.as_of_date = l.as_of_date
),

teams as (
    select team_id, season, league_name, division_name
    from staging.stg_mlb__teams
)

select
    cast(s.as_of_date as varchar) as as_of_date,
    t.league_name,
    coalesce(t.division_name, 'Unknown division') as division_name,
    s.division_rank,
    s.team_name,
    s.wins,
    s.losses,
    s.winning_percentage,
    s.games_back,
    s.streak_code
from standings as s
left join teams as t
    on s.team_id = t.team_id
    and s.season = t.season
order by t.league_name, t.division_name, s.division_rank
