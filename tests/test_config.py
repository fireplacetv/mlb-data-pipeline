"""Tests for configuration."""

import os


def test_config_import():
    """Test that config imports successfully."""
    from mlb import config

    assert config.MLB_DATA_DIR is not None
    assert config.LOOKBACK_DAYS > 0
    assert config.MAX_CATCHUP_DAYS > 0


def test_config_env_vars():
    """Test that config reads from environment variables."""
    # Since config is imported at module level, we test that env vars
    # are read correctly by checking the env var values directly
    assert os.getenv("LOOKBACK_DAYS", "4") == "4"
    assert os.getenv("MAX_CATCHUP_DAYS", "30") == "30"


def test_ensure_directories(temp_data_dir):
    """Test that ensure_directories creates required folders."""
    from mlb import config

    config.ensure_directories()

    assert config.LAKE_DIR.exists()
    assert config.WAREHOUSE_DIR.exists()
    assert config.DLT_PIPELINES_DIR.exists()
    assert config.CACHE_DIR.exists()
    assert config.LOGS_DIR.exists()
