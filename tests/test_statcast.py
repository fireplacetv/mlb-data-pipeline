"""Tests for the Statcast pipeline, run end to end through dlt with a recorded fixture."""

import shutil
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest
from pybaseball.datasources.statcast import get_statcast_data_from_csv

from mlb import config
from mlb.pipelines import common, statcast

FIXTURE = Path(__file__).parent / "fixtures" / "statcast" / "statcast_2025-09-01.csv"
FIXTURE_ROWS = 6


def load_fixture_day(day: date) -> pd.DataFrame:
    """Parse the fixture the way pybaseball.statcast() does, dated as `day`."""
    frame = get_statcast_data_from_csv(FIXTURE.read_text()).convert_dtypes(convert_string=False)
    frame["game_date"] = day.isoformat()
    return frame


class FakeSavant:
    """Stands in for pybaseball: serves the fixture, records calls, fails on request."""

    def __init__(self, empty: set[date] = frozenset(), failing: set[date] = frozenset()):
        self.empty = empty
        self.failing = failing
        self.calls: list[date] = []

    def __call__(self, day: date) -> pd.DataFrame:
        self.calls.append(day)
        if day in self.failing:
            raise ConnectionError(f"Savant timed out for {day}")
        if day in self.empty:
            return pd.DataFrame()
        return load_fixture_day(day)


@pytest.fixture
def run_pipeline(tmp_path: Path) -> Callable[..., int]:
    """Run the pipeline against tmp_path with no network and no sleeping."""

    def _run(
        start: date | None = None,
        end: date | None = None,
        *,
        chunk_days: int | None = None,
        today: date = date(2025, 9, 10),
        savant: FakeSavant | None = None,
    ) -> int:
        return statcast.run(
            start,
            end,
            chunk_days=chunk_days,
            data_dir=tmp_path,
            schema_dir=tmp_path / "schemas",
            today=today,
            fetch=savant or FakeSavant(),
            sleep=lambda _seconds: None,
        )

    return _run


@pytest.fixture(autouse=True)
def no_pybaseball_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests from writing pybaseball cache config."""
    monkeypatch.setattr(statcast, "enable_pybaseball_cache", lambda _cache_dir: None)


def lake(tmp_path: Path) -> Path:
    return tmp_path / "lake" / "raw_statcast"


def load_markers(tmp_path: Path) -> list[Path]:
    return sorted((lake(tmp_path) / "_dlt_loads").iterdir())


def pitches(tmp_path: Path) -> pd.DataFrame:
    return pq.read_table(lake(tmp_path) / "pitches").to_pandas()


def watermark(tmp_path: Path) -> date | None:
    """Read the watermark the way the next run would, restoring from the lake."""
    return statcast.restore_watermark(statcast.build_pipeline(tmp_path, tmp_path / "schemas"))


def backfill_mark(tmp_path: Path) -> date | None:
    """Read the automatic backfill's progress mark, restoring from the lake."""
    return statcast.restore_backfill_mark(statcast.build_pipeline(tmp_path, tmp_path / "schemas"))


def test_backfill_writes_parquet_marker_and_schema(tmp_path: Path, run_pipeline) -> None:
    assert run_pipeline(date(2025, 9, 1), date(2025, 9, 2)) == 0

    assert list((lake(tmp_path) / "pitches").glob("*.parquet"))
    assert len(load_markers(tmp_path)) == 1
    assert (tmp_path / "schemas" / "statcast.schema.yaml").exists()
    rows = pitches(tmp_path)
    assert len(rows) == 2 * FIXTURE_ROWS
    assert set(rows["game_date"]) == {"2025-09-01", "2025-09-02"}
    assert rows["_dlt_load_id"].nunique() == 1


def test_running_twice_creates_second_load(tmp_path: Path, run_pipeline) -> None:
    run_pipeline(date(2025, 9, 1), date(2025, 9, 2))
    run_pipeline(date(2025, 9, 1), date(2025, 9, 2))

    assert len(load_markers(tmp_path)) == 2
    rows = pitches(tmp_path)
    assert rows["_dlt_load_id"].nunique() == 2
    assert len(rows) == 4 * FIXTURE_ROWS  # appended; staging deduplicates


def test_watermark_restored_after_deleting_pipelines_dir(tmp_path: Path, run_pipeline) -> None:
    run_pipeline(date(2025, 9, 1), date(2025, 9, 2))
    shutil.rmtree(tmp_path / "dlt_pipelines")

    savant = FakeSavant()
    assert run_pipeline(today=date(2025, 9, 5), savant=savant) == 0

    # Catch-up from the restored watermark 2025-09-02, minus 4 lookback days.
    assert savant.calls[0] == date(2025, 8, 29)
    assert savant.calls[-1] == date(2025, 9, 4)
    assert watermark(tmp_path) == date(2025, 9, 4)


