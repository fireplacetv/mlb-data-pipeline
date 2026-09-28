-- Gap check: Final regular-season and postseason games, dated on or before the newest
-- pitch in the lake, with no Statcast pitches. A hit means a hole in the Statcast lake.
{{ config(severity='warn') }}

with newest_pitch as (
    select max(game_date) as game_date
    from {{ ref('stg_statcast__pitches') }}
),

final_games as (
    select game_pk, game_date, game_type, away_team_name, home_team_name
    from {{ ref('stg_mlb__games') }}
    where coded_game_state in ('F', 'O')
        and game_type in ('R', 'F', 'D', 'L', 'W')
),

games_with_pitches as (
    select distinct game_pk
    from {{ ref('stg_statcast__pitches') }}
)

select g.*
from final_games as g
cross join newest_pitch as n
left join games_with_pitches as p
    on g.game_pk = p.game_pk
where g.game_date <= n.game_date
    and p.game_pk is null
