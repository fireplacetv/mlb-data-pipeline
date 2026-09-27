"""Configuration: environment variables, paths, and helpers."""

import logging
import os
from datetime import datetime, timedelta
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


def setup_logging() -> None:
    """Configure logging for the pipeline."""
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
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


def yesterday() -> datetime:
    """Return yesterday's date at midnight (timezone-naive)."""
    return datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)


def lookback_window(through_date: datetime) -> tuple[datetime, datetime]:
    """Return the date range for a lookback re-pull.

    Args:
        through_date: The date we've loaded through.

    Returns:
        Tuple of (start_date, end_date), both midnight UTC.
    """
    start = through_date - timedelta(days=LOOKBACK_DAYS)
    return (start, through_date)
