"""Download a copy of the production warehouse from R2 for the Rill exploration tool (P1M1,
docs/roadmap/phase-1-modeling-layer.md).

The local warehouse (`data/warehouse/mlb.duckdb`) only holds whatever days a developer has
loaded locally. The warehouse `scheduled-ingest.yml` uploads to R2 after each run holds staging
back to 2015 (ARCHITECTURE.md §9.4), so Rill reads a downloaded copy of that object instead.

Run: python -m mlb.explore [--dest PATH]
"""

import argparse
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from mlb import config

logger = logging.getLogger(__name__)


class ObjectFilesystem(Protocol):
    """The slice of an fsspec filesystem download_warehouse needs."""

    def get(self, rpath: str, lpath: str) -> Any: ...


FilesystemFactory = Callable[[config.Lake], ObjectFilesystem]


def r2_filesystem(lake: config.Lake) -> ObjectFilesystem:
    """An s3fs filesystem for lake's R2 bucket (s3fs ships with dlt[s3], already a dependency)."""
    import s3fs

    return s3fs.S3FileSystem(
        key=lake.access_key_id,
        secret=lake.secret_access_key,
        client_kwargs={"endpoint_url": lake.endpoint_url},
    )


def resolve_remote_lake(data_dir: Path = config.MLB_DATA_DIR) -> config.Lake:
    """The R2 lake described by the environment, refusing a local or unconfigured one.

    Raises config.LakeConfigError naming the missing or inconsistent settings: an empty
    S3_BUCKET_URL (nothing configured), S3_BUCKET_URL without IS_PROD=true, or missing
    credentials. The exploration tool always reads the production warehouse, never the
    local lake, so a local Lake is refused here even though config.lake_from_env allows it.
    """
    lake = config.lake_from_env(data_dir)
    if not lake.remote:
        raise config.LakeConfigError(
            "No R2 warehouse configured: set S3_BUCKET_URL, IS_PROD=true, AWS_ACCESS_KEY_ID "
            "and AWS_SECRET_ACCESS_KEY in .env (the same settings scheduled-ingest.yml uses) "
            "to download the production warehouse for exploration."
        )
    return lake


def warehouse_object_path(lake: config.Lake) -> str:
    """The warehouse object's path in lake's bucket (alongside the lake's dataset folders).

    Matches the object scheduled-ingest.yml's "Upload warehouse to R2" step writes: lake.bucket_url
    is already "<bucket>[/<folder>]" (the "s3://" prefix stripped), with no trailing slash.
    """
    return f"{lake.bucket_url.removeprefix('s3://')}/mlb.duckdb"


def download_warehouse(
    dest: Path = config.EXPLORE_DB,
    *,
    data_dir: Path = config.MLB_DATA_DIR,
    lake: config.Lake | None = None,
    filesystem: FilesystemFactory = r2_filesystem,
) -> None:
    """Download the production warehouse from R2 to dest, overwriting any copy already there.

    Raises config.LakeConfigError (uncaught here, handled by main) when R2 isn't configured.
    """
    lake = lake or resolve_remote_lake(data_dir)
    object_path = warehouse_object_path(lake)
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading s3://%s to %s", object_path, dest)
    filesystem(lake).get(object_path, str(dest))
    logger.info("Downloaded %s (%d bytes)", dest, dest.stat().st_size)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse the optional --dest flag."""
    parser = argparse.ArgumentParser(
        description="Download the production warehouse from R2 for local exploration."
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=config.EXPLORE_DB,
        help=f"where to save the warehouse copy (default: {config.EXPLORE_DB})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point: set up logging, then download the warehouse. Returns the exit code."""
    args = parse_args(argv)
    config.setup_logging("explore")
    try:
        download_warehouse(args.dest)
    except config.LakeConfigError as exc:
        logger.error("%s", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
