"""Tests for choosing the lake destination: local folder or Cloudflare R2 bucket."""

import logging
from datetime import date
from pathlib import Path

import pytest

from mlb import config
from mlb.pipelines import common, mlb_api, statcast
from tests.test_statcast import FakeSavant

KEYS = {"access_key_id": "test-key-id", "secret_access_key": "test-secret"}
ACCOUNT = "22a6e718787853845a70714844efe66a"
ENDPOINT = f"https://{ACCOUNT}.r2.cloudflarestorage.com"


def test_empty_s3_bucket_url_is_local_lake_under_data_dir(tmp_path: Path) -> None:
    lake = config.resolve_lake(tmp_path, "", is_prod=False)

    assert lake == config.Lake(str(tmp_path / "lake"), remote=False)


def test_bucket_s3_api_url_from_cloudflare_is_remote_r2(tmp_path: Path) -> None:
    lake = config.resolve_lake(tmp_path, f"{ENDPOINT}/mlb-lake", is_prod=True, **KEYS)

    assert lake.remote
    assert lake.bucket_url == "s3://mlb-lake"
    assert lake.endpoint_url == ENDPOINT
    assert (lake.access_key_id, lake.secret_access_key) == ("test-key-id", "test-secret")


def test_bucket_s3_api_url_can_name_a_folder(tmp_path: Path) -> None:
    lake = config.resolve_lake(tmp_path, f"{ENDPOINT}/mlb-lake/prod/", is_prod=True, **KEYS)

    assert lake.bucket_url == "s3://mlb-lake/prod"


def test_account_endpoint_without_bucket_is_refused(tmp_path: Path) -> None:
    with pytest.raises(config.LakeConfigError, match="names no bucket"):
        config.resolve_lake(tmp_path, f"{ENDPOINT}/", is_prod=True, **KEYS)


@pytest.mark.parametrize(
    "url", ["s3://mlb-lake/prod", "file://./data/lake", "http://example.com/mlb-lake"]
)
def test_other_url_forms_are_refused(tmp_path: Path, url: str) -> None:
    with pytest.raises(config.LakeConfigError, match="not a bucket URL"):
        config.resolve_lake(tmp_path, url, is_prod=True, **KEYS)


def test_bucket_url_without_is_prod_is_refused(tmp_path: Path) -> None:
    with pytest.raises(config.LakeConfigError, match="IS_PROD"):
        config.resolve_lake(tmp_path, f"{ENDPOINT}/mlb-lake", is_prod=False, **KEYS)


def test_bucket_url_without_credentials_names_what_is_missing(tmp_path: Path) -> None:
    with pytest.raises(config.LakeConfigError, match="AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY"):
        config.resolve_lake(tmp_path, f"{ENDPOINT}/mlb-lake", is_prod=True)


def test_is_prod_with_local_lake_is_refused(tmp_path: Path) -> None:
    with pytest.raises(config.LakeConfigError, match="IS_PROD=true needs S3_BUCKET_URL"):
        config.resolve_lake(tmp_path, "", is_prod=True)


def test_describe_and_repr_never_show_secrets(tmp_path: Path) -> None:
    lake = config.resolve_lake(tmp_path, f"{ENDPOINT}/mlb-lake", is_prod=True, **KEYS)

    for text in (lake.describe(), repr(lake)):
        assert "test-secret" not in text
        assert "test-key-id" not in text
    assert lake.describe() == f"remote S3-compatible bucket s3://mlb-lake (endpoint {ENDPOINT})"


def test_remote_destination_carries_r2_credentials(tmp_path: Path) -> None:
    lake = config.resolve_lake(tmp_path, f"{ENDPOINT}/mlb-lake", is_prod=True, **KEYS)

    params = common.lake_destination(lake).config_params
    credentials = params["credentials"]

    assert params["bucket_url"] == "s3://mlb-lake"
    assert credentials.aws_access_key_id == "test-key-id"
    assert credentials.aws_secret_access_key == "test-secret"
    assert credentials.endpoint_url == ENDPOINT
    assert credentials.region_name == "auto"


def test_local_destination_has_no_credentials(tmp_path: Path) -> None:
    lake = config.resolve_lake(tmp_path, "", is_prod=False)

    params = common.lake_destination(lake).config_params

    assert params == {"bucket_url": str(tmp_path / "lake")}


def test_pipeline_writes_to_given_lake_and_logs_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    other = tmp_path / "elsewhere"

    with caplog.at_level(logging.INFO):
        code = statcast.run(
            date(2025, 9, 1),
            date(2025, 9, 1),
            data_dir=tmp_path,
            schema_dir=tmp_path / "schemas",
            today=date(2025, 9, 10),
            lake=config.Lake(str(other), remote=False),
            fetch=FakeSavant(),
            sleep=lambda _seconds: None,
        )

    assert code == 0
    assert list((other / "raw_statcast" / "pitches").glob("*.parquet"))
    assert not (tmp_path / "lake").exists()
    assert f"Lake destination: local filesystem {other}" in caplog.text


def test_run_refuses_bucket_url_without_is_prod_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(config, "S3_BUCKET_URL", f"{ENDPOINT}/mlb-lake")
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
    assert "IS_PROD=true needs S3_BUCKET_URL" in caplog.text
