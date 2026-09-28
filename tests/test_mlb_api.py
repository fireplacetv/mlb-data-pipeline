"""Tests for the MLB Stats API pipeline, run end to end through dlt with recorded fixtures."""

import json
import shutil
from collections.abc import Callable
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pyarrow.parquet as pq
import pytest
import requests
from requests.adapters import BaseAdapter

from mlb.pipelines import mlb_api

FIXTURES = Path(__file__).parent / "fixtures" / "api"
FINAL_GAMES = {"2025-09-01": [776501, 776502], "2025-09-02": [776601]}
BOXSCORE_PLAYER_IDS = {660271, 656305, 808967, 657277, 477132, 592662, 607192, 434378}
ROSTER_PLAYER_IDS = BOXSCORE_PLAYER_IDS | {605141, 671218}


class FakeStatsApi(BaseAdapter):
    """Serves fixtures by path, records every request, and fails on request.

    Days without a schedule fixture get an empty schedule (an off day).
    """

    def __init__(self, failing_days: set[str] = frozenset()) -> None:
        super().__init__()
        self.failing_days = failing_days
        self.requests: list[tuple[str, dict[str, str]]] = []

    def send(self, request: requests.PreparedRequest, **kwargs: object) -> requests.Response:
        url = urlparse(request.url)
        path = url.path.removeprefix("/api/v1/")
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.requests.append((path, params))
        if params.get("startDate") in self.failing_days:
            raise requests.ConnectionError(f"statsapi timed out for {params['startDate']}")
        return self._respond(request, self._body(path, params))

    def _body(self, path: str, params: dict[str, str]) -> dict:
        parts = path.split("/")
        if path == "schedule":
            fixture = FIXTURES / f"schedule_{params['startDate']}.json"
            return read(fixture) if fixture.exists() else {"totalGames": 0, "dates": []}
        if parts[0] == "game" and parts[2] == "boxscore":
            return read(FIXTURES / f"boxscore_{parts[1]}.json")
        if path == "teams":
            return read(FIXTURES / "teams_2025.json")
        if parts[0] == "teams" and parts[2] == "roster":
            return read(FIXTURES / f"roster_{parts[1]}.json")
        if path == "standings":
            return read(FIXTURES / "standings_2025-09-02.json")
        if path == "people":
            wanted = {int(i) for i in params["personIds"].split(",")}
            people = read(FIXTURES / "people.json")["people"]
            return {"people": [p for p in people if p["id"] in wanted]}
        raise AssertionError(f"unexpected request: {path} {params}")

    @staticmethod
    def _respond(request: requests.PreparedRequest, body: dict) -> requests.Response:
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(body).encode()
        response.headers["Content-Type"] = "application/json"
        response.encoding = "utf-8"
        response.url = request.url
        response.request = request
        return response

    def close(self) -> None:
        pass

    def calls(self, path_prefix: str) -> list[tuple[str, dict[str, str]]]:
        return [(p, q) for p, q in self.requests if p.startswith(path_prefix)]

    def requested_person_ids(self) -> list[int]:
        return [int(i) for _, q in self.calls("people") for i in q["personIds"].split(",")]


def read(path: Path) -> dict:
    return json.loads(path.read_text())


@pytest.fixture
def run_pipeline(tmp_path: Path) -> Callable[..., int]:
    """Run the pipeline against tmp_path with the fake API and no sleeping."""

    def _run(
        start: date | None = None,
        end: date | None = None,
        *,
        today: date = date(2025, 9, 3),
        api: FakeStatsApi | None = None,
    ) -> int:
        session = requests.Session()
        session.mount("https://", api or FakeStatsApi())
        return mlb_api.run(
            start,
            end,
            data_dir=tmp_path,
            schema_dir=tmp_path / "schemas",
            today=today,
            session=session,
            sleep=lambda _seconds: None,
        )

    return _run


def lake(tmp_path: Path) -> Path:
    return tmp_path / "lake" / "raw_mlb"


def table(tmp_path: Path, name: str) -> list[dict]:
    return pq.read_table(lake(tmp_path) / name).to_pylist()


def watermark(tmp_path: Path) -> date | None:
    """Read the watermark the way the next run would, restoring from the lake."""
    return mlb_api.restore_watermark(mlb_api.build_pipeline(tmp_path, tmp_path / "schemas"))


