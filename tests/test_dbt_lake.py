"""Tests for what dbt needs to read the lake from R2: a matching httpfs and the lake root rule."""

from importlib.metadata import version
from pathlib import Path

import duckdb
import yaml

DBT_DIR = Path(__file__).resolve().parents[1] / "dbt"


def test_httpfs_package_matches_duckdb() -> None:
    # The Dockerfile installs httpfs from this package; DuckDB refuses a mismatched build.
    assert version("duckdb-extension-httpfs") == duckdb.__version__


def test_sources_and_macro_share_one_lake_root_rule() -> None:
    macro = (DBT_DIR / "macros" / "lake_root.sql").read_text()
    rule = macro.split("{{-", 1)[1].split("-}}", 1)[0].strip()
    for sources in DBT_DIR.glob("models/staging/*/_*__sources.yml"):
        location = yaml.safe_load(sources.read_text())["sources"][0]["meta"]["external_location"]
        assert "{{ " + rule + " }}" in location, sources.name
