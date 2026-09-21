"""In-move containment watchdog (W08-b level 2, coherence spec section 10.3).

While a move executes, a background thread polls an injectable pose-fix
source and evaluates containment. Debounced (3 consecutive OUTSIDE) so
in-motion accuracy -- unmeasured; the 2026-09-21 stand test is a static
hover, 6-8 fixes per second with two tags in view -- cannot trip on one bad
frame. No tag in view -> no fix -> no action: neither trips nor resets the
streak. The watchdog never issues a motion command itself; it only sets an
abort event the caller checks once its blocking move returns (the existing
controlled-landing path). Sending `land` mid-move is a separate, still-off
concern gated behind the caller's own flag; see `CubeMissionRunner`.
"""
from __future__ import annotations

import threading
from typing import Callable

from sentinel.pose.containment import Containment, CubeFootprint, check_containment

POLL_INTERVAL_S = 0.5
#: Debounce: in-motion pose accuracy has never been measured (only a static
#: hover has -- 6-8 fixes/second with two tags in view), so a single bad
#: in-flight frame must not by itself trip a controlled landing.
OUTSIDE_STREAK_TO_TRIP = 3

FixSource = Callable[[], "tuple[float, float] | None"]


class PoseWatchdog:
    """Polls ``fix_fn`` on a background thread until ``stop()``.

    ``fix_fn`` returns a room-frame ``(x_cm, y_cm)`` fix, or ``None`` when no
    tag is in view. Pure/injectable: no camera, no ``tag_map``, no
    thread-unsafe shared state -- callers own everything ``fix_fn`` touches.
    """

    def __init__(self, fix_fn: FixSource, footprint: CubeFootprint, *,
                 poll_interval_s: float = POLL_INTERVAL_S,
                 on_trip: Callable[[], None] | None = None) -> None:
        self._fix_fn = fix_fn
        self._footprint = footprint
        self._poll_interval_s = poll_interval_s
        self._on_trip = on_trip
        self.abort_event = threading.Event()
        self.last_fix: tuple[float, float] | None = None
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
            fix = self._fix_fn()
            if fix is not None:
                self.last_fix = fix
                containment = check_containment(fix[0], fix[1], self._footprint)
                if containment is Containment.OUTSIDE:
                    self._streak += 1
                    if self._streak >= OUTSIDE_STREAK_TO_TRIP:
                        self.abort_event.set()
                        if self._on_trip is not None:
                            self._on_trip()
                else:
                    self._streak = 0
            self._stop_event.wait(self._poll_interval_s)
