"""Statcast pipeline: Baseball Savant pitch-level data -> Parquet lake (ARCHITECTURE.md §6.3).

Run: python -m mlb.pipelines.statcast [--start YYYY-MM-DD --end YYYY-MM-DD | --chunk-days N]
"""

import argparse
import logging
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import dlt
import pandas as pd
from dlt.common.pipeline import LoadInfo

from mlb import config
from mlb.pipelines import common

logger = logging.getLogger(__name__)

PIPELINE_NAME = "statcast"
DATASET_NAME = "raw_statcast"
SOURCE_NAME = "statcast"
WATERMARK_KEY = common.WATERMARK_KEY

# Hints only for key columns; everything else is inferred (§6.5). Types match what
# pybaseball returns with the locked pandas: nullable ints, and game_date as text
# (pybaseball's date sniffing skips pandas 3 "str" columns). Staging casts it.
KEY_COLUMN_HINTS: dict[str, dict[str, str]] = {
    "game_pk": {"data_type": "bigint"},
    "game_date": {"data_type": "text"},
    "batter": {"data_type": "bigint"},
    "pitcher": {"data_type": "bigint"},
    "at_bat_number": {"data_type": "bigint"},
    "pitch_number": {"data_type": "bigint"},
}

FETCH_ATTEMPTS = 3
RETRY_BASE_SECONDS = 5.0
POLITE_DELAY_SECONDS = 2.0

FetchDay = Callable[[date], pd.DataFrame]
Sleep = Callable[[float], None]


@dataclass
class DayOutcomes:
    """What happened to each day of the window during extraction."""

    loaded: dict[date, int] = field(default_factory=dict)
    empty: list[date] = field(default_factory=list)
    off_season: list[date] = field(default_factory=list)
    failed: list[date] = field(default_factory=list)


def fetch_statcast_day(day: date) -> pd.DataFrame:
    """Download one day of Statcast pitches from Savant via pybaseball."""
    import pybaseball

    iso = day.isoformat()
    return pybaseball.statcast(start_dt=iso, end_dt=iso, verbose=False, parallel=False)


def enable_pybaseball_cache(cache_dir: Path) -> None:
    """Turn on pybaseball's cache, stored under cache_dir so it survives containers."""
    import pybaseball

    cache_dir.mkdir(parents=True, exist_ok=True)
    pybaseball.cache.config.cache_directory = str(cache_dir)
    pybaseball.cache.enable()
    logger.info("pybaseball cache enabled at %s", cache_dir)


def fetch_with_retry(
    fetch: FetchDay, day: date, sleep: Sleep, attempts: int = FETCH_ATTEMPTS
) -> pd.DataFrame:
    """Call fetch(day), retrying with exponential backoff. Re-raises the last error."""
    for attempt in range(1, attempts + 1):
        try:
            return fetch(day)
        except Exception as exc:
            if attempt == attempts:
                raise
            delay = RETRY_BASE_SECONDS * 2 ** (attempt - 1)
            logger.warning(
                "%s: attempt %d/%d failed (%s); retrying in %.0fs",
                day,
                attempt,
                attempts,
                exc,
                delay,
            )
            sleep(delay)
    raise AssertionError("unreachable")


def extract_days(
    days: list[date], fetch: FetchDay, sleep: Sleep, outcomes: DayOutcomes
) -> Iterator[pd.DataFrame]:
    """Yield one DataFrame per in-season day with data, isolating per-day failures."""
    fetched_any = False
    for day in days:
        if not config.in_season(day):
            outcomes.off_season.append(day)
            continue
        if fetched_any:
            sleep(POLITE_DELAY_SECONDS)
        fetched_any = True
        try:
            frame = fetch_with_retry(fetch, day, sleep)
        except Exception:
            logger.exception("%s: failed after %d attempts; skipping", day, FETCH_ATTEMPTS)
            outcomes.failed.append(day)
            continue
        if frame is None or frame.empty:
            logger.info("%s: no pitches; skipping", day)
            outcomes.empty.append(day)
            continue
        logger.info("%s: %d pitches", day, len(frame))
        outcomes.loaded[day] = len(frame)
        yield frame


@dlt.source(name=SOURCE_NAME)
def statcast_source(
    window: config.LoadWindow,
    prior_mark: date | None,
    mark_key: str,
    fetch: FetchDay,
    sleep: Sleep,
    outcomes: DayOutcomes,
) -> dlt.sources.DltResource:
    """dlt source with the `pitches` resource for window; advances the mark at mark_key.

    mark_key is WATERMARK_KEY for a catch-up/manual backfill, or BACKFILL_MARK_KEY for an
    automatic --chunk-days backfill, tracked separately since the two never share progress.
    """

    @dlt.resource(name="pitches", write_disposition="append", columns=KEY_COLUMN_HINTS)
    def pitches() -> Iterator[pd.DataFrame]:
        yield from extract_days(window.days(), fetch, sleep, outcomes)
        # Saved with this load, so it only moves once the data has landed (§6.6).
        new_mark = config.next_watermark(prior_mark, window, outcomes.failed)
        if new_mark is not None:
            dlt.current.source_state()[mark_key] = new_mark.isoformat()

    return pitches


def build_pipeline(
    data_dir: Path, schema_dir: Path, lake: config.Lake | None = None
) -> dlt.Pipeline:
    """Create the statcast dlt pipeline writing Parquet to the lake."""
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


def log_run_summary(pipeline: dlt.Pipeline, load_info: LoadInfo) -> None:
    """Log row counts per table and the duration of each dlt step."""
    common.log_row_counts(common.last_row_counts(pipeline))
    common.log_step_durations(pipeline)
    logger.info("Load ids: %s", ", ".join(load_info.loads_ids))


def log_outcomes(outcomes: DayOutcomes) -> None:
    """Log per-day counts of loaded, empty, off-season, and failed days."""
    logger.info(
        "Days: %d loaded, %d empty, %d off-season, %d failed",
        len(outcomes.loaded),
        len(outcomes.empty),
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
    fetch: FetchDay = fetch_statcast_day,
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
        mark_key = WATERMARK_KEY
        prior_mark = restore_watermark(pipeline)
        logger.info("Watermark (loaded_through): %s", prior_mark or "none")
        try:
            window = config.choose_window(prior_mark, yesterday_, start, end)
        except (config.CatchupGapError, ValueError) as exc:
            logger.error("%s", exc)
            return 2

    mode = "backfill" if window.backfill else "catch-up"
    logger.info("Window (%s): %s through %s", mode, window.start, window.end)

    if window.backfill:
        enable_pybaseball_cache(data_dir / "cache" / "pybaseball")

    outcomes = DayOutcomes()
    before = common.table_columns(pipeline)
    source = statcast_source(window, prior_mark, mark_key, fetch, sleep, outcomes)
    load_info = pipeline.run(source, loader_file_format="parquet")
    common.log_schema_changes(before, common.table_columns(pipeline))
    log_run_summary(pipeline, load_info)
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
    parser = argparse.ArgumentParser(description="Load Statcast pitches into the lake.")
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
