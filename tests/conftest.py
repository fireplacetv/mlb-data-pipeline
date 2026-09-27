"""Pytest configuration and fixtures."""

import os

import pytest


@pytest.fixture
def temp_data_dir(tmp_path):
    """Provide a temporary data directory for tests."""
    os.environ["MLB_DATA_DIR"] = str(tmp_path)
    return tmp_path
