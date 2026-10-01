"""Pytest configuration and fixtures."""

import os

import pytest


@pytest.fixture
def temp_data_dir(tmp_path):
    """Provide a temporary data directory for tests."""
    os.environ["MLB_DATA_DIR"] = str(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def local_lake(monkeypatch):
    """Keep every test on the local lake, whatever S3_BUCKET_URL or IS_PROD the shell has set."""
    from mlb import config

    monkeypatch.setattr(config, "S3_BUCKET_URL", "")
    monkeypatch.setattr(config, "IS_PROD", False)
