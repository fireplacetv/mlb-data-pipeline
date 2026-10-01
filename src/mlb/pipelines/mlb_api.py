"""MLB Stats API pipeline: schedule, boxscores, snapshots -> Parquet lake (ARCHITECTURE.md §6.4).

Run: python -m mlb.pipelines.mlb_api [--start YYYY-MM-DD --end YYYY-MM-DD | --chunk-days N]

A run is several dlt loads, one per step, all under the `mlb_api` source:
1. One load per in-season day: `schedule` and the boxscores of that day's Final games.
   A failed day is logged and skipped (§6.4).
2. One snapshot load: `teams` for every season the window touches, and `standings` and
   `rosters` as of the window's end date.
3. One final load: `people` bios for player ids not fetched before. It also saves the
   watermark and the fetched ids, so neither moves unless every earlier step finished.
"""

import argparse
import logging
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterator
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from itertools import batched
from pathlib import Path
from typing import Any

import dlt
import requests
from dlt.common.normalizers.json.relational import DataItemNormalizer as RelationalNormalizer
from dlt.sources import DltResource
from dlt.sources.rest_api import RESTAPIConfig, rest_api_resources
from dlt.sources.rest_api.config_setup import make_parent_key_name

from mlb import config
from mlb.pipelines import common

logger = logging.getLogger(__name__)

PIPELINE_NAME = "mlb_api"
DATASET_NAME = "raw_mlb"
SOURCE_NAME = "mlb_api"
PEOPLE_FETCHED_KEY = "people_fetched"

BASE_URL = "https://statsapi.mlb.com/api/"
SPORT_ID_MLB = 1
LEAGUE_IDS = "103,104"  # American League, National League
ROSTER_TYPE = "40Man"

# status.codedGameState values of a finished game: F = Final, O = Game Over. Postponed
# (D) and cancelled (C) games also report abstractGameState "Final", so it can't be used.
FINAL_CODED_STATES = frozenset({"F", "O"})

PEOPLE_BATCH_SIZE = 100
POLITE_DELAY_SECONDS = 1.0

Row = dict[str, Any]
Sleep = Callable[[float], None]


@dataclass
class RunOutcomes:
    """What happened during a run: days by outcome, player ids seen, rows per table."""

    loaded: list[date] = field(default_factory=list)
    off_season: list[date] = field(default_factory=list)
    failed: list[date] = field(default_factory=list)
    player_ids: set[int] = field(default_factory=set)
    row_counts: Counter[str] = field(default_factory=Counter)


# --- Row shaping (processing steps) -------------------------------------------


def is_final(game: Row) -> bool:
    """True if a schedule game is finished (Final or Game Over)."""
    return game.get("status", {}).get("codedGameState") in FINAL_CODED_STATES


def flatten_boxscore(box: Row, game_pk_key: str, player_ids: set[int]) -> Row:
    """Move both teams' players into one `players` list, each tagged with its game and team.

    The API keys players by "ID<person id>" under teams.away/home.players. Left as is,
    dlt would make a column per player per stat. As a list, dlt makes one child table,
    `boxscore__players`, with a row per player per game. The game_pk (which the
    boxscore response lacks) comes from the parent schedule row.
    """
    game_pk = box.pop(game_pk_key)
    players: list[Row] = []
    for side in ("away", "home"):
        team = box.get("teams", {}).get(side, {})
        team_id = team.get("team", {}).get("id")
        for player in team.pop("players", {}).values():
            players.append({"game_pk": game_pk, "team_id": team_id, "side": side, **player})
            player_ids.add(player["person"]["id"])
    return {"game_pk": game_pk, **box, "players": players}


def stamp_standings(record: Row, as_of: date) -> Row:
    """Add the requested as-of date to a division record and each of its team records."""
    stamp = {"as_of_date": as_of.isoformat()}
    team_records = [stamp | team for team in record.get("teamRecords", [])]
    return stamp | record | {"teamRecords": team_records}


def stamp_roster_entry(entry: Row, team_id_key: str, as_of: date, player_ids: set[int]) -> Row:
    """Add the team id (from the parent teams row) and roster date to a roster entry."""
    player_ids.add(entry["person"]["id"])
    return {"team_id": entry.pop(team_id_key), "roster_date": as_of.isoformat(), **entry}


# --- REST API source configs --------------------------------------------------


def client_config(session: requests.Session | None) -> dict[str, Any]:
    """REST client settings. Tests pass a session that serves recorded fixtures."""
    client: dict[str, Any] = {"base_url": BASE_URL}
    if session is not None:
        client["session"] = session
    return client


