"""Tests for the box score check: reads back what the mlb_api pipeline landed in the lake."""

import logging
from datetime import date
from pathlib import Path

import pytest
import requests

from mlb import box_scores
from mlb.pipelines import mlb_api
from tests.test_mlb_api import FakeStatsApi


def load(tmp_path: Path, start: date, end: date, *, today: date = date(2025, 9, 3)) -> int:
    """Run the mlb_api pipeline into tmp_path against the recorded fixtures."""
    session = requests.Session()
    session.mount("https://", FakeStatsApi())
    return mlb_api.run(
        start,
        end,
        data_dir=tmp_path,
        schema_dir=tmp_path / "schemas",
        today=today,
        session=session,
        sleep=lambda _seconds: None,
    )


def show(tmp_path: Path, day: date | None = None) -> int:
    return box_scores.run(day, data_dir=tmp_path, schema_dir=tmp_path / "schemas")


def box_score_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("\n")]


def test_run_records_last_day_loaded(tmp_path: Path) -> None:
    assert load(tmp_path, date(2025, 9, 1), date(2025, 9, 2)) == 0

    assert box_scores.last_loaded_day(tmp_path) == date(2025, 9, 2)


def test_run_loading_no_day_clears_previous_record(tmp_path: Path) -> None:
    load(tmp_path, date(2025, 9, 1), date(2025, 9, 2))
    # December is off-season: no day is loaded, so no box scores to show.
    assert load(tmp_path, date(2025, 12, 1), date(2025, 12, 2), today=date(2026, 1, 1)) == 0

    assert box_scores.last_loaded_day(tmp_path) is None


def test_defaults_to_last_day_loaded(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    load(tmp_path, date(2025, 9, 1), date(2025, 9, 2))

    with caplog.at_level(logging.INFO):
        assert show(tmp_path) == 0

    assert "Box scores for 2025-09-02: 1 Final games in the lake" in caplog.text
    [box] = box_score_messages(caplog)
    assert "2025-09-02  Los Angeles Dodgers @ San Francisco Giants  (Final, game 776601)" in box
    for name in ("Shohei Ohtani DH", "Tyler Glasnow", "Matt Chapman 3B", "Justin Verlander"):
        assert name in box


def test_shows_every_final_game_of_an_explicit_day(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    load(tmp_path, date(2025, 9, 1), date(2025, 9, 2))

    with caplog.at_level(logging.INFO):
        show(tmp_path, date(2025, 9, 1))

    boxes = box_score_messages(caplog)
    # The doubleheader's two Final games; the postponed game has no box score.
    assert ["game 776501" in boxes[0], "game 776502" in boxes[1]] == [True, True]
    assert "Yoshinobu Yamamoto" in boxes[0]


def test_reloaded_day_shows_each_game_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    load(tmp_path, date(2025, 9, 2), date(2025, 9, 2))
    load(tmp_path, date(2025, 9, 2), date(2025, 9, 2))

    with caplog.at_level(logging.INFO):
        show(tmp_path)

    [box] = box_score_messages(caplog)
    assert box.count("Justin Verlander") == 1


def test_nothing_loaded_logs_and_exits_zero(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        assert show(tmp_path) == 0

    assert "loaded no in-season day" in caplog.text


def test_format_game_lays_out_line_score_and_player_lines() -> None:
    game = {
        "game_pk": 1,
        "detailed_state": "Final",
        "teams__away__team__name": "Away",
        "teams__home__team__name": "Home",
        "teams__away__team_stats__batting__runs": 3,
        "teams__away__team_stats__batting__hits": 7,
        "teams__away__team_stats__fielding__errors": 1,
        "teams__home__team_stats__batting__runs": 5,
        "teams__home__team_stats__batting__hits": 9,
        "teams__home__team_stats__fielding__errors": 0,
    }
    starter = {
        "game_pk": 1,
        "side": "home",
        "person__id": 10,
        "person__full_name": "Starter",
        "position__abbreviation": "SS",
        "batting_order": "100",
        "stats__batting__at_bats": 4,
        "stats__batting__hits": 2,
    }
    sub = starter | {"person__id": 11, "person__full_name": "Sub", "batting_order": "101"}
    closer = {
        "game_pk": 1,
        "side": "home",
        "person__id": 21,
        "person__full_name": "Closer",
        "stats__pitching__innings_pitched": "1.0",
    }
    opener = closer | {"person__id": 20, "person__full_name": "Opener"}
    order = {(1, "home", 20): 0, (1, "home", 21): 1}

    lines = box_scores.format_game(date(2025, 9, 1), game, [sub, closer, starter, opener], order)

    rows = lines.splitlines()
    assert rows[0] == "2025-09-01  Away @ Home  (Final, game 1)"
    assert rows[2].split() == ["Away", "3", "7", "1"]
    assert rows[3].split() == ["Home", "5", "9", "0"]
    starter_row = next(i for i, r in enumerate(rows) if r.startswith("Starter SS"))
    assert rows[starter_row + 1].startswith("  Sub SS")
    assert rows[starter_row].split()[2:5] == ["4", "-", "2"]
    assert rows.index(next(r for r in rows if r.startswith("Opener"))) < rows.index(
        next(r for r in rows if r.startswith("Closer"))
    )


def test_parse_args_date_is_optional() -> None:
    assert box_scores.parse_args([]).date is None
    assert box_scores.parse_args(["--date", "2025-09-01"]).date == date(2025, 9, 1)
