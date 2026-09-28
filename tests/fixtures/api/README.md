# MLB Stats API fixtures

Trimmed responses for `tests/test_mlb_api.py`, which serves them through a fake HTTP adapter
(`FakeStatsApi`) so the pipeline runs end to end through dlt without network access.

| File | Endpoint | Contents |
|---|---|---|
| `schedule_2025-09-01.json` | `/api/v1/schedule?sportId=1&startDate=2025-09-01&endDate=2025-09-01` | A Dodgers @ Giants doubleheader (both Final) and a postponed game |
| `schedule_2025-09-02.json` | same, for 2025-09-02 | One Final game |
| `boxscore_<game_pk>.json` | `/api/v1/game/<game_pk>/boxscore` | One per Final game, two players per team (a batter and a pitcher) |
| `teams_2025.json` | `/api/v1/teams?sportId=1&season=2025` | Dodgers (119) and Giants (137) |
| `roster_<team_id>.json` | `/api/v1/teams/<team_id>/roster?rosterType=40Man&date=2025-09-02` | Five players each |
| `standings_2025-09-02.json` | `/api/v1/standings?leagueId=103,104&season=2025&date=2025-09-02` | The NL West record with two teams |
| `people.json` | `/api/v1/people?personIds=...` | Bios for every player above. The fake returns only the requested IDs. |

Days without a schedule fixture get an empty schedule (`"dates": []`), like an off day.

**Provenance:** the structure (field names, nesting, and types) follows the Stats API's
responses, but the values are hand-written: `game_pk`s are made up and stats are plausible,
not real. The environment that built M2 couldn't reach statsapi.mlb.com. To replace a fixture
with a real recording, run on a machine with access, then trim it (keep the same games and
players, or update the constants at the top of `tests/test_mlb_api.py`):

```bash
BASE=https://statsapi.mlb.com/api/v1
curl -s "$BASE/schedule?sportId=1&startDate=2025-09-01&endDate=2025-09-01" > tests/fixtures/api/schedule_2025-09-01.json
curl -s "$BASE/game/<game_pk>/boxscore" > tests/fixtures/api/boxscore_<game_pk>.json
curl -s "$BASE/teams?sportId=1&season=2025" > tests/fixtures/api/teams_2025.json
curl -s "$BASE/teams/137/roster?rosterType=40Man&date=2025-09-02" > tests/fixtures/api/roster_137.json
curl -s "$BASE/standings?leagueId=103,104&season=2025&date=2025-09-02" > tests/fixtures/api/standings_2025-09-02.json
curl -s "$BASE/people?personIds=660271,657277" > tests/fixtures/api/people.json
```
