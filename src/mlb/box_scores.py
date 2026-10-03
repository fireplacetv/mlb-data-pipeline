"""Log the box scores of one day's Final games, read back from the lake, for a quick check.

Run: python -m mlb.box_scores [--date YYYY-MM-DD]

With no --date, shows the last day the most recent mlb_api run loaded (the run records it
in <MLB_DATA_DIR>/mlb_api_last_day.txt). Everything comes from the lake, from that day's
latest completed load, so the output shows what was ingested rather than re-asking the API.
The scheduled workflows run it after ingesting, so each run's log ends with a box score
of every game on the final day it loaded.
"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any

import dlt

from mlb import config
from mlb.pipelines import mlb_api

logger = logging.getLogger(__name__)

Row = dict[str, Any]

NAME_WIDTH = 26
BATTING_COLUMNS = [
    ("AB", "stats__batting__at_bats"),
    ("R", "stats__batting__runs"),
    ("H", "stats__batting__hits"),
    ("RBI", "stats__batting__rbi"),
    ("BB", "stats__batting__base_on_balls"),
    ("SO", "stats__batting__strike_outs"),
    ("HR", "stats__batting__home_runs"),
]
PITCHING_COLUMNS = [
    ("IP", "stats__pitching__innings_pitched"),
    ("H", "stats__pitching__hits"),
    ("R", "stats__pitching__runs"),
    ("ER", "stats__pitching__earned_runs"),
    ("BB", "stats__pitching__base_on_balls"),
    ("SO", "stats__pitching__strike_outs"),
    ("HR", "stats__pitching__home_runs"),
    ("NP", "stats__pitching__number_of_pitches"),
]
LINE_SCORE_COLUMNS = [
    ("R", "team_stats__batting__runs"),
    ("H", "team_stats__batting__hits"),
    ("E", "team_stats__fielding__errors"),
]


# --- Reading the lake ---------------------------------------------------------


def last_loaded_day(data_dir: Path) -> date | None:
    """The last day the most recent mlb_api run loaded, or None if it loaded no day."""
    path = mlb_api.last_day_path(data_dir)
    if not path.exists():
        return None
    return date.fromisoformat(path.read_text().strip())


def query(dataset: Any, sql: str) -> list[Row]:
    """Run SQL against the lake dataset and return the rows as dicts."""
    return dataset.query(sql).arrow().to_pylist()


def day_load_id(dataset: Any, day: date) -> str | None:
    """The latest completed load holding the day's schedule (a day can be loaded again)."""
    rows = query(
        dataset,
        f"""
        select max(s._dlt_load_id) as load_id
        from schedule as s
        join _dlt_loads as l on s._dlt_load_id = l.load_id
        where l.status = 0 and s.official_date = '{day.isoformat()}'
        """,
    )
    return rows[0]["load_id"] if rows else None


def pitcher_order(dataset: Any, load_id: str) -> dict[tuple[int, str, int], int]:
    """Map (game_pk, side, person id) to the pitcher's place in the game's pitching order."""
    order: dict[tuple[int, str, int], int] = {}
    for side in ("away", "home"):
        rows = query(
            dataset,
            f"""
            select b.game_pk, p.value as person_id, p._dlt_list_idx as idx
            from boxscore__teams__{side}__pitchers as p
            join boxscore as b on p._dlt_parent_id = b._dlt_id
            where b._dlt_load_id = '{load_id}'
            """,
        )
        order.update({(r["game_pk"], side, r["person_id"]): r["idx"] for r in rows})
    return order


def read_day(pipeline: dlt.Pipeline, day: date) -> list[str]:
    """Return a formatted box score for each Final game on day, in scheduled order."""
    tables = pipeline.default_schema.tables if pipeline.default_schema_name else {}
    if "boxscore" not in tables:
        return []
    dataset = pipeline.dataset()
    load_id = day_load_id(dataset, day)
    if load_id is None:
        return []
    # select *: older days may lack columns newer days have, so pick them out in Python.
    games = query(
        dataset,
        f"""
        select b.*, s.status__detailed_state as detailed_state
        from boxscore as b
        join schedule as s on b.game_pk = s.game_pk and b._dlt_load_id = s._dlt_load_id
        where b._dlt_load_id = '{load_id}'
        order by s.game_date, s.game_pk
        """,
    )
    players = query(dataset, f"select * from boxscore__players where _dlt_load_id = '{load_id}'")
    order = pitcher_order(dataset, load_id)
    return [
        format_game(day, game, [p for p in players if p["game_pk"] == game["game_pk"]], order)
        for game in games
    ]


