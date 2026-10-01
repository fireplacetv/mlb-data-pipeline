"""Tests for the load-window and watermark rules (ARCHITECTURE.md §6.6)."""

from datetime import date, timedelta

import pytest

from mlb.config import (
    CatchupGapError,
    LoadWindow,
    choose_window,
    in_season,
    next_backfill_window,
    next_watermark,
)

YESTERDAY = date(2025, 9, 10)


def window(start: date | None = None, end: date | None = None, mark: date | None = None):
    """choose_window with fixed lookback 4 and max catch-up 30."""
    return choose_window(mark, YESTERDAY, start, end, lookback_days=4, max_catchup_days=30)


class TestChooseWindow:
    def test_first_run_loads_lookback_through_yesterday(self) -> None:
        assert window() == LoadWindow(date(2025, 9, 6), YESTERDAY, backfill=False)

    def test_normal_catchup_starts_lookback_before_watermark(self) -> None:
        got = window(mark=date(2025, 9, 7))
        assert got == LoadWindow(date(2025, 9, 3), YESTERDAY, backfill=False)

    def test_gap_at_limit_is_allowed(self) -> None:
        got = window(mark=date(2025, 8, 11))  # 30 days before yesterday
        assert got.start == date(2025, 8, 7)

    def test_gap_over_limit_refuses_and_names_backfill(self) -> None:
        with pytest.raises(CatchupGapError, match="--start 2025-08-11 --end 2025-09-10"):
            window(mark=date(2025, 8, 10))

    def test_backfill_is_exact_range(self) -> None:
        got = window(date(2025, 4, 1), date(2025, 4, 30), mark=date(2025, 9, 1))
        assert got == LoadWindow(date(2025, 4, 1), date(2025, 4, 30), backfill=True)

    def test_backfill_ignores_catchup_gap_limit(self) -> None:
        got = window(date(2015, 4, 1), date(2015, 4, 2), mark=date(2020, 1, 1))
        assert got.backfill

    @pytest.mark.parametrize(
        ("start", "end"),
        [
            (date(2025, 9, 2), None),
            (None, date(2025, 9, 2)),
            (date(2025, 9, 3), date(2025, 9, 2)),
            (date(2025, 9, 10), date(2025, 9, 11)),  # ends after yesterday
        ],
    )
    def test_invalid_backfill_flags_raise(self, start: date | None, end: date | None) -> None:
        with pytest.raises(ValueError):
            window(start, end)

    def test_window_days_are_inclusive(self) -> None:
        days = LoadWindow(date(2025, 9, 1), date(2025, 9, 3), backfill=True).days()
        assert days == [date(2025, 9, 1), date(2025, 9, 2), date(2025, 9, 3)]


class TestNextWatermark:
    catchup = LoadWindow(date(2025, 9, 3), YESTERDAY, backfill=False)

    def test_catchup_all_succeeded_advances_to_end(self) -> None:
        assert next_watermark(date(2025, 9, 7), self.catchup, []) == YESTERDAY

    def test_first_run_sets_watermark(self) -> None:
        first = LoadWindow(date(2025, 9, 6), YESTERDAY, backfill=False)
        assert next_watermark(None, first, []) == YESTERDAY

    def test_catchup_failed_day_stops_before_first_failure(self) -> None:
        failed = [date(2025, 9, 10), date(2025, 9, 9)]
        assert next_watermark(date(2025, 9, 7), self.catchup, failed) == date(2025, 9, 8)

    def test_catchup_failure_in_lookback_never_moves_backward(self) -> None:
        assert next_watermark(date(2025, 9, 7), self.catchup, [date(2025, 9, 4)]) == date(
            2025, 9, 7
        )

    def test_first_run_failing_first_day_leaves_no_watermark(self) -> None:
        first = LoadWindow(date(2025, 9, 6), YESTERDAY, backfill=False)
        assert next_watermark(None, first, [date(2025, 9, 6)]) is None

    def test_connected_backfill_advances(self) -> None:
        fill = LoadWindow(date(2025, 8, 1), date(2025, 8, 31), backfill=True)
        assert next_watermark(date(2025, 7, 31), fill, []) == date(2025, 8, 31)

    def test_overlapping_backfill_advances(self) -> None:
        fill = LoadWindow(date(2025, 7, 1), date(2025, 8, 31), backfill=True)
        assert next_watermark(date(2025, 7, 31), fill, []) == date(2025, 8, 31)

    def test_disconnected_backfill_leaves_watermark(self) -> None:
        fill = LoadWindow(date(2025, 8, 2), date(2025, 8, 31), backfill=True)
        assert next_watermark(date(2025, 7, 31), fill, []) == date(2025, 7, 31)

    def test_backfill_with_failed_day_leaves_watermark(self) -> None:
        fill = LoadWindow(date(2025, 8, 1), date(2025, 8, 31), backfill=True)
        assert next_watermark(date(2025, 7, 31), fill, [date(2025, 8, 15)]) == date(2025, 7, 31)

    def test_backfill_without_watermark_establishes_it(self) -> None:
        fill = LoadWindow(date(2025, 9, 1), date(2025, 9, 2), backfill=True)
        assert next_watermark(None, fill, []) == date(2025, 9, 2)

    def test_backfill_of_older_history_never_moves_backward(self) -> None:
        fill = LoadWindow(date(2015, 4, 1), date(2015, 10, 31), backfill=True)
        assert next_watermark(date(2025, 9, 1), fill, []) == date(2025, 9, 1)