def rest_config(resources: list[Any], session: requests.Session | None) -> RESTAPIConfig:
    """Wrap resources in a config with shared defaults: append-only, no pagination."""
    return {
        "client": client_config(session),
        "resource_defaults": {
            "write_disposition": "append",
            "endpoint": {"paginator": "single_page"},
        },
        "resources": resources,
    }


def day_config(day: date, player_ids: set[int], session: requests.Session | None) -> RESTAPIConfig:
    """schedule (one row per game) and boxscores of Final games, for one day."""
    iso = day.isoformat()
    schedule_endpoint = {
        "path": "v1/schedule",
        "params": {"sportId": SPORT_ID_MLB, "startDate": iso, "endDate": iso},
        "data_selector": "dates[*].games[*]",
    }
    game_pk_key = make_parent_key_name("final_games", "gamePk")
    return rest_config(
        [
            {"name": "schedule", "endpoint": schedule_endpoint},
            # Not loaded: feeds boxscore with Final games only.
            {
                "name": "final_games",
                "selected": False,
                "endpoint": schedule_endpoint,
                "processing_steps": [{"filter": is_final}],
            },
            {
                "name": "boxscore",
                "endpoint": {
                    "path": "v1/game/{resources.final_games.gamePk}/boxscore",
                    "data_selector": "$",
                },
                "include_from_parent": ["gamePk"],
                "processing_steps": [
                    {"map": lambda box: flatten_boxscore(box, game_pk_key, player_ids)}
                ],
            },
        ],
        session,
    )


def seasons_touched(window: config.LoadWindow) -> list[int]:
    """Every season (calendar year) with a day in the window."""
    return list(range(window.start.year, window.end.year + 1))


def snapshot_config(
    window: config.LoadWindow, player_ids: set[int], session: requests.Session | None
) -> RESTAPIConfig:
    """teams per season touched; standings and rosters as of the window's end date."""
    as_of = window.end
    teams = [
        {
            "name": f"teams_{season}",
            "table_name": "teams",
            "endpoint": {
                "path": "v1/teams",
                "params": {"sportId": SPORT_ID_MLB, "season": season},
                "data_selector": "teams",
            },
        }
        for season in seasons_touched(window)
    ]
    roster_parent = f"teams_{as_of.year}"
    team_id_key = make_parent_key_name(roster_parent, "id")
    standings = {
        "name": "standings",
        "endpoint": {
            "path": "v1/standings",
            "params": {"leagueId": LEAGUE_IDS, "season": as_of.year, "date": as_of.isoformat()},
            "data_selector": "records",
        },
        "processing_steps": [{"map": lambda record: stamp_standings(record, as_of)}],
    }
    rosters = {
        "name": "rosters",
        "endpoint": {
            "path": f"v1/teams/{{resources.{roster_parent}.id}}/roster",
            "params": {"rosterType": ROSTER_TYPE, "date": as_of.isoformat()},
            "data_selector": "roster",
        },
        "include_from_parent": ["id"],
        "processing_steps": [
            {"map": lambda entry: stamp_roster_entry(entry, team_id_key, as_of, player_ids)}
        ],
    }
    return rest_config([*teams, standings, rosters], session)


def new_person_ids(
    player_ids: set[int],
    prior_mark: date | None,
    mark_key: str,
    window: config.LoadWindow,
    failed_days: list[date],
) -> DltResource:
    """Batches of player ids not fetched before. Also saves the mark at mark_key with this load.

    mark_key is common.WATERMARK_KEY for a catch-up/manual backfill, or
    common.BACKFILL_MARK_KEY for an automatic --chunk-days backfill, tracked separately
    since the two never share progress.
    """

    @dlt.resource(name="new_person_ids", selected=False)
    def batches() -> Iterator[list[Row]]:
        state = dlt.current.source_state()
        fetched = set(state.get(PEOPLE_FETCHED_KEY, []))
        new_ids = sorted(player_ids - fetched)
        logger.info("People: %d new player ids (%d fetched before)", len(new_ids), len(fetched))
        for chunk in batched(new_ids, PEOPLE_BATCH_SIZE):
            # A list: the REST API source's dependent resources iterate parent pages.
            yield [{"person_ids": ",".join(str(i) for i in chunk)}]
        # Saved with this load, so neither moves until the data has landed (§6.6).
        state[PEOPLE_FETCHED_KEY] = sorted(fetched | set(new_ids))
        new_mark = config.next_watermark(prior_mark, window, failed_days)
        if new_mark is not None:
            state[mark_key] = new_mark.isoformat()

    return batches


