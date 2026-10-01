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

# Lake destination (docs/roadmap/phase-2-cloud-and-scale.md, P2M1). An empty BUCKET_URL
# means the local lake under MLB_DATA_DIR; an s3:// URL (Cloudflare R2) needs IS_PROD=true.
BUCKET_URL = os.getenv("BUCKET_URL", "").strip()
IS_PROD = os.getenv("IS_PROD", "false").strip().lower() in {"1", "true", "yes"}
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "").strip()
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "").strip()

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


# --- Lake destination (docs/roadmap/phase-2-cloud-and-scale.md, P2M1) --------


class LakeConfigError(ValueError):
    """BUCKET_URL, IS_PROD, and the credentials don't describe a usable lake."""


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
        endpoint = self.endpoint_url or "default AWS S3 endpoint"
        return f"remote S3-compatible bucket {self.bucket_url} (endpoint {endpoint})"


def r2_endpoint_url(account_id: str) -> str:
    """The S3 API endpoint of a Cloudflare R2 account."""
    return f"https://{account_id}.r2.cloudflarestorage.com"


def resolve_lake(
    data_dir: Path,
    bucket_url: str,
    is_prod: bool,
    access_key_id: str = "",
    secret_access_key: str = "",
    r2_account_id: str = "",
) -> Lake:
    """Turn the lake settings into a Lake, failing loudly on a risky or incomplete setup.

    An empty bucket_url is the local lake, data_dir/lake. A file:// URL is a local folder,
    relative to the working directory. A remote lake is either s3://<bucket>/<path>, with
    r2_account_id pointing it at R2, or the bucket's S3 API URL as Cloudflare shows it,
    https://<account id>.r2.cloudflarestorage.com/<bucket>[/<path>]. A remote lake needs
    is_prod (so a dev run never writes the production lake by accident) and both
    credentials. is_prod with a local lake is refused too, so a prod run with a missing
    BUCKET_URL can't quietly write to a throwaway disk.
    """
    if not bucket_url:
        lake = Lake(str(data_dir / "lake"), remote=False)
    elif bucket_url.startswith("file://"):
        # dlt reads file://./x as /x, so resolve relative paths here.
        lake = Lake(str(Path(bucket_url.removeprefix("file://")).resolve()), remote=False)
    elif bucket_url.startswith("s3://"):
        endpoint = r2_endpoint_url(r2_account_id) if r2_account_id else None
        lake = _remote_lake(bucket_url, endpoint, is_prod, access_key_id, secret_access_key)
    elif bucket_url.startswith("https://"):
        s3_url, endpoint = _split_s3_api_url(bucket_url, r2_account_id)
        lake = _remote_lake(s3_url, endpoint, is_prod, access_key_id, secret_access_key)
    else:
        raise LakeConfigError(
            f"BUCKET_URL={bucket_url!r} is not supported: leave it empty for the local lake, "
            "or use the bucket's S3 API URL (https://<account id>.r2.cloudflarestorage.com/"
            "<bucket>), s3://<bucket>/<path>, or file://<path>"
        )
    if is_prod and not lake.remote:
        raise LakeConfigError(
            "IS_PROD=true needs a remote BUCKET_URL (s3://<bucket>/<path>); "
            f"refusing to write the production lake to {lake.bucket_url}"
        )
    return lake


def _split_s3_api_url(url: str, r2_account_id: str) -> tuple[str, str]:
    """Split https://<host>/<bucket>[/<path>] into (s3://<bucket>[/<path>], https://<host>).

    The URL must name a bucket: the account endpoint alone (https://<host>/) says where R2
    is, not where the lake goes. An R2_ACCOUNT_ID that names another account is refused.
    """
    parts = urlsplit(url)
    path = parts.path.strip("/")
    if not path:
        raise LakeConfigError(
            f"BUCKET_URL={url} names no bucket. Use the bucket's S3 API URL from its Settings "
            f"page ({url.rstrip('/')}/<bucket>), optionally with a folder: .../<bucket>/prod"
        )
    endpoint = f"https://{parts.netloc}"
    if r2_account_id and endpoint != r2_endpoint_url(r2_account_id):
        raise LakeConfigError(
            f"R2_ACCOUNT_ID={r2_account_id} doesn't match BUCKET_URL's endpoint {endpoint}; "
            "the URL already holds the account ID, so leave R2_ACCOUNT_ID empty"
        )
    return f"s3://{path}", endpoint


def _remote_lake(
    bucket_url: str,
    endpoint_url: str | None,
    is_prod: bool,
    access_key_id: str,
    secret_access_key: str,
) -> Lake:
    """Validate the settings for an s3:// lake and return it."""
    if not is_prod:
        raise LakeConfigError(
            f"BUCKET_URL={bucket_url} is remote, but IS_PROD is not true. Set IS_PROD=true "
            "to write the production lake, or leave BUCKET_URL empty for the local lake."
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
        raise LakeConfigError(f"BUCKET_URL={bucket_url} needs {' and '.join(missing)}")
    return Lake(
        bucket_url.rstrip("/"),
        remote=True,
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )


def lake_from_env(data_dir: Path) -> Lake:
    """The Lake described by BUCKET_URL, IS_PROD, and the credential env vars."""
    return resolve_lake(
        data_dir,
        BUCKET_URL,
        IS_PROD,
        AWS_ACCESS_KEY_ID,
        AWS_SECRET_ACCESS_KEY,
        R2_ACCOUNT_ID,
    )


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