def test_backfill_writes_expected_tables_and_markers(tmp_path: Path, run_pipeline) -> None:
    assert run_pipeline(date(2025, 9, 1), date(2025, 9, 2)) == 0

    tables = {p.name for p in lake(tmp_path).iterdir() if not p.name.startswith("_dlt")}
    assert {
        "schedule",
        "boxscore",
        "boxscore__players",
        "boxscore__players__all_positions",
        "boxscore__teams__away__batters",
        "boxscore__teams__home__pitchers",
        "boxscore__officials",
        "standings",
        "standings__team_records",
        "standings__team_records__records__split_records",
        "teams",
        "rosters",
        "people",
    } <= tables
    assert "final_games" not in tables and "new_person_ids" not in tables
    # Two days + snapshots + people: one completed-load marker each.
    assert len(list((lake(tmp_path) / "_dlt_loads").iterdir())) == 4
    assert (tmp_path / "schemas" / "mlb_api.schema.yaml").exists()


def test_child_tables_carry_load_id(tmp_path: Path, run_pipeline) -> None:
    run_pipeline(date(2025, 9, 1), date(2025, 9, 2))

    for name in (
        "boxscore__players",
        "boxscore__players__all_positions",
        "standings__team_records",
    ):
        rows = table(tmp_path, name)
        assert rows and all(r["_dlt_load_id"] for r in rows), name
    by_game = {(r["game_pk"], r["_dlt_load_id"]) for r in table(tmp_path, "boxscore__players")}
    root = {(r["game_pk"], r["_dlt_load_id"]) for r in table(tmp_path, "boxscore")}
    assert by_game == root


def test_schedule_has_one_row_per_game_in_a_single_load(tmp_path: Path, run_pipeline) -> None:
    run_pipeline(date(2025, 9, 1), date(2025, 9, 1))

    rows = table(tmp_path, "schedule")
    assert sorted(r["game_pk"] for r in rows) == [776501, 776502, 776503]
    assert len({r["_dlt_load_id"] for r in rows}) == 1
    # Nested objects flatten into columns on the game row.
    assert {r["status__detailed_state"] for r in rows} == {"Final", "Postponed"}
    assert {r["teams__home__team__id"] for r in rows} >= {137}


def test_boxscores_only_for_final_games(tmp_path: Path, run_pipeline) -> None:
    api = FakeStatsApi()
    run_pipeline(date(2025, 9, 1), date(2025, 9, 2), api=api)

    requested = sorted(int(path.split("/")[1]) for path, _ in api.calls("game/"))
    assert requested == [776501, 776502, 776601]  # postponed 776503 is never requested
    assert sorted(r["game_pk"] for r in table(tmp_path, "boxscore")) == requested


def test_boxscore_players_unnest_to_one_child_table(tmp_path: Path, run_pipeline) -> None:
    run_pipeline(date(2025, 9, 1), date(2025, 9, 1))

    players = table(tmp_path, "boxscore__players")
    # 2 games x 2 teams x 2 players, each tagged with its game and team.
    assert len(players) == 8
    assert {(p["game_pk"], p["team_id"], p["side"]) for p in players} == {
        (pk, team, side) for pk in (776501, 776502) for team, side in ((119, "away"), (137, "home"))
    }
    ohtani = next(p for p in players if p["person__id"] == 660271 and p["game_pk"] == 776501)
    assert ohtani["stats__batting__hits"] == 2
    assert ohtani["batting_order"] == "100"
    # No per-player columns on the boxscore row.
    box_columns = pq.read_schema(next((lake(tmp_path) / "boxscore").glob("*.parquet"))).names
    assert not [c for c in box_columns if "__id660271" in c or "players" in c]
    batters = table(tmp_path, "boxscore__teams__away__batters")
    assert {b["value"] for b in batters} == {660271, 808967, 477132}


def test_snapshots_are_taken_as_of_window_end(tmp_path: Path, run_pipeline) -> None:
    api = FakeStatsApi()
    run_pipeline(date(2025, 9, 1), date(2025, 9, 2), api=api)

    [(_, standings_params)] = api.calls("standings")
    assert standings_params == {"leagueId": "103,104", "season": "2025", "date": "2025-09-02"}
    roster_calls = api.calls("teams/")
    assert sorted(path for path, _ in roster_calls) == ["teams/119/roster", "teams/137/roster"]
    assert all(q == {"rosterType": "40Man", "date": "2025-09-02"} for _, q in roster_calls)
    assert [q for _, q in api.calls("teams") if _ == "teams"] == [
        {"sportId": "1", "season": "2025"}
    ]

    records = table(tmp_path, "standings__team_records")
    assert {r["as_of_date"] for r in records} == {"2025-09-02"}
    assert {r["team__id"] for r in records} == {119, 137}
    rosters = table(tmp_path, "rosters")
    assert {r["roster_date"] for r in rosters} == {"2025-09-02"}
    assert {(r["team_id"], r["person__id"]) for r in rosters} >= {(119, 660271), (137, 657277)}
    assert len(rosters) == 10


