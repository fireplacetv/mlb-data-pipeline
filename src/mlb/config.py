"""Configuration: environment variables, paths, logging, and date-window helpers."""

import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# Environment variables
MLB_DATA_DIR = Path(os.getenv("MLB_DATA_DIR", "./data"))
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "4"))
MAX_CATCHUP_DAYS = int(os.getenv("MAX_CATCHUP_DAYS", "30"))
GIANTS_TEAM_ID = int(os.getenv("GIANTS_TEAM_ID", "137"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Automatic backfill (ARCHITECTURE.md §6.6, docs/roadmap/phase-2-cloud-and-scale.md P2M4):
# where history starts, and how many days each --chunk-days firing covers.
BACKFILL_START = date.fromisoformat(os.getenv("BACKFILL_START", "2015-04-01"))
BACKFILL_CHUNK_DAYS = int(os.getenv("BACKFILL_CHUNK_DAYS", "30"))

# Lake destination (docs/roadmap/phase-2-cloud-and-scale.md, P2M1), in the order Cloudflare
# shows them. An empty S3_BUCKET_URL means the local lake under MLB_DATA_DIR; the bucket's
# R2 S3 API URL means the cloud lake, which needs IS_PROD=true.
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "").strip()
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
S3_BUCKET_URL = os.getenv("S3_BUCKET_URL", "").strip()
IS_PROD = os.getenv("IS_PROD", "false").strip().lower() in {"1", "true", "yes"}

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
    logger.info(f"IS_PROD: {IS_PROD}")
    logger.info(f"BACKFILL_START: {BACKFILL_START}")
    logger.info(f"BACKFILL_CHUNK_DAYS: {BACKFILL_CHUNK_DAYS}")


# --- Lake destination (docs/roadmap/phase-2-cloud-and-scale.md, P2M1) --------


class LakeConfigError(ValueError):
    """S3_BUCKET_URL, IS_PROD, and the credentials don't describe a usable lake."""


@dataclass(frozen=True)
class Lake:
    """Where dlt writes the lake: a local folder, or an S3-compatible bucket (R2)."""

    bucket_url: str
    remote: bool
    endpoint_url: str | None = None
    access_key_id: str = field(default="", repr=False)
    secret_access_key: str = field(default="", repr=False)

    def describe(self) -> str:
        """A log line naming the destination, without credentials."""
        if not self.remote:
            return f"local filesystem {self.bucket_url}"
        return f"remote S3-compatible bucket {self.bucket_url} (endpoint {self.endpoint_url})"


def resolve_lake(
    data_dir: Path,
    s3_bucket_url: str,
    is_prod: bool,
    access_key_id: str = "",
    secret_access_key: str = "",
) -> Lake:
    """Turn the lake settings into a Lake, failing loudly on a risky or incomplete setup.

    An empty s3_bucket_url is the local lake, data_dir/lake. Otherwise it is the bucket's
    S3 API URL as Cloudflare shows it, https://<account id>.r2.cloudflarestorage.com/
    <bucket>, optionally with a folder after the bucket. A remote lake needs is_prod (so a
    dev run never writes the production lake by accident) and both credentials. is_prod
    with a local lake is refused too, so a prod run with a missing S3_BUCKET_URL can't
    quietly write to a throwaway disk.
    """
    if not s3_bucket_url:
        if is_prod:
            raise LakeConfigError(
                "IS_PROD=true needs S3_BUCKET_URL (the bucket's S3 API URL); "
                f"refusing to write the production lake to {data_dir / 'lake'}"
            )
        return Lake(str(data_dir / "lake"), remote=False)
    bucket_url, endpoint_url = _split_s3_bucket_url(s3_bucket_url)
    if not is_prod:
        raise LakeConfigError(
            f"S3_BUCKET_URL={s3_bucket_url} is set, but IS_PROD is not true. Set IS_PROD=true "
            "to write the production lake, or leave S3_BUCKET_URL empty for the local lake."
        )
    missing = [
        name
        for name, value in [
            ("AWS_ACCESS_KEY_ID", access_key_id),
            ("AWS_SECRET_ACCESS_KEY", secret_access_key),
        ]
        if not value
    ]
    if missing:
        raise LakeConfigError(f"S3_BUCKET_URL={s3_bucket_url} needs {' and '.join(missing)}")
    return Lake(
        bucket_url,
        remote=True,
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )


def _split_s3_bucket_url(url: str) -> tuple[str, str]:
    """Split https://<host>/<bucket>[/<folder>] into (s3://<bucket>[/<folder>], https://<host>).

    The URL must name a bucket: the account endpoint alone (https://<host>/) says where R2
    is, not where the lake goes.
    """
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc:
        raise LakeConfigError(
            f"S3_BUCKET_URL={url!r} is not a bucket URL. Leave it empty for the local lake, or "
            "use the bucket's S3 API URL: https://<account id>.r2.cloudflarestorage.com/<bucket>"
        )
    path = parts.path.strip("/")
    if not path:
        raise LakeConfigError(
            f"S3_BUCKET_URL={url} names no bucket. Use the bucket's S3 API URL from its Settings "
            f"page ({url.rstrip('/')}/<bucket>), optionally with a folder: .../<bucket>/prod"
        )
    return f"s3://{path}", f"https://{parts.netloc}"


def lake_from_env(data_dir: Path) -> Lake:
    """The Lake described by S3_BUCKET_URL, IS_PROD, and the credential env vars."""
    return resolve_lake(data_dir, S3_BUCKET_URL, IS_PROD, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)


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


def next_backfill_window(
    backfilled_through: date | None,
    floor: date,
    yesterday_: date,
    chunk_days: int,
) -> LoadWindow | None:
    """Choose the next contiguous backfill chunk, starting at floor or backfilled_through + 1.

    Returns None once the backfill has reached yesterday_: nothing left to load. Chunks are
    always contiguous (each starts the day after the last one ended), including across the
    off-season (in_season() skips those days without a request) — that keeps next_watermark's
    connectedness check satisfied, so backfilled_through advances on every fully-succeeded
    chunk and a failed day holds it at the start of the chunk that failed, for the next
    firing to retry (ARCHITECTURE.md §6.6).
    """
    if chunk_days < 1:
        raise ValueError(f"chunk_days must be at least 1, got {chunk_days}")
    start = floor if backfilled_through is None else backfilled_through + timedelta(days=1)
    if start > yesterday_:
        return None
    end = min(start + timedelta(days=chunk_days - 1), yesterday_)
    return LoadWindow(start, end, backfill=True)


def in_season(day: date) -> bool:
    """True unless the day is clearly outside a season (SEASON_START..SEASON_END)."""
    return SEASON_START <= (day.month, day.day) <= SEASON_END