def people_config(ids: DltResource, session: requests.Session | None) -> RESTAPIConfig:
    """people bios, one request per batch of new ids."""
    return rest_config(
        [
            ids,
            {
                "name": "people",
                "endpoint": {
                    "path": "v1/people",
                    "params": {"personIds": "{resources.new_person_ids.person_ids}"},
                    "data_selector": "people",
                },
            },
        ],
        session,
    )


# dlt stamps _dlt_load_id on root rows only. Copy it to every nested table, so each
# child table can be filtered to completed loads and deduplicated on its own (§7.3).
LOAD_ID_PROPAGATION = {"propagation": {"root": {"_dlt_load_id": "_dlt_load_id"}}}


@dlt.source(name=SOURCE_NAME)
def mlb_api_source(rest_api_config: RESTAPIConfig) -> list[DltResource]:
    """One step of a run. Every step shares this source name, so they share state and schema."""
    RelationalNormalizer.update_normalizer_config(
        dlt.current.source_schema(), deepcopy(LOAD_ID_PROPAGATION)
    )
    return rest_api_resources(rest_api_config)


# --- Running --------------------------------------------------------------------


def build_pipeline(
    data_dir: Path, schema_dir: Path, lake: config.Lake | None = None
) -> dlt.Pipeline:
    """Create the mlb_api dlt pipeline writing Parquet to the lake."""
    return common.build_pipeline(PIPELINE_NAME, DATASET_NAME, data_dir, schema_dir, lake)


def stored_watermark(pipeline: dlt.Pipeline) -> date | None:
    """Return the watermark from the pipeline's local state."""
    return common.stored_watermark(pipeline, SOURCE_NAME)


def restore_watermark(pipeline: dlt.Pipeline) -> date | None:
    """Sync state from the lake (restores a deleted pipelines dir), then read the watermark."""
    return common.restore_watermark(pipeline, SOURCE_NAME)


def stored_backfill_mark(pipeline: dlt.Pipeline) -> date | None:
    """Return the automatic backfill's progress mark from the pipeline's local state."""
    return common.stored_watermark(pipeline, SOURCE_NAME, common.BACKFILL_MARK_KEY)


def restore_backfill_mark(pipeline: dlt.Pipeline) -> date | None:
    """Sync state from the lake, then read the automatic backfill's progress mark."""
    return common.restore_watermark(pipeline, SOURCE_NAME, common.BACKFILL_MARK_KEY)


def run_step(
    pipeline: dlt.Pipeline, rest_api_config: RESTAPIConfig, label: str, outcomes: RunOutcomes
) -> None:
    """Run one load, then log its step durations and add its row counts to outcomes."""
    load_info = pipeline.run(mlb_api_source(rest_api_config), loader_file_format="parquet")
    counts = common.last_row_counts(pipeline)
    outcomes.row_counts.update(counts)
    common.log_step_durations(pipeline, label)
    logger.info(
        "%s: %s (load ids: %s)",
        label,
        ", ".join(f"{t}={n}" for t, n in sorted(counts.items())) or "no rows",
        ", ".join(load_info.loads_ids) or "none",
    )


def load_days(
    pipeline: dlt.Pipeline,
    window: config.LoadWindow,
    session: requests.Session | None,
    sleep: Sleep,
    outcomes: RunOutcomes,
) -> None:
    """Load schedule and boxscores one day at a time, isolating per-day failures."""
    fetched_any = False
    for day in window.days():
        if not config.in_season(day):
            outcomes.off_season.append(day)
            continue
        if fetched_any:
            sleep(POLITE_DELAY_SECONDS)
        fetched_any = True
        try:
            run_step(pipeline, day_config(day, outcomes.player_ids, session), str(day), outcomes)
        except Exception:
            logger.exception("%s: failed; skipping", day)
            outcomes.failed.append(day)
            continue
        outcomes.loaded.append(day)


def log_outcomes(outcomes: RunOutcomes) -> None:
    """Log per-day counts of loaded, off-season, and failed days."""
    logger.info(
        "Days: %d loaded, %d off-season, %d failed",
        len(outcomes.loaded),
        len(outcomes.off_season),
        len(outcomes.failed),
    )


