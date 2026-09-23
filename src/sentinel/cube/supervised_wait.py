"""Cancellable waits whose deadline does not depend on provider cooperation.

Operations receive an Event and must return values instead of publishing them.
Only a COMPLETED result may be consumed. A timed-out worker may still run, but
its eventual result is discarded. ``on_deadline`` must be a short, nonblocking
latch/dispatch callback; it must never wait for the operation it supervises.
An optional external cancellation Event signals the operation and returns a
CANCELLED outcome without waiting for a noncooperative worker to finish.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from threading import Event, Lock, Thread
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class WaitOutcome(Generic[T]):
    status: str
    value: T | None
    started_at: float | None
    completed_at: float | None
    completion_known: bool
    error_category: str | None = None


class SupervisedWait:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 poll_interval_s: float = 0.01) -> None:
        if not math.isfinite(poll_interval_s) or poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be finite and positive")
        self.clock = clock
        self.poll_interval_s = poll_interval_s

    def _now(self) -> float:
        value = self.clock()
        if not math.isfinite(value):
            raise ValueError("invalid monotonic clock")
        return value

    def run(self, operation: Callable[[Event], T], deadline: float,
            on_deadline: Callable[[], None], *, cancellation: Event | None = None) -> WaitOutcome[T]:
        if not math.isfinite(deadline):
            raise ValueError("deadline must be a finite monotonic instant")
        try:
            started = self._now()
        except BaseException:
            try:
                on_deadline()
            except BaseException:
                pass
            return WaitOutcome("WATCHDOG_FAILED", None, None, None, True, "WATCHDOG_FAILED")
        cancellation, done = cancellation if cancellation is not None else Event(), Event()
        lock = Lock()
        result: list[WaitOutcome[T]] = []
        claimed = False
        launched = False

        def cancel(now: float, *, completion_known: bool) -> None:
            nonlocal claimed
            with lock:
                if claimed:
                    return
                claimed = True
                cancellation.set()
                result.append(WaitOutcome("CANCELLED", None, started, now,
                                          completion_known, "CANCELLED"))
            done.set()

        def expire(now: float, category: str | None = None) -> None:
            nonlocal claimed
            with lock:
                if claimed:
                    return
                claimed = True
                was_cancelled = cancellation.is_set()
                cancellation.set()
                if was_cancelled:
                    result.append(WaitOutcome("CANCELLED", None, started, now,
                                              not launched, "CANCELLED"))
                    done.set()
                    return
            try:
                on_deadline()
            except BaseException:
                category = "WATCHDOG_FAILED"
            with lock:
                result.append(WaitOutcome(
                    "WATCHDOG_FAILED" if category else "DEADLINE_EXCEEDED",
                    None, started, now, not launched, category,
                ))
            done.set()

        def work() -> None:
            nonlocal claimed
            value, category = None, None
            try:
                if cancellation.is_set():
                    cancel(started, completion_known=True)
                    return
                value = operation(cancellation)
            except BaseException:
                category = "OPERATION_FAILED"
            try:
                now = self._now()
            except BaseException:
                expire(started, "WATCHDOG_FAILED")
                return
            if cancellation.is_set():
                cancel(now, completion_known=True)
                return
            if now >= deadline or now < started:
                expire(now, "WATCHDOG_FAILED" if now < started else None)
                return
            with lock:
                if claimed:
                    return
                claimed = True
                result.append(WaitOutcome(
                    "FAILED" if category else "COMPLETED", value,
                    started, now, True, category,
                ))
            done.set()

        def supervise() -> None:
            while not done.is_set():
                if cancellation.is_set():
                    try:
                        now = self._now()
                    except BaseException:
                        now = started
                    cancel(now, completion_known=not launched)
                    return
                try:
                    now = self._now()
                except BaseException:
                    expire(started, "WATCHDOG_FAILED")
                    return
                if now >= deadline or now < started:
                    expire(now, "WATCHDOG_FAILED" if now < started else None)
                    return
                done.wait(min(self.poll_interval_s, deadline - now))

        if cancellation.is_set():
            cancel(started, completion_known=True)
        elif started >= deadline:
            expire(started)
        else:
            launched = True
            Thread(target=supervise, name="checkpoint-watchdog", daemon=True).start()
            Thread(target=work, name="supervised-operation", daemon=True).start()
        done.wait()
        return result[0]
