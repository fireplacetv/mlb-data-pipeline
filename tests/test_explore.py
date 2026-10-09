"""Tests for the Rill exploration tool's warehouse download helper (mlb.explore, P1M1)."""

from pathlib import Path

import pytest

from mlb import config, explore

KEYS = {"access_key_id": "test-key-id", "secret_access_key": "test-secret"}
ACCOUNT = "22a6e718787853845a70714844efe66a"
ENDPOINT = f"https://{ACCOUNT}.r2.cloudflarestorage.com"


class FakeFilesystem:
    """Records the (rpath, lpath) it was asked to download, instead of touching the network."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get(self, rpath: str, lpath: str) -> None:
        self.calls.append((rpath, lpath))
        Path(lpath).write_bytes(b"fake duckdb file")


def remote_lake(folder: str = "") -> config.Lake:
    bucket_url = f"{ENDPOINT}/mlb-lake/{folder}" if folder else f"{ENDPOINT}/mlb-lake"
    return config.resolve_lake(Path("unused"), bucket_url, is_prod=True, **KEYS)


def test_warehouse_object_path_sits_alongside_the_lake_dataset_folders() -> None:
    assert explore.warehouse_object_path(remote_lake()) == "mlb-lake/mlb.duckdb"
    assert explore.warehouse_object_path(remote_lake("prod")) == "mlb-lake/prod/mlb.duckdb"


def test_resolve_remote_lake_returns_the_configured_r2_lake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "S3_BUCKET_URL", f"{ENDPOINT}/mlb-lake")
    monkeypatch.setattr(config, "IS_PROD", True)
    monkeypatch.setattr(config, "AWS_ACCESS_KEY_ID", KEYS["access_key_id"])
    monkeypatch.setattr(config, "AWS_SECRET_ACCESS_KEY", KEYS["secret_access_key"])

    lake = explore.resolve_remote_lake(tmp_path)

    assert lake == remote_lake()


def test_resolve_remote_lake_refuses_the_local_lake(tmp_path: Path) -> None:
    # The autouse local_lake fixture (conftest.py) already leaves S3_BUCKET_URL empty.
    with pytest.raises(config.LakeConfigError, match="No R2 warehouse configured"):
        explore.resolve_remote_lake(tmp_path)


def test_resolve_remote_lake_still_names_a_broken_remote_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # S3_BUCKET_URL set but IS_PROD not true: config.lake_from_env's own error surfaces as-is.
    monkeypatch.setattr(config, "S3_BUCKET_URL", f"{ENDPOINT}/mlb-lake")

    with pytest.raises(config.LakeConfigError, match="IS_PROD"):
        explore.resolve_remote_lake(tmp_path)


def test_download_warehouse_fetches_the_object_next_to_the_lake(tmp_path: Path) -> None:
    lake = remote_lake("prod")
    fs = FakeFilesystem()
    dest = tmp_path / "explore" / "mlb.duckdb"

    explore.download_warehouse(dest, lake=lake, filesystem=lambda _lake: fs)

    assert fs.calls == [("mlb-lake/prod/mlb.duckdb", str(dest))]
    assert dest.read_bytes() == b"fake duckdb file"


def test_download_warehouse_creates_the_destination_directory(tmp_path: Path) -> None:
    lake = remote_lake()
    dest = tmp_path / "nested" / "explore" / "mlb.duckdb"

    explore.download_warehouse(dest, lake=lake, filesystem=lambda _lake: FakeFilesystem())

    assert dest.exists()


def test_download_warehouse_without_r2_configured_raises_before_touching_any_filesystem(
    tmp_path: Path,
) -> None:
    def fail_if_called(_lake: config.Lake) -> FakeFilesystem:
        raise AssertionError("filesystem() should not be called when R2 isn't configured")

    with pytest.raises(config.LakeConfigError, match="No R2 warehouse configured"):
        explore.download_warehouse(
            tmp_path / "mlb.duckdb", data_dir=tmp_path, filesystem=fail_if_called
        )


def test_main_without_r2_configured_logs_and_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # main() calls config.setup_logging(), which reconfigures handlers (force=True) and so
    # bypasses caplog; stdout is where the user actually sees this error.
    monkeypatch.setattr(config, "EXPLORE_DB", tmp_path / "mlb.duckdb")

    code = explore.main([])

    assert code == 2
    assert "No R2 warehouse configured" in capsys.readouterr().out