def test_first_run_catches_up_lookback_days(tmp_path: Path, run_pipeline) -> None:
    savant = FakeSavant()
    assert run_pipeline(savant=savant) == 0
    assert savant.calls == [date(2025, 9, d) for d in range(5, 10)]
    assert watermark(tmp_path) == date(2025, 9, 9)


def test_gap_over_limit_exits_nonzero_without_fetching(tmp_path: Path, run_pipeline) -> None:
    run_pipeline(date(2025, 7, 1), date(2025, 7, 1))
    savant = FakeSavant()

    assert run_pipeline(today=date(2025, 9, 10), savant=savant) == 2
    assert savant.calls == []
    assert watermark(tmp_path) == date(2025, 7, 1)


def test_connected_backfill_advances_watermark(tmp_path: Path, run_pipeline) -> None:
    run_pipeline(date(2025, 9, 1), date(2025, 9, 2))
    run_pipeline(date(2025, 9, 3), date(2025, 9, 4))
    assert watermark(tmp_path) == date(2025, 9, 4)


def test_disconnected_backfill_keeps_watermark(tmp_path: Path, run_pipeline) -> None:
    run_pipeline(date(2025, 9, 1), date(2025, 9, 2))
    assert run_pipeline(date(2025, 9, 5), date(2025, 9, 6)) == 0

    assert watermark(tmp_path) == date(2025, 9, 2)
    assert len(pitches(tmp_path)) == 4 * FIXTURE_ROWS  # data still lands


def test_failed_day_is_isolated_and_exits_nonzero(tmp_path: Path, run_pipeline) -> None:
    run_pipeline(date(2025, 9, 1), date(2025, 9, 1))
    savant = FakeSavant(failing={date(2025, 9, 7)})

    assert run_pipeline(today=date(2025, 9, 10), savant=savant) == 1

    # Every attempt for the failed day was made, and later days still loaded.
    assert savant.calls.count(date(2025, 9, 7)) == statcast.FETCH_ATTEMPTS
    loaded_dates = set(pitches(tmp_path)["game_date"])
    assert "2025-09-07" not in loaded_dates
    assert {"2025-09-08", "2025-09-09"} <= loaded_dates
    assert watermark(tmp_path) == date(2025, 9, 6)


def test_failed_day_in_backfill_keeps_watermark(tmp_path: Path, run_pipeline) -> None:
    run_pipeline(date(2025, 9, 1), date(2025, 9, 1))
    savant = FakeSavant(failing={date(2025, 9, 3)})

    assert run_pipeline(date(2025, 9, 2), date(2025, 9, 4), savant=savant) == 1
    assert watermark(tmp_path) == date(2025, 9, 1)


def test_empty_day_is_skipped(tmp_path: Path, run_pipeline) -> None:
    savant = FakeSavant(empty={date(2025, 9, 2)})

    assert run_pipeline(date(2025, 9, 1), date(2025, 9, 3), savant=savant) == 0

    assert set(pitches(tmp_path)["game_date"]) == {"2025-09-01", "2025-09-03"}
    assert watermark(tmp_path) == date(2025, 9, 3)


def test_off_season_days_are_not_fetched(tmp_path: Path, run_pipeline) -> None:
    savant = FakeSavant()

    assert run_pipeline(date(2024, 11, 14), date(2024, 11, 20), savant=savant) == 0

    assert savant.calls == [date(2024, 11, 14), date(2024, 11, 15)]


def test_transient_error_is_retried(tmp_path: Path) -> None:
    attempts: list[date] = []
    sleeps: list[float] = []

    def flaky(day: date) -> pd.DataFrame:
        attempts.append(day)
        if len(attempts) == 1:
            raise ConnectionError("reset")
        return load_fixture_day(day)

    frame = statcast.fetch_with_retry(flaky, date(2025, 9, 1), sleeps.append)

    assert len(frame) == FIXTURE_ROWS
    assert sleeps == [statcast.RETRY_BASE_SECONDS]


def test_key_columns_land_with_hinted_types(tmp_path: Path, run_pipeline) -> None:
    run_pipeline(date(2025, 9, 1), date(2025, 9, 1))
    schema = pq.read_schema(next((lake(tmp_path) / "pitches").glob("*.parquet")))

    for column in ("game_pk", "batter", "pitcher", "at_bat_number", "pitch_number"):
        assert str(schema.field(column).type) == "int64"
    assert "string" in str(schema.field("game_date").type)


