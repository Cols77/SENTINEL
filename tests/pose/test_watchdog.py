import threading
import time

from sentinel.pose.containment import CubeFootprint
from sentinel.pose.watchdog import PoseWatchdog

FOOTPRINT = CubeFootprint(0.0, 100.0, 0.0, 100.0)
INSIDE = (50.0, 50.0)
OUTSIDE = (500.0, 500.0)


def _drain(queue, default=None):
    """A fix_fn that pops from ``queue`` and then holds on ``default``."""
    def fix_fn():
        return queue.pop(0) if queue else default
    return fix_fn


def test_three_consecutive_outside_fixes_trip_the_abort():
    watchdog = PoseWatchdog(_drain([OUTSIDE, OUTSIDE, OUTSIDE]), FOOTPRINT,
                            poll_interval_s=0.001)
    watchdog.start()
    deadline = time.monotonic() + 2.0
    while not watchdog.abort_event.is_set() and time.monotonic() < deadline:
        time.sleep(0.005)
    watchdog.stop()
    assert watchdog.abort_event.is_set()
    assert watchdog.last_fix == OUTSIDE


def test_an_inside_fix_resets_the_streak_and_two_outside_never_trips():
    fixes = [OUTSIDE, OUTSIDE, INSIDE, OUTSIDE, OUTSIDE]
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