class TestNextBackfillWindow:
    FLOOR = date(2015, 4, 1)

    def test_first_chunk_starts_at_floor(self) -> None:
        got = next_backfill_window(None, self.FLOOR, YESTERDAY, chunk_days=30)
        assert got == LoadWindow(self.FLOOR, date(2015, 4, 30), backfill=True)

    def test_next_chunk_starts_after_prior_progress(self) -> None:
        got = next_backfill_window(date(2015, 4, 30), self.FLOOR, YESTERDAY, chunk_days=30)
        assert got == LoadWindow(date(2015, 5, 1), date(2015, 5, 30), backfill=True)

    def test_chunk_spans_the_off_season(self) -> None:
        # Contiguous across the winter; in_season() skips the off-season days without a request.
        got = next_backfill_window(date(2015, 10, 20), self.FLOOR, YESTERDAY, chunk_days=30)
        assert got == LoadWindow(date(2015, 10, 21), date(2015, 11, 19), backfill=True)

    def test_chunk_is_capped_at_yesterday(self) -> None:
        got = next_backfill_window(
            YESTERDAY - timedelta(days=5), self.FLOOR, YESTERDAY, chunk_days=30
        )
        assert got == LoadWindow(YESTERDAY - timedelta(days=4), YESTERDAY, backfill=True)

    def test_terminus_returns_none(self) -> None:
        assert next_backfill_window(YESTERDAY, self.FLOOR, YESTERDAY, chunk_days=30) is None

    def test_terminus_when_floor_is_already_future(self) -> None:
        assert (
            next_backfill_window(None, YESTERDAY + timedelta(days=1), YESTERDAY, chunk_days=30)
            is None
        )

    def test_rejects_non_positive_chunk_days(self) -> None:
        with pytest.raises(ValueError, match="chunk_days"):
            next_backfill_window(None, self.FLOOR, YESTERDAY, chunk_days=0)

    def test_failed_chunk_holds_progress_for_a_retry(self) -> None:
        """A failed day inside a chunk should leave the next firing retrying the same chunk."""
        win = next_backfill_window(date(2015, 4, 30), self.FLOOR, YESTERDAY, chunk_days=30)
        mark = next_watermark(date(2015, 4, 30), win, failed_days=[win.end])
        assert mark == date(2015, 4, 30)
        assert next_backfill_window(mark, self.FLOOR, YESTERDAY, chunk_days=30) == win

    def test_full_backfill_converges_without_gaps_or_repeats(self) -> None:
        """Successive firings (chained through next_watermark) cover every day exactly once."""
        mark: date | None = None
        seen: list[date] = []
        for _ in range(200):
            win = next_backfill_window(mark, self.FLOOR, YESTERDAY, chunk_days=30)
            if win is None:
                break
            seen.extend(win.days())
            mark = next_watermark(mark, win, failed_days=[])
        else:
            pytest.fail("backfill did not reach the terminus within 200 firings")

        span = (YESTERDAY - self.FLOOR).days + 1
        assert len(seen) == span
        assert seen == [self.FLOOR + timedelta(days=i) for i in range(span)]
        assert mark == YESTERDAY


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2025, 2, 14), False),
        (date(2025, 2, 15), True),
        (date(2025, 7, 4), True),
        (date(2025, 11, 15), True),
        (date(2025, 11, 16), False),
        (date(2025, 1, 5), False),
    ],
)
def test_in_season(day: date, expected: bool) -> None:
    assert in_season(day) is expected
