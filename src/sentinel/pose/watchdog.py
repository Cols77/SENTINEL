"""In-move containment watchdog (W08-b level 2, coherence spec section 10.3).

While a move executes, a background thread polls an injectable pose-fix
source and evaluates containment. No tag in view -> no fix -> no action:
neither trips nor resets the streak. The watchdog never issues a motion
command itself; it sets an abort event the caller checks once its blocking
move returns (the existing controlled-landing path), and may call ``on_trip``
so the caller can send `land` mid-move (bench test B1).

What bench test B1 measured (docs/wp5-rehearsal-manifest.md, 2026-09-21), and
what this module takes from it:

- The Tello honours `land` sent while a `move` executes; the move then
  answers ``error Auto land``. ``stop`` is not a command on this firmware.
- Video lags the command clock by about 0.2 s, so every fix is that old.
- Travel after `land`: 12 cm at the missions' ``speed 20`` (one run), about
  35 cm at ``speed 50``.
- In motion, single-tag fixes jumped by more than a metre; two-tag joint
  fixes stayed smooth. The fix source must therefore return joint fixes only
  (``CubeMissionRunner._live_watchdog_fix`` does), which is what makes a
  debounce of two safe: static joint spread is under 3 cm.
"""
from __future__ import annotations

import threading
from typing import Callable

from sentinel.pose.containment import Containment, CubeFootprint, check_containment

#: The fix source blocks on a frame read, so this only bounds the idle spin.
POLL_INTERVAL_S = 0.1
#: Consecutive OUTSIDE joint fixes before tripping. One wrong fix must not
#: land the drone; two in a row from a joint solve is not noise.
OUTSIDE_STREAK_TO_TRIP = 2
#: B1: motion shows in the video 0.2 s after it shows in the state stream.
VIDEO_LAG_S = 0.2
#: Joint fixes per second while moving. Static is 6-8; B1's in-motion video
#: localised 278 of 414 frames. 4 is a deliberately low working figure.
IN_MOTION_FIX_RATE_HZ = 4.0
#: B1, lag-corrected ground travel after `land`, by commanded speed (cm/s).
#: PROVISIONAL: one run at 20, two at 50. Eight more runs at 20 are owed.
POST_LAND_TRAVEL_CM = ((20.0, 12.0), (50.0, 35.0))

FixSource = Callable[[], "tuple[float, float] | None"]


def post_land_travel_cm(speed_cm_s: float) -> float:
    """Linear through the two measured speeds, never below the slower one."""
    (s0, t0), (s1, t1) = POST_LAND_TRAVEL_CM
    return max(t0, t0 + (t1 - t0) * (speed_cm_s - s0) / (s1 - s0))


def trip_margin_cm(speed_cm_s: float) -> float:
    """How far inside the cube edge the watchdog must trip for a `land` sent
    on the trip to stop the drone before the edge: the distance flown while
    the debounce fills and while the video catches up, plus the travel after
    `land`. About 26 cm at the missions' 20 cm/s; about 70 cm at 50 cm/s."""
    debounce_s = OUTSIDE_STREAK_TO_TRIP / IN_MOTION_FIX_RATE_HZ
    return (debounce_s + VIDEO_LAG_S) * speed_cm_s + post_land_travel_cm(speed_cm_s)


def _shrunk(footprint: CubeFootprint, margin_cm: float) -> CubeFootprint:
    return CubeFootprint(footprint.min_x_cm + margin_cm, footprint.max_x_cm - margin_cm,
                         footprint.min_y_cm + margin_cm, footprint.max_y_cm - margin_cm)


class PoseWatchdog:
    """Polls ``fix_fn`` on a background thread until ``stop()``.

    ``fix_fn`` returns a room-frame ``(x_cm, y_cm)`` fix, or ``None`` when no
    usable fix exists. ``margin_cm`` moves the trip line that far inside the
    footprint; it is only meaningful when ``on_trip`` acts immediately, since
    an abort that waits for the move to return gains nothing from tripping
    early. A ``fix_fn`` that raises is counted in ``errors`` and treated as
    "no fix": a fault in the fix source must not silently end the watch.
    """

    def __init__(self, fix_fn: FixSource, footprint: CubeFootprint, *,
                 poll_interval_s: float = POLL_INTERVAL_S,
                 margin_cm: float = 0.0,
                 on_trip: Callable[[], None] | None = None) -> None:
        self._fix_fn = fix_fn
        self._footprint = _shrunk(footprint, margin_cm) if margin_cm else footprint
        self._poll_interval_s = poll_interval_s
        self._on_trip = on_trip
        self.abort_event = threading.Event()
        self.last_fix: tuple[float, float] | None = None
        self.errors = 0
        self.last_error: Exception | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._streak = 0

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the poll loop to end and join it. Always safe to call twice."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                fix = self._fix_fn()
            except Exception as exc:  # the watch must outlive a faulty fix source
                self.errors += 1
                self.last_error = exc
                fix = None
            if fix is not None:
                self.last_fix = fix
                containment = check_containment(fix[0], fix[1], self._footprint, margin_cm=0.0)
                if containment is Containment.OUTSIDE:
                    self._streak += 1
                    if self._streak >= OUTSIDE_STREAK_TO_TRIP and not self.abort_event.is_set():
                        self.abort_event.set()
                        if self._on_trip is not None:
                            self._on_trip()
                else:
                    self._streak = 0
            self._stop_event.wait(self._poll_interval_s)
