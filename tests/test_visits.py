"""Tests for pet visit reconstruction."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custom_components.petkit_ble.visits import (
    MIN_VISIT_DURATION,
    VISIT_GAP_GRACE,
    VisitTracker,
)

START = datetime(2026, 8, 16, 9, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return START + timedelta(seconds=seconds)


def test_no_visits_when_never_detected():
    tracker = VisitTracker()
    for i in range(10):
        tracker.update(False, at(i * 10))
    assert tracker.count == 0
    assert tracker.duration == timedelta()
    assert tracker.last_visit is None


def test_single_visit_counted_and_timed():
    tracker = VisitTracker()
    tracker.update(True, at(0))
    tracker.update(True, at(20))
    # Gap must exceed the grace period before the visit closes.
    tracker.update(False, at(20 + VISIT_GAP_GRACE.total_seconds() + 1))

    assert tracker.count == 1
    assert tracker.duration == timedelta(seconds=20)
    assert tracker.last_visit is not None


def test_visit_counted_while_still_in_progress():
    """A drink should appear as it happens, not only once it ends."""
    tracker = VisitTracker()
    tracker.update(True, at(0))
    assert tracker.count == 0  # too short to trust yet

    tracker.update(True, at(MIN_VISIT_DURATION.total_seconds() + 1))
    assert tracker.count == 1
    assert tracker.in_progress


def test_momentary_blip_is_ignored():
    """A single stray sample must not become a visit."""
    tracker = VisitTracker()
    tracker.update(True, at(0))
    tracker.update(False, at(VISIT_GAP_GRACE.total_seconds() + 1))

    assert tracker.count == 0
    assert tracker.duration == timedelta()


def test_brief_gap_does_not_split_one_drink():
    """Cats sip in bursts; a short pause is the same visit."""
    tracker = VisitTracker()
    tracker.update(True, at(0))
    tracker.update(True, at(10))
    tracker.update(False, at(15))  # inside the grace period
    tracker.update(True, at(20))
    tracker.update(True, at(30))
    tracker.update(False, at(30 + VISIT_GAP_GRACE.total_seconds() + 1))

    assert tracker.count == 1
    assert tracker.duration == timedelta(seconds=30)


def test_long_gap_splits_into_two_visits():
    tracker = VisitTracker()
    gap = VISIT_GAP_GRACE.total_seconds() + 5

    tracker.update(True, at(0))
    tracker.update(True, at(10))
    tracker.update(False, at(10 + gap))

    tracker.update(True, at(200))
    tracker.update(True, at(215))
    tracker.update(False, at(215 + gap))

    assert tracker.count == 2
    assert tracker.duration == timedelta(seconds=25)


def test_current_duration_climbs_during_a_visit():
    tracker = VisitTracker()
    tracker.update(True, at(0))
    assert tracker.current_duration(at(12)) == timedelta(seconds=12)
    assert tracker.current_duration(at(0)) == timedelta()


def test_current_duration_zero_when_idle():
    tracker = VisitTracker()
    assert tracker.current_duration(at(5)) == timedelta()


def test_totals_reset_at_local_midnight():
    tracker = VisitTracker()
    tracker.update(True, at(0))
    tracker.update(True, at(10))
    tracker.update(False, at(10 + VISIT_GAP_GRACE.total_seconds() + 1))
    assert tracker.count == 1

    tomorrow = START + timedelta(days=1)
    tracker.update(False, tomorrow)

    assert tracker.count == 0
    assert tracker.duration == timedelta()


def test_restore_seeds_todays_totals():
    tracker = VisitTracker()
    tracker.restore(
        count=4,
        duration_seconds=125.5,
        last_visit=at(-60),
        day=START.date(),
    )
    assert tracker.count == 4
    assert tracker.duration == timedelta(seconds=125.5)

    # A further visit continues from the restored totals.
    tracker.update(True, at(0))
    tracker.update(True, at(10))
    tracker.update(False, at(10 + VISIT_GAP_GRACE.total_seconds() + 1))

    assert tracker.count == 5
    assert tracker.duration == timedelta(seconds=135.5)


# --- device-recorded history (authoritative) ----------------------------


class FakeRecord:
    """Stands in for protocol.WorkRecord."""

    def __init__(self, offset_seconds: float, stay: int, raw: int) -> None:
        self.timestamp = START + timedelta(seconds=offset_seconds)
        self.stay_seconds = stay
        self.raw_time = raw


def test_device_records_are_counted():
    tracker = VisitTracker()
    tracker.update(False, at(0))  # establish today

    changed = tracker.ingest(
        [FakeRecord(60, 25, raw=1001), FakeRecord(300, 40, raw=1002)], at(600)
    )

    assert changed
    assert tracker.count == 2
    assert tracker.duration == timedelta(seconds=65)
    assert tracker.device_backed


def test_device_records_are_deduplicated():
    """The fountain may resend a window it already delivered."""
    tracker = VisitTracker()
    records = [FakeRecord(60, 25, raw=1001)]

    tracker.ingest(records, at(600))
    tracker.ingest(records, at(700))

    assert tracker.count == 1
    assert tracker.duration == timedelta(seconds=25)


def test_device_records_supersede_the_fallback():
    """Estimates are discarded rather than added to real numbers."""
    tracker = VisitTracker()
    tracker.update(True, at(0))
    tracker.update(True, at(10))
    tracker.update(False, at(10 + VISIT_GAP_GRACE.total_seconds() + 1))
    assert tracker.count == 1 and not tracker.device_backed

    tracker.ingest([FakeRecord(60, 25, raw=1001)], at(600))

    assert tracker.device_backed
    assert tracker.count == 1  # the device's record, not estimate + record
    assert tracker.duration == timedelta(seconds=25)


def test_flag_stops_counting_once_device_backed():
    tracker = VisitTracker()
    tracker.ingest([FakeRecord(60, 25, raw=1001)], at(100))
    assert tracker.count == 1

    tracker.update(True, at(200))
    tracker.update(True, at(230))
    tracker.update(False, at(230 + VISIT_GAP_GRACE.total_seconds() + 1))

    # Still one: the flag only reports the live visit now.
    assert tracker.count == 1
    assert tracker.duration == timedelta(seconds=25)


def test_flag_still_reports_visit_in_progress_when_device_backed():
    tracker = VisitTracker()
    tracker.ingest([FakeRecord(60, 25, raw=1001)], at(100))

    tracker.update(True, at(200))
    assert tracker.in_progress
    assert tracker.current_duration(at(210)) == timedelta(seconds=10)


def test_records_from_another_day_are_ignored():
    """The buffer can hold yesterday's visits; they must not inflate today."""
    tracker = VisitTracker()
    yesterday = FakeRecord(-86400 + 60, 30, raw=900)
    today = FakeRecord(60, 20, raw=1001)

    tracker.ingest([yesterday, today], at(600))

    assert tracker.count == 1
    assert tracker.duration == timedelta(seconds=20)