# --- Formatting -----------------------------------------------------------------


def cell(value: Any) -> str:
    """A stat as text; '-' when the lake has no value for it."""
    return "-" if value is None else str(value)


def table_row(label: str, values: list[Any]) -> str:
    """A fixed-width row: a left-aligned label, then right-aligned stat columns."""
    label = label if len(label) <= NAME_WIDTH else label[: NAME_WIDTH - 1] + "…"
    return f"{label:<{NAME_WIDTH}}" + "".join(f"{cell(v):>5}" for v in values)


def team_name(game: Row, side: str) -> str:
    """The team's name for a side of a boxscore row."""
    return cell(game.get(f"teams__{side}__team__name"))


def player_label(player: Row) -> str:
    """'Name POS', indented when the player came in as a substitute."""
    name = f"{cell(player.get('person__full_name'))} {cell(player.get('position__abbreviation'))}"
    batting_order = player.get("batting_order")
    is_sub = batting_order is not None and not str(batting_order).endswith("00")
    return f"  {name}" if is_sub else name


def batting_lines(players: list[Row]) -> list[str]:
    """One line per batter in batting order (starters and substitutes)."""
    batters = sorted(
        (p for p in players if p.get("batting_order") is not None),
        key=lambda p: int(p["batting_order"]),
    )
    return [table_row(player_label(p), [p.get(col) for _, col in BATTING_COLUMNS]) for p in batters]


def pitching_lines(
    players: list[Row], side: str, order: dict[tuple[int, str, int], int]
) -> list[str]:
    """One line per pitcher in the order they pitched."""
    pitchers = [p for p in players if p.get("stats__pitching__innings_pitched") is not None]
    pitchers.sort(key=lambda p: order.get((p["game_pk"], side, p["person__id"]), len(order)))
    return [
        table_row(cell(p.get("person__full_name")), [p.get(col) for _, col in PITCHING_COLUMNS])
        for p in pitchers
    ]


def format_game(
    day: date, game: Row, players: list[Row], order: dict[tuple[int, str, int], int]
) -> str:
    """A multi-line box score: header, line score, then each team's batting and pitching."""
    away, home = team_name(game, "away"), team_name(game, "home")
    lines = [
        f"{day.isoformat()}  {away} @ {home}  "
        f"({cell(game.get('detailed_state'))}, game {game['game_pk']})",
        table_row("", [header for header, _ in LINE_SCORE_COLUMNS]),
    ]
    for side in ("away", "home"):
        values = [game.get(f"teams__{side}__{col}") for _, col in LINE_SCORE_COLUMNS]
        lines.append(table_row(team_name(game, side), values))
    for side in ("away", "home"):
        side_players = [p for p in players if p.get("side") == side]
        lines += ["", team_name(game, side)]
        lines.append(table_row("Batting", [h for h, _ in BATTING_COLUMNS]))
        lines += batting_lines(side_players)
        lines.append(table_row("Pitching", [h for h, _ in PITCHING_COLUMNS]))
        lines += pitching_lines(side_players, side, order)
    return "\n".join(lines)


# --- Running --------------------------------------------------------------------


def run(
    day: date | None = None,
    *,
    data_dir: Path = config.MLB_DATA_DIR,
    schema_dir: Path = config.SCHEMA_EXPORT_DIR,
    lake: config.Lake | None = None,
) -> int:
    """Log the box scores for day (default: the last day mlb_api loaded). Returns the exit code."""
    day = day or last_loaded_day(data_dir)
    if day is None:
        logger.info("Box scores: the last mlb_api run loaded no in-season day; nothing to show.")
        return 0
    try:
        pipeline = mlb_api.build_pipeline(data_dir, schema_dir, lake)
    except config.LakeConfigError as exc:
        logger.error("%s", exc)
        return 2
    # Restores the schema from the lake if the local pipelines dir is missing.
    pipeline.sync_destination()
    box_scores = read_day(pipeline, day)
    logger.info("Box scores for %s: %d Final games in the lake", day, len(box_scores))
    for box_score in box_scores:
        logger.info("\n%s\n", box_score)
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse the optional --date flag."""
    parser = argparse.ArgumentParser(description="Log one day's box scores from the lake.")
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        help="day to show (YYYY-MM-DD); defaults to the last day the last mlb_api run loaded",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point: set up logging, then log the box scores."""
    args = parse_args(argv)
    config.setup_logging("box_scores")
    return run(args.date)


if __name__ == "__main__":
    sys.exit(main())
