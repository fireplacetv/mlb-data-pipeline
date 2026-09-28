"""Configuration: environment variables, paths, logging, and date-window helpers."""

import logging
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Environment variables
MLB_DATA_DIR = Path(os.getenv("MLB_DATA_DIR", "./data"))
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "4"))
MAX_CATCHUP_DAYS = int(os.getenv("MAX_CATCHUP_DAYS", "30"))
GIANTS_TEAM_ID = int(os.getenv("GIANTS_TEAM_ID", "137"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Data subdirectories
LAKE_DIR = MLB_DATA_DIR / "lake"
WAREHOUSE_DIR = MLB_DATA_DIR / "warehouse"
DLT_PIPELINES_DIR = MLB_DATA_DIR / "dlt_pipelines"
CACHE_DIR = MLB_DATA_DIR / "cache"
LOGS_DIR = MLB_DATA_DIR / "logs"

# DuckDB warehouse
WAREHOUSE_DB = WAREHOUSE_DIR / "mlb.duckdb"

# dlt schemas exported after every run, committed to git (ARCHITECTURE.md §6.5).
# Lives in the repo, not under MLB_DATA_DIR: src/mlb/config.py -> repo root.
SCHEMA_EXPORT_DIR = Path(__file__).resolve().parents[2] / "schemas" / "export"

# Dates clearly outside a season are skipped (month, day), inclusive. §6.3
SEASON_START = (2, 15)
SEASON_END = (11, 15)


def setup_logging(run_name: str | None = None, logs_dir: Path = LOGS_DIR) -> None:
    """Configure logging to stdout and, when run_name is given, to a file in logs_dir."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if run_name:
        logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        handlers.append(logging.FileHandler(logs_dir / f"{run_name}_{stamp}.log"))
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers,
        force=True,
    )


def ensure_directories() -> None:
    """Create data directories if they don't exist."""
    for directory in [LAKE_DIR, WAREHOUSE_DIR, DLT_PIPELINES_DIR, CACHE_DIR, LOGS_DIR]:
        directory.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Ensured directory exists: {directory}")

    logger.info(f"Data directory: {MLB_DATA_DIR}")


def log_config() -> None:
    """Log the loaded configuration."""
    logger.info(f"MLB_DATA_DIR: {MLB_DATA_DIR}")
    logger.info(f"LOOKBACK_DAYS: {LOOKBACK_DAYS}")
    logger.info(f"MAX_CATCHUP_DAYS: {MAX_CATCHUP_DAYS}")
    logger.info(f"GIANTS_TEAM_ID: {GIANTS_TEAM_ID}")


def yesterday(today: date | None = None) -> date:
    """Return yesterday's date in local time (the container sets TZ)."""
    return (today or date.today()) - timedelta(days=1)


# --- Date windows and watermarks (ARCHITECTURE.md §6.6) ---------------------


class CatchupGapError(Exception):
    """The gap since the last load is too large for a catch-up; a backfill is needed."""


@dataclass(frozen=True)
class LoadWindow:
    """An inclusive range of days to load, and whether it was an explicit backfill."""

    start: date
    end: date
    backfill: bool

    def days(self) -> list[date]:
        """Every day in the window, in order."""
        return [self.start + timedelta(days=i) for i in range((self.end - self.start).days + 1)]


def choose_window(
    loaded_through: date | None,
    yesterday_: date,
    start: date | None = None,
    end: date | None = None,
    lookback_days: int = LOOKBACK_DAYS,
    max_catchup_days: int = MAX_CATCHUP_DAYS,
) -> LoadWindow:
    """Choose the days to load from the flags and the stored watermark.

    Raises CatchupGapError when a catch-up would span more than max_catchup_days.
    """
    if start is not None or end is not None:
        return _backfill_window(start, end, yesterday_)

    if loaded_through is None:
        logger.warning(
            "No watermark found (first run). Loading the last %d days only. "
            "For history, run a backfill: --start YYYY-MM-DD --end %s",
            lookback_days,
            yesterday_.isoformat(),
        )
        return LoadWindow(yesterday_ - timedelta(days=lookback_days), yesterday_, backfill=False)

    gap = (yesterday_ - loaded_through).days
    if gap > max_catchup_days:
        raise CatchupGapError(
            f"Last loaded through {loaded_through.isoformat()}, {gap} days ago "
            f"(MAX_CATCHUP_DAYS={max_catchup_days}). Run a backfill instead: "
            f"--start {(loaded_through + timedelta(days=1)).isoformat()} "
            f"--end {yesterday_.isoformat()}"
        )
    return LoadWindow(loaded_through - timedelta(days=lookback_days), yesterday_, backfill=False)


def _backfill_window(start: date | None, end: date | None, yesterday_: date) -> LoadWindow:
    """Validate explicit --start/--end flags and return them as a backfill window."""
    if start is None or end is None:
        raise ValueError("--start and --end must be given together")
    if start > end:
        raise ValueError(f"--start {start} is after --end {end}")
    if end > yesterday_:
        raise ValueError(f"--end {end} is after yesterday ({yesterday_}); only past days load")
    return LoadWindow(start, end, backfill=True)


def next_watermark(
    loaded_through: date | None, window: LoadWindow, failed_days: list[date]
) -> date | None:
    """Return the watermark after loading window, given the days that failed.

    Catch-up: advance to the day before the first failed day (or the window end).
    Backfill: advance to the window end only if it connects to the watermark
    (starts on or before loaded_through + 1, or no watermark exists yet) and no
    day failed. The watermark never moves backward.
    """
    if window.backfill:
        connected = loaded_through is None or window.start <= loaded_through + timedelta(days=1)
        if failed_days or not connected:
            return loaded_through
        candidate = window.end
    else:
        first_failed = min(failed_days, default=None)
        candidate = first_failed - timedelta(days=1) if first_failed else window.end
        if candidate < window.start and loaded_through is None:
            return None

    if loaded_through is None:
        return candidate
    return max(loaded_through, candidate)


def in_season(day: date) -> bool:
    """True unless the day is clearly outside a season (SEASON_START..SEASON_END)."""
    return SEASON_START <= (day.month, day.day) <= SEASON_END