# --- lifetime totals ------------------------------------------------------


def test_daily_duration_never_goes_backwards():
    """Regression: the daily total used to dip every time a visit closed.

    The live in-progress figure kept climbing through the grace period, then
    only the shorter measured length was banked, so the sensor fell back. A
    total_increasing sensor that decreases reads as a counter reset.
    """
    tracker = VisitTracker()
    seen: list[float] = []

    def sample():
        seen.append(tracker.duration.total_seconds())

    grace = VISIT_GAP_GRACE.total_seconds()
    observations = [
        (True, 0),
        (True, 10),
        (True, 20),
        (False, 25),
        (False, 20 + grace + 1),
        (True, 200),
        (True, 215),
        (False, 215 + grace + 1),
    ]

    sample()
    for detected, offset in observations:
        tracker.update(detected, at(offset))
        sample()

    assert seen == sorted(seen), f"daily duration dipped: {seen}"


def test_lifetime_totals_survive_midnight():
    tracker = VisitTracker()
    tracker.update(True, at(0))
    tracker.update(True, at(30))
    tracker.update(False, at(30 + VISIT_GAP_GRACE.total_seconds() + 1))

    assert tracker.count == 1
    assert tracker.total_count == 1
    assert tracker.total_duration == timedelta(seconds=30)

    tomorrow = START + timedelta(days=1)
    tracker.update(False, tomorrow)

    assert tracker.count == 0, "daily figures reset"
    assert tracker.duration == timedelta()
    assert tracker.total_count == 1, "lifetime figures do not"
    assert tracker.total_duration == timedelta(seconds=30)


def test_lifetime_totals_accumulate_device_records():
    tracker = VisitTracker()
    tracker.ingest([FakeRecord(60, 25, raw=1)], at(100))
    tracker.ingest([FakeRecord(300, 40, raw=2)], at(400))

    assert tracker.total_count == 2
    assert tracker.total_duration == timedelta(seconds=65)


def test_switching_to_device_records_unwinds_estimates_from_totals():
    """Estimates must be given back, or today gets counted twice lifetime."""
    tracker = VisitTracker()
    tracker.update(True, at(0))
    tracker.update(True, at(20))
    tracker.update(False, at(20 + VISIT_GAP_GRACE.total_seconds() + 1))
    assert tracker.total_count == 1
    assert tracker.total_duration == timedelta(seconds=20)

    tracker.ingest([FakeRecord(60, 25, raw=1)], at(300))

    assert tracker.total_count == 1, "estimate replaced, not added to"
    assert tracker.total_duration == timedelta(seconds=25)


def test_totals_never_go_negative_when_unwinding():
    tracker = VisitTracker()
    tracker.restore_totals(count=0, duration_seconds=0.0)
    tracker.count = 5
    tracker.duration = timedelta(seconds=99)

    tracker.ingest([FakeRecord(60, 10, raw=1)], at(300))

    assert tracker.total_count >= 0
    assert tracker.total_duration >= timedelta()


def test_restore_totals():
    tracker = VisitTracker()
    tracker.restore_totals(count=42, duration_seconds=1234.5)
    assert tracker.total_count == 42
    assert tracker.total_duration == timedelta(seconds=1234.5)

    tracker.ingest([FakeRecord(60, 10, raw=1)], at(300))
    assert tracker.total_count == 43
    assert tracker.total_duration == timedelta(seconds=1244.5)
