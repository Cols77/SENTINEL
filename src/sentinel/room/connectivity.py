"""Motors-off dual-path connectivity preflight (Plan 06 Task 4).

Before a live session ever authorizes takeoff, this proves -- concurrently,
never one path gating the other's budget -- that fresh drone telemetry AND
provider reachability both answer. It issues no flight command and touches
nothing beyond the two probes it is given: the caller supplies
``drone_probe``/``provider_probe`` (e.g. a bounded telemetry read on the
connected flight client, and a lightweight reachability call to the
perception provider), and this module only times, races and records them.

This is the sole capability-grant exemption path in the room-agent CLI
(``scripts/room_session.py --check-connectivity-only``), but it is never a
substitute for authorization: the CLI still requires manifest authorization
and provider/filming consent before this ever runs, and this module commands
nothing itself, so a caller cannot use it to reach a real client without
also passing every other gate.
"""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Callable


class ConnectivityError(RuntimeError):
    """Invalid connectivity-check configuration (never a probe failure -- see ConnectivityCheck)."""


@dataclass(frozen=True)
class ProbeResult:
    """One path's answer: an identity proving *which* drone/provider replied,
    and the clock reading the probe completed at (used for the freshness
    check against ``max_age_s``, not merely "it eventually answered")."""

    identity: str
    checked_at: float

    def __post_init__(self) -> None:
        if not isinstance(self.identity, str) or not self.identity.strip():
            raise ConnectivityError("a probe result requires a nonempty identity")


@dataclass(frozen=True)
class ConnectivityCheck:
    ready: bool
    drone_ready: bool
    provider_ready: bool
    drone_identity: str | None
    provider_identity: str | None
    drone_checked_at: float | None
    provider_checked_at: float | None
    drone_error: str | None
    provider_error: str | None


def _run_into(probe: Callable[[], ProbeResult], box: dict) -> None:
    try:
        result = probe()
        if not isinstance(result, ProbeResult):
            raise ConnectivityError("probe must return a ProbeResult")
        box["result"] = result
    except Exception as exc:  # a probe failure is data, never raised out of check()
        box["error"] = f"{type(exc).__name__}: {exc}"


def check(
    *, drone_probe: Callable[[], ProbeResult], provider_probe: Callable[[], ProbeResult],
    max_age_s: float, timeout_s: float, clock: Callable[[], float] = time.monotonic,
) -> ConnectivityCheck:
    """Run both probes concurrently under one shared ``timeout_s`` budget.

    A probe that is missing, errors, times out, or answers with a
    ``checked_at`` older than ``max_age_s`` by the time both paths have
    settled refuses that whole path -- there is no partial-credit "one path
    is enough" mode; ``ready`` is true only when both are.

    Probe threads are daemonized: a hung probe cannot block process exit or
    this call's own return once ``timeout_s`` elapses, it is just reported
    as a timeout on that path.
    """
    if not isinstance(max_age_s, (int, float)) or max_age_s <= 0:
        raise ConnectivityError("max_age_s must be a positive number")
    if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        raise ConnectivityError("timeout_s must be a positive number")
    drone_box: dict = {}
    provider_box: dict = {}
    drone_thread = threading.Thread(target=_run_into, args=(drone_probe, drone_box), daemon=True)
    provider_thread = threading.Thread(target=_run_into, args=(provider_probe, provider_box), daemon=True)
    started = clock()
    drone_thread.start()
    provider_thread.start()
    drone_thread.join(timeout_s)
    remaining = max(0.0, timeout_s - (clock() - started))
    provider_thread.join(remaining)

    def _resolve(thread: threading.Thread, box: dict) -> tuple["ProbeResult | None", str | None]:
        if thread.is_alive():
            return None, "TimeoutError: probe did not answer within timeout_s"
        if "result" in box:
            return box["result"], None
        return None, box.get("error", "probe produced neither a result nor an error")

    drone_result, drone_error = _resolve(drone_thread, drone_box)
    provider_result, provider_error = _resolve(provider_thread, provider_box)
    now = clock()
    drone_fresh = drone_result is not None and (now - drone_result.checked_at) <= max_age_s
    provider_fresh = provider_result is not None and (now - provider_result.checked_at) <= max_age_s
    if drone_result is not None and not drone_fresh:
        drone_error = "stale telemetry"
    if provider_result is not None and not provider_fresh:
        provider_error = "stale reachability check"
    return ConnectivityCheck(
        ready=drone_fresh and provider_fresh, drone_ready=drone_fresh, provider_ready=provider_fresh,
        drone_identity=drone_result.identity if drone_result else None,
        provider_identity=provider_result.identity if provider_result else None,
        drone_checked_at=drone_result.checked_at if drone_result else None,
        provider_checked_at=provider_result.checked_at if provider_result else None,
        drone_error=drone_error, provider_error=provider_error,
    )