def test_teams_snapshot_for_every_season_touched(tmp_path: Path, run_pipeline) -> None:
    api = FakeStatsApi()
    assert run_pipeline(date(2024, 11, 14), date(2025, 2, 16), api=api) == 0

    seasons = [q["season"] for p, q in api.calls("teams") if p == "teams"]
    assert seasons == ["2024", "2025"]
    # Rosters and standings only for the end date's season.
    assert len(api.calls("teams/")) == 2
    assert [q["season"] for _, q in api.calls("standings")] == ["2025"]
    assert len(table(tmp_path, "teams")) == 4  # appended; staging keeps latest per season


def test_off_season_days_are_not_requested(tmp_path: Path, run_pipeline) -> None:
    api = FakeStatsApi()
    run_pipeline(date(2024, 11, 14), date(2024, 11, 20), api=api)

    days = [q["startDate"] for _, q in api.calls("schedule")]
    # Each day's schedule is read twice: once to load, once to pick Final games.
    assert sorted(set(days)) == ["2024-11-14", "2024-11-15"]


def test_people_fetches_only_new_ids(tmp_path: Path, run_pipeline) -> None:
    first = FakeStatsApi()
    run_pipeline(date(2025, 9, 1), date(2025, 9, 1), api=first)
    assert sorted(first.requested_person_ids()) == sorted(ROSTER_PLAYER_IDS)
    assert len(table(tmp_path, "people")) == len(ROSTER_PLAYER_IDS)

    second = FakeStatsApi()
    run_pipeline(date(2025, 9, 2), date(2025, 9, 2), api=second)
    assert second.calls("people") == []
    assert len(table(tmp_path, "people")) == len(ROSTER_PLAYER_IDS)


def test_people_fetched_ids_survive_deleting_pipelines_dir(tmp_path: Path, run_pipeline) -> None:
    run_pipeline(date(2025, 9, 1), date(2025, 9, 1))
    shutil.rmtree(tmp_path / "dlt_pipelines")

    api = FakeStatsApi()
    assert run_pipeline(today=date(2025, 9, 4), api=api) == 0

    assert api.calls("people") == []
    # Catch-up from the restored watermark 2025-09-01, minus 4 lookback days.
    assert min(q["startDate"] for _, q in api.calls("schedule")) == "2025-08-28"
    assert watermark(tmp_path) == date(2025, 9, 3)


def test_people_requests_are_batched() -> None:
    ids = mlb_api.new_person_ids(set(range(1, 251)), None, window_of(1), [])
    batches = list(ids)
    assert [len(b["person_ids"].split(",")) for b in batches] == [100, 100, 50]


def window_of(days: int) -> mlb_api.config.LoadWindow:
    return mlb_api.config.LoadWindow(date(2025, 9, 1), date(2025, 9, days), backfill=True)


def test_failed_day_is_isolated_and_exits_nonzero(tmp_path: Path, run_pipeline) -> None:
    run_pipeline(date(2025, 8, 31), date(2025, 8, 31))
    api = FakeStatsApi(failing_days={"2025-09-01"})

    assert run_pipeline(today=date(2025, 9, 3), api=api) == 1

    games = {r["game_pk"] for r in table(tmp_path, "schedule")}
    assert 776601 in games and 776501 not in games
    # Catch-up stops the watermark before the first failed day.
    assert watermark(tmp_path) == date(2025, 8, 31)


def test_backfill_sets_watermark(tmp_path: Path, run_pipeline) -> None:
    run_pipeline(date(2025, 9, 1), date(2025, 9, 2))
    assert watermark(tmp_path) == date(2025, 9, 2)


def test_invalid_window_exits_2_without_requests(tmp_path: Path, run_pipeline) -> None:
    api = FakeStatsApi()
    assert run_pipeline(date(2025, 9, 1), date(2025, 9, 5), api=api) == 2
    assert api.requests == []


@pytest.mark.parametrize(
    ("coded", "expected"),
    [("F", True), ("O", True), ("D", False), ("C", False), ("S", False), ("I", False)],
)
def test_is_final(coded: str, expected: bool) -> None:
    game = {"status": {"abstractGameState": "Final", "codedGameState": coded}}
    assert mlb_api.is_final(game) is expected


def test_parse_args_requires_both_dates() -> None:
    with pytest.raises(SystemExit):
        mlb_api.parse_args(["--end", "2025-09-02"])
    args = mlb_api.parse_args(["--start", "2025-09-01", "--end", "2025-09-02"])
    assert (args.start, args.end) == (date(2025, 9, 1), date(2025, 9, 2))
