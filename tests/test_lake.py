"""Tests for choosing the lake destination: local folder or S3-compatible bucket (R2)."""

import logging
from datetime import date
from pathlib import Path

import pytest

from mlb import config
from mlb.pipelines import common, mlb_api, statcast
from tests.test_statcast import FakeSavant

KEYS = {"access_key_id": "test-key-id", "secret_access_key": "test-secret"}


def test_empty_bucket_url_is_local_lake_under_data_dir(tmp_path: Path) -> None:
    lake = config.resolve_lake(tmp_path, "", is_prod=False)

    assert lake == config.Lake(str(tmp_path / "lake"), remote=False)


def test_relative_file_url_resolves_against_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    lake = config.resolve_lake(Path("/unused"), "file://./data/lake", is_prod=False)

    assert lake.bucket_url == str(tmp_path / "data" / "lake")
    assert not lake.remote


def test_absolute_file_url_is_kept(tmp_path: Path) -> None:
    lake = config.resolve_lake(Path("/unused"), f"file://{tmp_path}/lake", is_prod=False)

    assert lake.bucket_url == str(tmp_path / "lake")


def test_s3_url_with_prod_and_keys_is_remote_r2(tmp_path: Path) -> None:
    lake = config.resolve_lake(
        tmp_path, "s3://mlb-lake/prod/", is_prod=True, r2_account_id="abc123", **KEYS
    )

    assert lake.remote
    assert lake.bucket_url == "s3://mlb-lake/prod"
    assert lake.endpoint_url == "https://abc123.r2.cloudflarestorage.com"
    assert (lake.access_key_id, lake.secret_access_key) == ("test-key-id", "test-secret")


def test_s3_url_without_account_id_uses_default_endpoint(tmp_path: Path) -> None:
    lake = config.resolve_lake(tmp_path, "s3://mlb-lake", is_prod=True, **KEYS)

    assert lake.endpoint_url is None


def test_s3_url_without_is_prod_is_refused(tmp_path: Path) -> None:
    with pytest.raises(config.LakeConfigError, match="IS_PROD"):
        config.resolve_lake(tmp_path, "s3://mlb-lake", is_prod=False, **KEYS)


def test_s3_url_without_credentials_names_what_is_missing(tmp_path: Path) -> None:
    with pytest.raises(config.LakeConfigError, match="AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY"):
        config.resolve_lake(tmp_path, "s3://mlb-lake", is_prod=True)


def test_is_prod_with_local_lake_is_refused(tmp_path: Path) -> None:
    with pytest.raises(config.LakeConfigError, match="remote BUCKET_URL"):
        config.resolve_lake(tmp_path, "", is_prod=True)


def test_unsupported_scheme_is_refused(tmp_path: Path) -> None:
    with pytest.raises(config.LakeConfigError, match="not supported"):
        config.resolve_lake(tmp_path, "gs://mlb-lake", is_prod=True, **KEYS)


def test_describe_and_repr_never_show_secrets(tmp_path: Path) -> None:
    lake = config.resolve_lake(
        tmp_path, "s3://mlb-lake", is_prod=True, r2_account_id="abc123", **KEYS
    )

    for text in (lake.describe(), repr(lake)):
        assert "test-secret" not in text
        assert "test-key-id" not in text
    assert "s3://mlb-lake" in lake.describe()
    assert "abc123.r2.cloudflarestorage.com" in lake.describe()


def test_remote_destination_carries_r2_credentials(tmp_path: Path) -> None:
    lake = config.resolve_lake(
        tmp_path, "s3://mlb-lake", is_prod=True, r2_account_id="abc123", **KEYS
    )

    params = common.lake_destination(lake).config_params
    credentials = params["credentials"]

    assert params["bucket_url"] == "s3://mlb-lake"
    assert credentials.aws_access_key_id == "test-key-id"
    assert credentials.aws_secret_access_key == "test-secret"
    assert credentials.endpoint_url == "https://abc123.r2.cloudflarestorage.com"
    assert credentials.region_name == "auto"