def test_schema_changes_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    before = {"pitches": {"game_pk", "release_speed"}}
    after = {"pitches": {"game_pk", "release_speed", "bat_speed", "release_speed__v_text"}}

    with caplog.at_level("INFO"):
        common.log_schema_changes(before, after | {"new_table": {"a"}})

    assert "new table new_table" in caplog.text
    assert "bat_speed" in caplog.text
    assert "variant columns (type drift): release_speed__v_text" in caplog.text


def test_parse_args_requires_both_dates() -> None:
    with pytest.raises(SystemExit):
        statcast.parse_args(["--start", "2025-09-01"])
    args = statcast.parse_args(["--start", "2025-09-01", "--end", "2025-09-02"])
    assert (args.start, args.end) == (date(2025, 9, 1), date(2025, 9, 2))


def test_parse_args_rejects_chunk_days_with_explicit_range() -> None:
    with pytest.raises(SystemExit):
        statcast.parse_args(["--start", "2025-09-01", "--end", "2025-09-02", "--chunk-days", "5"])
    args = statcast.parse_args(["--chunk-days", "5"])
    assert args.chunk_days == 5


def test_parse_args_chunk_days_defaults_when_given_no_value() -> None:
    args = statcast.parse_args(["--chunk-days"])
    assert args.chunk_days == config.BACKFILL_CHUNK_DAYS
    assert statcast.parse_args([]).chunk_days is None


def test_parse_args_rejects_non_positive_chunk_days() -> None:
    with pytest.raises(SystemExit):
        statcast.parse_args(["--chunk-days", "0"])


def test_chunk_days_backfills_from_configured_floor(
    tmp_path: Path, run_pipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "BACKFILL_START", date(2025, 8, 30))
    savant = FakeSavant()

    assert run_pipeline(chunk_days=5, today=date(2025, 9, 10), savant=savant) == 0

    assert savant.calls == [date(2025, 8, d) for d in (30, 31)] + [
        date(2025, 9, d) for d in (1, 2, 3)
    ]
    assert backfill_mark(tmp_path) == date(2025, 9, 3)
    assert watermark(tmp_path) is None  # the catch-up watermark is untouched


def test_chunk_days_resumes_from_its_own_mark(
    tmp_path: Path, run_pipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "BACKFILL_START", date(2025, 9, 1))
    run_pipeline(chunk_days=2, today=date(2025, 9, 10))

    savant = FakeSavant()
    assert run_pipeline(chunk_days=2, today=date(2025, 9, 10), savant=savant) == 0

    assert savant.calls == [date(2025, 9, 3), date(2025, 9, 4)]
    assert backfill_mark(tmp_path) == date(2025, 9, 4)


def test_chunk_days_is_independent_of_the_catchup_watermark(
    tmp_path: Path, run_pipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "BACKFILL_START", date(2015, 4, 1))
    run_pipeline(today=date(2025, 9, 10))  # a normal catch-up sets loaded_through
    caught_up_to = watermark(tmp_path)
    assert caught_up_to is not None

    savant = FakeSavant()
    assert run_pipeline(chunk_days=3, today=date(2025, 9, 10), savant=savant) == 0

    assert savant.calls[0] == date(2015, 4, 1)  # not held back by the catch-up watermark
    assert watermark(tmp_path) == caught_up_to  # and doesn't move it either


def test_chunk_days_terminus_is_a_noop(
    tmp_path: Path, run_pipeline, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(config, "BACKFILL_START", date(2025, 9, 8))
    assert run_pipeline(chunk_days=10, today=date(2025, 9, 10)) == 0
    assert backfill_mark(tmp_path) == date(2025, 9, 9)  # capped at yesterday

    savant = FakeSavant()
    with caplog.at_level("INFO"):
        assert run_pipeline(chunk_days=10, today=date(2025, 9, 10), savant=savant) == 0

    assert savant.calls == []
    assert "backfill complete" in caplog.text.lower()


def test_chunk_days_failed_day_holds_mark_for_a_retry(
    tmp_path: Path, run_pipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "BACKFILL_START", date(2025, 9, 1))
    failing = FakeSavant(failing={date(2025, 9, 2)})

    assert run_pipeline(chunk_days=3, today=date(2025, 9, 10), savant=failing) == 1
    assert backfill_mark(tmp_path) is None  # the failure was on the first day of the chunk

    savant = FakeSavant()
    assert run_pipeline(chunk_days=3, today=date(2025, 9, 10), savant=savant) == 0
    assert savant.calls == [date(2025, 9, 1), date(2025, 9, 2), date(2025, 9, 3)]  # retried
