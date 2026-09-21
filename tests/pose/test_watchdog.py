import threading
import time

import pytest

from sentinel.pose.containment import CubeFootprint
from sentinel.pose.watchdog import (OUTSIDE_STREAK_TO_TRIP, PoseWatchdog, post_land_travel_cm,
                                    trip_margin_cm)

FOOTPRINT = CubeFootprint(0.0, 100.0, 0.0, 100.0)
INSIDE = (50.0, 50.0)
OUTSIDE = (500.0, 500.0)


def _drain(queue, default=None):
    """A fix_fn that pops from ``queue`` and then holds on ``default``."""
    def fix_fn():
        return queue.pop(0) if queue else default
    return fix_fn


def _wait_for(event, seconds=2.0):
    deadline = time.monotonic() + seconds
    while not event.is_set() and time.monotonic() < deadline:
        time.sleep(0.005)


def test_consecutive_outside_fixes_trip_the_abort():
    watchdog = PoseWatchdog(_drain([OUTSIDE] * OUTSIDE_STREAK_TO_TRIP), FOOTPRINT,
                            poll_interval_s=0.001)
    watchdog.start()
    _wait_for(watchdog.abort_event)
    watchdog.stop()
    assert watchdog.abort_event.is_set()
    assert watchdog.last_fix == OUTSIDE


def test_an_inside_fix_resets_the_streak_so_a_lone_wrong_fix_never_trips():
    """The debounce was 3 when in-motion pose was unmeasured; bench test B1
    measured it (joint fixes smooth, single-tag fixes jumping a metre), the
    fix source now returns joint fixes only, and the debounce is 2. One short
    of it, interrupted by an inside fix, must never land the drone."""
    almost = [OUTSIDE] * (OUTSIDE_STREAK_TO_TRIP - 1)
    fixes = almost + [INSIDE] + almost
    watchdog = PoseWatchdog(_drain(fixes, default=INSIDE), FOOTPRINT, poll_interval_s=0.001)
    watchdog.start()
    time.sleep(0.05)
    watchdog.stop()
    assert not watchdog.abort_event.is_set()


def test_no_tags_in_view_never_trips_and_never_touches_the_streak():
    watchdog = PoseWatchdog(_drain([]), FOOTPRINT, poll_interval_s=0.001)
    watchdog.start()
    time.sleep(0.02)
    watchdog.stop()
    assert not watchdog.abort_event.is_set()
    assert watchdog.last_fix is None


def test_stop_always_joins_the_thread_even_when_the_caller_raises():
    watchdog = PoseWatchdog(_drain([], default=INSIDE), FOOTPRINT, poll_interval_s=0.001)
    before = threading.active_count()
    watchdog.start()
    try:
        try:
            raise RuntimeError("the move failed")
        finally:
            watchdog.stop()
    except RuntimeError:
        pass
    assert threading.active_count() == before
    assert watchdog._thread is None  # type: ignore[attr-defined]


def test_a_fix_source_that_raises_does_not_end_the_watch():
    """A dead watchdog looks exactly like a quiet one. A frame-read timeout
    or any other fault in the fix source is counted and the watch goes on."""
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] <= 3:
            raise TimeoutError("no frame")
        return OUTSIDE

    watchdog = PoseWatchdog(flaky, FOOTPRINT, poll_interval_s=0.001)
    watchdog.start()
    _wait_for(watchdog.abort_event)
    watchdog.stop()
    assert watchdog.abort_event.is_set()
    assert watchdog.errors == 3
    assert isinstance(watchdog.last_error, TimeoutError)


def test_on_trip_fires_once_however_long_the_drone_stays_outside():
    trips = []
    watchdog = PoseWatchdog(_drain([], default=OUTSIDE), FOOTPRINT, poll_interval_s=0.001,
                            on_trip=lambda: trips.append(1))
    watchdog.start()
    _wait_for(watchdog.abort_event)
    time.sleep(0.03)
    watchdog.stop()
    assert trips == [1]


def test_a_margin_moves_the_trip_line_inside_the_cube():
    near_edge = (90.0, 50.0)            # inside the cube, 10 cm from its edge
    plain = PoseWatchdog(_drain([], default=near_edge), FOOTPRINT, poll_interval_s=0.001)
    early = PoseWatchdog(_drain([], default=near_edge), FOOTPRINT, poll_interval_s=0.001,
                         margin_cm=26.0)
    plain.start()
    early.start()
    _wait_for(early.abort_event)
    plain.stop()
    early.stop()
    assert early.abort_event.is_set()
    assert not plain.abort_event.is_set()


def test_the_trip_margin_is_the_b1_arithmetic():
    """(debounce + video lag) x speed + travel after `land`, from bench test
    B1: 12 cm of travel at the missions' speed 20, about 35 cm at speed 50."""
    assert post_land_travel_cm(20.0) == pytest.approx(12.0)
    assert post_land_travel_cm(50.0) == pytest.approx(35.0)
    assert post_land_travel_cm(10.0) == pytest.approx(12.0)    # never below the measured floor
    assert trip_margin_cm(20.0) == pytest.approx(26.0, abs=0.5)
    assert trip_margin_cm(50.0) == pytest.approx(70.0, abs=1.0)