def test_local_destination_has_no_credentials(tmp_path: Path) -> None:
    lake = config.resolve_lake(tmp_path, "", is_prod=False)

    params = common.lake_destination(lake).config_params

    assert params == {"bucket_url": str(tmp_path / "lake")}


def test_pipeline_writes_to_file_url_lake_and_logs_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    other = tmp_path / "elsewhere"
    lake = config.resolve_lake(tmp_path, f"file://{other}", is_prod=False)

    with caplog.at_level(logging.INFO):
        code = statcast.run(
            date(2025, 9, 1),
            date(2025, 9, 1),
            data_dir=tmp_path,
            schema_dir=tmp_path / "schemas",
            today=date(2025, 9, 10),
            lake=lake,
            fetch=FakeSavant(),
            sleep=lambda _seconds: None,
        )

    assert code == 0
    assert list((other / "raw_statcast" / "pitches").glob("*.parquet"))
    assert not (tmp_path / "lake").exists()
    assert f"Lake destination: local filesystem {other}" in caplog.text


def test_run_refuses_remote_lake_without_is_prod_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(config, "BUCKET_URL", "s3://mlb-lake")
    savant = FakeSavant()

    code = statcast.run(
        data_dir=tmp_path,
        schema_dir=tmp_path / "schemas",
        today=date(2025, 9, 10),
        fetch=savant,
        sleep=lambda _seconds: None,
    )

    assert code == 2
    assert savant.calls == []
    assert "IS_PROD is not true" in caplog.text


def test_mlb_api_run_refuses_is_prod_with_local_lake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(config, "IS_PROD", True)

    code = mlb_api.run(data_dir=tmp_path, schema_dir=tmp_path / "schemas", today=date(2025, 9, 10))

    assert code == 2
    assert not (tmp_path / "lake").exists()
    assert "IS_PROD=true needs a remote BUCKET_URL" in caplog.text


ACCOUNT = "22a6e718787853845a70714844efe66a"
S3_API = f"https://{ACCOUNT}.r2.cloudflarestorage.com"


def test_bucket_s3_api_url_from_cloudflare_is_remote_r2(tmp_path: Path) -> None:
    lake = config.resolve_lake(tmp_path, f"{S3_API}/mlb-lake", is_prod=True, **KEYS)

    assert lake.remote
    assert lake.bucket_url == "s3://mlb-lake"
    assert lake.endpoint_url == S3_API


def test_bucket_s3_api_url_can_name_a_folder(tmp_path: Path) -> None:
    lake = config.resolve_lake(tmp_path, f"{S3_API}/mlb-lake/prod/", is_prod=True, **KEYS)

    assert lake.bucket_url == "s3://mlb-lake/prod"


def test_account_endpoint_without_bucket_is_refused(tmp_path: Path) -> None:
    with pytest.raises(config.LakeConfigError, match="names no bucket"):
        config.resolve_lake(tmp_path, f"{S3_API}/", is_prod=True, **KEYS)


def test_s3_api_url_with_matching_account_id_is_accepted(tmp_path: Path) -> None:
    lake = config.resolve_lake(
        tmp_path, f"{S3_API}/mlb-lake", is_prod=True, r2_account_id=ACCOUNT, **KEYS
    )

    assert lake.endpoint_url == S3_API


def test_s3_api_url_with_other_account_id_is_refused(tmp_path: Path) -> None:
    with pytest.raises(config.LakeConfigError, match="doesn't match"):
        config.resolve_lake(
            tmp_path, f"{S3_API}/mlb-lake", is_prod=True, r2_account_id="abc123", **KEYS
        )


def test_s3_api_url_still_needs_is_prod(tmp_path: Path) -> None:
    with pytest.raises(config.LakeConfigError, match="IS_PROD"):
        config.resolve_lake(tmp_path, f"{S3_API}/mlb-lake", is_prod=False, **KEYS)