def run(
    start: date | None = None,
    end: date | None = None,
    *,
    chunk_days: int | None = None,
    data_dir: Path = config.MLB_DATA_DIR,
    schema_dir: Path = config.SCHEMA_EXPORT_DIR,
    today: date | None = None,
    lake: config.Lake | None = None,
    session: requests.Session | None = None,
    sleep: Sleep = time.sleep,
) -> int:
    """Load the chosen window into the lake. Returns the process exit code.

    chunk_days runs an automatic backfill instead of a catch-up/manual backfill: it loads
    chunk_days days starting after the separately-tracked backfilled_through mark (or
    config.BACKFILL_START on the first run), then stops. Returns 0 with nothing loaded once
    the backfill reaches yesterday.
    """
    run_started = time.monotonic()
    try:
        pipeline = build_pipeline(data_dir, schema_dir, lake)
    except config.LakeConfigError as exc:
        logger.error("%s", exc)
        return 2
    yesterday_ = config.yesterday(today)

    if chunk_days is not None:
        mark_key = common.BACKFILL_MARK_KEY
        prior_mark = restore_backfill_mark(pipeline)
        logger.info("Backfill progress (backfilled_through): %s", prior_mark or "none")
        window = config.next_backfill_window(
            prior_mark, config.BACKFILL_START, yesterday_, chunk_days
        )
        if window is None:
            logger.info("Backfill complete: reached yesterday (%s); nothing to do.", yesterday_)
            return 0
    else:
        mark_key = common.WATERMARK_KEY
        prior_mark = restore_watermark(pipeline)
        logger.info("Watermark (loaded_through): %s", prior_mark or "none")
        try:
            window = config.choose_window(prior_mark, yesterday_, start, end)
        except (config.CatchupGapError, ValueError) as exc:
            logger.error("%s", exc)
            return 2

    mode = "backfill" if window.backfill else "catch-up"
    logger.info("Window (%s): %s through %s", mode, window.start, window.end)

    outcomes = RunOutcomes()
    before = common.table_columns(pipeline)
    load_days(pipeline, window, session, sleep, outcomes)
    run_step(
        pipeline,
        snapshot_config(window, outcomes.player_ids, session),
        f"snapshots as of {window.end}",
        outcomes,
    )
    ids = new_person_ids(outcomes.player_ids, prior_mark, mark_key, window, outcomes.failed)
    run_step(pipeline, people_config(ids, session), "people", outcomes)

    common.log_schema_changes(before, common.table_columns(pipeline))
    common.log_row_counts(dict(outcomes.row_counts))
    log_outcomes(outcomes)
    new_mark = common.stored_watermark(pipeline, SOURCE_NAME, mark_key)
    logger.info(
        "%s now: %s", "Backfill progress" if chunk_days else "Watermark", new_mark or "none"
    )
    logger.info("Total duration %.1fs", time.monotonic() - run_started)

    if outcomes.failed:
        failed = ", ".join(d.isoformat() for d in outcomes.failed)
        retry = (
            "the next --chunk-days firing will retry this chunk"
            if chunk_days
            else "re-run with --start/--end to retry"
        )
        logger.error("Failed days (%s): %s", retry, failed)
        return 1
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse --start/--end/--chunk-days flags."""
    parser = argparse.ArgumentParser(description="Load MLB Stats API data into the lake.")
    parser.add_argument("--start", type=date.fromisoformat, help="first day (YYYY-MM-DD)")
    parser.add_argument("--end", type=date.fromisoformat, help="last day (YYYY-MM-DD)")
    parser.add_argument(
        "--chunk-days",
        type=int,
        nargs="?",
        const=config.BACKFILL_CHUNK_DAYS,
        default=None,
        help=(
            "automatic backfill: load N days from backfilled_through, then stop "
            f"(BACKFILL_CHUNK_DAYS={config.BACKFILL_CHUNK_DAYS} if given with no N)"
        ),
    )
    args = parser.parse_args(argv)
    if (args.start is None) != (args.end is None):
        parser.error("--start and --end must be given together")
    if args.chunk_days is not None and (args.start is not None or args.end is not None):
        parser.error("--chunk-days cannot be combined with --start/--end")
    if args.chunk_days is not None and args.chunk_days < 1:
        parser.error(f"--chunk-days must be at least 1, got {args.chunk_days}")
    return args


def main(argv: list[str] | None = None) -> int:
    """Entry point: set up logging and directories, then run."""
    args = parse_args(argv)
    config.setup_logging(PIPELINE_NAME)
    config.ensure_directories()
    return run(args.start, args.end, chunk_days=args.chunk_days)


if __name__ == "__main__":
    sys.exit(main())
