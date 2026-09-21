# src/sentinel/cube/mission.py
"""Waypoint missions inside the operating cube, with three pose modes.

off      - pure open-loop navigation; no tags needed.
passive  - pose is measured at each stop, logged, and may trip the existing
           abort path through the containment check. Nothing pose-derived is
           sent (SR-063 as written); the implied trim is logged only.
reanchor - additionally, at a stationary hover, a fix that passes the quality
           and plausibility gates replaces the open-loop position estimate and
           may trigger bounded trims (requires the SR-063 amendment).

The drone translates only while facing +y, so forward = +y and right = +x and
the inherited envelope bookkeeping is valid in the room frame.
"""
import hashlib
import json
import math
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sentinel.client import EmergencyAborted
from sentinel.envelope import EnvelopeViolation
from sentinel.models import Position
from sentinel.pose.containment import Containment, check_containment
from sentinel.pose.detector import NoTagDetected, estimate_pose
from sentinel.pose.localise import LocalisationFix, localise
from sentinel.pose.replay import FrameRecorder
from sentinel.pose.scoring import heading_delta_deg
from sentinel.pose.stream import _to_bgr, run_pose_stream
from sentinel.pose.watchdog import POLL_INTERVAL_S, PoseWatchdog, trip_margin_cm
from sentinel.runner import FlightTestRunner

POSE_MODES = ("off", "passive", "reanchor")
MAX_WAYPOINTS = 8
MAX_LOOK_DEG = 170
FACING_DEG = 90.0
DEAD_BAND_CM = 10
MIN_MOVE_CM = 20
#: The speed FlightTestRunner._preflight commands and reads back before any
#: move. B1 measured 16 cm/s of real cruise at this setting.
COMMAND_SPEED_CM_S = 20.0
MAX_TRIM_ROUNDS = 2
YAW_TRIM_THRESHOLD_DEG = 10.0
PLAUSIBILITY_CM = 60.0
TOLERANCE_CM = 15.0
OUTER_TOLERANCE_CM = 30.0
SETTLE_S = 1.5
BURST_MAX_S = 12.0
BURST_TARGET_REPORTS = 12
CM_PER_UNIT = 10.0
#: F2: a re-anchored fix can land a few cm beyond a commanded x/y bound
#: (still inside the cube footprint). A waypoint this close to a bound makes
#: that likelier -- flagged in reanchor mode. wp5-flight1-loop.json does not
#: clear this margin against wp5-flight1-site.json (RIGHT_NEAR/HOME are 20 cm
#: from the y bound), so this is a logged warning, not a PlanError.
REANCHOR_BOUND_MARGIN_CM = 25
#: A4/W08-b: timeout for one frame read on the watchdog's own thread, while a
#: blocking move is in progress on the main thread.
WATCHDOG_FRAME_TIMEOUT_S = 2.0


class PlanError(ValueError):
    """The mission does not fit the commanded bounds."""


class ContainmentAbort(RuntimeError):
    """The measured position left the operating cube."""


class ToleranceAbort(RuntimeError):
    """Both trims were spent and the drone is still beyond the outer tolerance circle."""


class StartUnlocalised(RuntimeError):
    """A mission without a start pad could not measure where the drone took off."""


class PoseImplausible(RuntimeError):
    """Two consecutive fixes disagreed with dead reckoning."""


@dataclass(frozen=True)
class Waypoint:
    name: str
    x_cm: int
    y_cm: int
    z_cm: int
    looks_deg: tuple[int, ...] = (0,)
    localise: bool = True


@dataclass(frozen=True)
class Mission:
    """``start_*`` None: the start is measured from the tags after takeoff (pose modes only)."""
    start_x_cm: int | None
    start_y_cm: int | None
    waypoints: tuple[Waypoint, ...]


def load_mission(path) -> Mission:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("schema_version") != "cube-mission-v1":
        raise ValueError(f"unsupported mission file: {doc.get('schema_version')!r}")
    waypoints = tuple(
        Waypoint(w["name"], int(w["x_cm"]), int(w["y_cm"]), int(w["z_cm"]),
                 tuple(int(v) for v in w.get("looks_deg", (0,))), bool(w.get("localise", True)))
        for w in doc["waypoints"])
    if not 1 <= len(waypoints) <= MAX_WAYPOINTS:
        raise ValueError(f"a mission has 1-{MAX_WAYPOINTS} waypoints, got {len(waypoints)}")
    start = doc.get("start")
    if start is None:
        return Mission(None, None, waypoints)
    return Mission(int(start["x_cm"]), int(start["y_cm"]), waypoints)


def tolerance_verdict(arrival_error_cm: float) -> str:
    """INSIDE the tolerance circle, in the OUTER ring (continue, logged), or OUTSIDE both."""
    if arrival_error_cm <= TOLERANCE_CM:
        return "INSIDE"
    return "OUTER" if arrival_error_cm <= OUTER_TOLERANCE_CM else "OUTSIDE"


def axis_move(delta_cm: float) -> int:
    """Distance to command for one axis: nothing inside the dead band, else >= the SDK minimum."""
    if abs(delta_cm) <= DEAD_BAND_CM:
        return 0
    return max(MIN_MOVE_CM, round(abs(delta_cm)))


_AXES = (("x_cm", "left", "right"), ("y_cm", "back", "forward"), ("z_cm", "down", "up"))


class _StreamHeld:
    """Client view whose video lifecycle calls are no-ops: one stream stays open all flight."""

    def __init__(self, client) -> None:
        self._client = client

    def start_video(self) -> None:
        return None

    def stop_video(self) -> None:
        return None

    def read_frame(self, timeout=None):
        return self._client.read_frame(timeout=timeout)


class CubeMissionRunner(FlightTestRunner):
    def __init__(self, client, mode, minimum_battery: int = 40, *, mission: Mission, site,
                 pose_mode: str = "off", tag_map=None, conn=None, run_id: str = "",
                 mission_log: Path, capture_dir: Path | None = None,
                 burst_fn: Callable[[], list] | None = None,
                 sleep_fn: Callable[[float], None] = time.sleep,
                 watchdog_fix_fn: Callable[[], tuple[float, float] | None] | None = None,
                 land_during_move: bool = False,
                 watchdog_poll_interval_s: float = POLL_INTERVAL_S, **runner_kwargs) -> None:
        if pose_mode not in POSE_MODES:
            raise ValueError(f"pose_mode must be one of {POSE_MODES}, got {pose_mode!r}")
        if pose_mode != "off" and burst_fn is None and (tag_map is None or conn is None):
            raise ValueError("pose modes other than 'off' need a tag map and a pose database")
        runner_kwargs["retain_video_stream"] = True
        runner_kwargs["envelope"] = site.envelope()
        super().__init__(client, mode, minimum_battery, **runner_kwargs)
        self.mission, self.site, self.pose_mode = mission, site, pose_mode
        self.tag_map, self.conn, self.run_id = tag_map, conn, run_id
        self.mission_log = Path(mission_log)
        self.recorder = FrameRecorder(capture_dir) if capture_dir is not None and mode == "real" else None
        self.burst_fn = burst_fn if burst_fn is not None else self._live_burst
        self.sleep_fn = sleep_fn
        self.footprint = site.footprint()
        self._look_offset = 0
        self._implausible_streak = 0
        # A4/W08-b: sending `land` from the watchdog thread mid-move. Bench
        # test B1 has shown the Tello honours it, but only 2 of its 9 runs
        # are flown (docs/wp5-rehearsal-manifest.md). Until all 9 pass this
        # stays off and an abort takes effect once the blocking move
        # returns, same as every other controlled landing here.
        self.land_during_move = land_during_move
        self._watchdog_poll_interval_s = watchdog_poll_interval_s
        self._watchdog_last_fix_cm: list[float] | None = None
        self._watchdog_land_error: Exception | None = None
        self._watchdog_fix_errors = 0
        if pose_mode not in ("passive", "reanchor"):
            self._watchdog_fix_fn = None
        elif watchdog_fix_fn is not None:
            self._watchdog_fix_fn = watchdog_fix_fn
        elif mode == "real":
            self._watchdog_fix_fn = self._live_watchdog_fix
        else:
            # DryRunClient frames are an undecodable placeholder (see
            # _live_burst); a dry run with no injected fix_fn honestly runs
            # the watchdog off, same as pose_mode "off".
            self._watchdog_fix_fn = None
        self._validate_mission()

    # -- planning -----------------------------------------------------------
    def _validate_mission(self) -> None:
        env = self.envelope
        in_xy = lambda x, y: env.min_x_cm <= x <= env.max_x_cm and env.min_y_cm <= y <= env.max_y_cm
        if self.mission.start_x_cm is None or self.mission.start_y_cm is None:
            if self.pose_mode == "off":
                raise PlanError("a mission without a start pad needs a pose mode: with pose off "
                                "nothing can tell the runner where it took off")
        elif not in_xy(self.mission.start_x_cm, self.mission.start_y_cm):
            raise PlanError("the start pad is outside the commanded bounds")
        for wp in self.mission.waypoints:
            if not (in_xy(wp.x_cm, wp.y_cm) and env.min_z_cm <= wp.z_cm <= env.max_z_cm):
                raise PlanError(f"waypoint {wp.name} is outside the commanded bounds")
            if any(abs(look) > MAX_LOOK_DEG for look in wp.looks_deg):
                raise PlanError(f"waypoint {wp.name} has a look offset beyond {MAX_LOOK_DEG} degrees")
            if self.pose_mode == "reanchor":
                margin = min(wp.x_cm - env.min_x_cm, env.max_x_cm - wp.x_cm,
                            wp.y_cm - env.min_y_cm, env.max_y_cm - wp.y_cm)
                if margin < REANCHOR_BOUND_MARGIN_CM:
                    self._emit("alert", f"{wp.name}: only {margin:.0f} cm from a commanded x/y "
                                        f"bound in reanchor mode (recommended >= "
                                        f"{REANCHOR_BOUND_MARGIN_CM:.0f} cm -- a re-anchored fix "
                                        "can land outside the bounds; F2)")

    # -- commands -----------------------------------------------------------
    def _rotate(self, direction: str, degrees: int) -> None:
        self._emit("flight", f"sending rotation {direction} {degrees} degrees")
        response = self._call(lambda: self.client.rotate(direction, degrees))
        if not self._is_ok(response):
            raise RuntimeError(f"rotation {direction} {degrees} failed: {response}")

    def _look_to(self, offset_deg: int) -> None:
        delta = offset_deg - self._look_offset
        if delta:
            self._rotate("ccw" if delta > 0 else "cw", abs(delta))
        self._look_offset = offset_deg

    def _step(self, direction: str, distance_cm: int, *, optional: bool) -> bool:
        """One checked move. An optional step that would leave the bounds is skipped."""
        try:
            self.move_checked(direction, distance_cm)
        except EnvelopeViolation:
            if not optional:
                raise
            self._emit("flight", f"skipped {direction} {distance_cm}: would leave the bounds")
            return False
        return True

    def _moves_toward(self, target: dict, origin: dict) -> list[tuple[str, int]]:
        moves = []
        for axis, negative, positive in _AXES:
            if axis not in target:
                continue
            delta = target[axis] - origin[axis]
            distance = axis_move(delta)
            if distance:
                moves.append((positive if delta > 0 else negative, distance))
        return moves

    def _go_to(self, wp: Waypoint) -> None:
        if self._look_offset != 0:
            raise RuntimeError("translation requires the drone to face +y")
        target = {"x_cm": wp.x_cm, "y_cm": wp.y_cm, "z_cm": wp.z_cm}
        self._watchdog_last_fix_cm = None
        self._watchdog_land_error = None
        self._watchdog_fix_errors = 0
        env = self.envelope
        origin = env.position.as_dict()
        # F2: reanchoring can leave the tracked x/y a few cm outside the
        # commanded bounds (still inside the cube footprint). Moving straight
        # to the next waypoint validates the WHOLE candidate position on
        # every axis (FlightEnvelope.contains), so if that axis's own delta
        # to this waypoint happens to fall inside the dead band, axis_move
        # sends it no corrective move at all -- and the OTHER axis's
        # perfectly good move then fails on a reason of its own making.
        # Recover any such axis first, non-optionally (never skipped as an
        # overshoot), before the rest of the plan is validated against it.
        #
        # A fix near a cube corner can leave x AND y outside the bounds at
        # once. Recovering one axis still validates the WHOLE candidate
        # position, so the first recovery move would itself be rejected on
        # the other, still-uncorrected axis. While that other axis is still
        # bad, widen its bound just enough to admit its own current (already
        # out-of-bounds) value -- so only the axis actually moving here is
        # really checked -- and restore the real bound immediately after,
        # before anything else in this method is validated.
        bad_axes = [axis for axis, _, _ in _AXES if axis in ("x_cm", "y_cm")
                    and not (getattr(env, f"min_{axis}") <= origin[axis] <= getattr(env, f"max_{axis}"))]
        for axis, negative, positive in _AXES:
            if axis not in bad_axes:
                continue
            widened = []
            for other in bad_axes:
                if other == axis:
                    continue
                current = getattr(env.position, other)
                lo_attr, hi_attr = f"min_{other}", f"max_{other}"
                lo, hi = getattr(env, lo_attr), getattr(env, hi_attr)
                if lo <= current <= hi:
                    continue
                widened.append((lo_attr, lo, hi_attr, hi))
                setattr(env, lo_attr, min(lo, current))
                setattr(env, hi_attr, max(hi, current))
            try:
                delta = target[axis] - origin[axis]
                distance = max(MIN_MOVE_CM, round(abs(delta)))
                self._step(positive if delta > 0 else negative, distance, optional=False)
            finally:
                for lo_attr, lo, hi_attr, hi in widened:
                    setattr(env, lo_attr, lo)
                    setattr(env, hi_attr, hi)
        for direction, distance in self._moves_toward(target, self.envelope.position.as_dict()):
            # a minimum-move overshoot (delta under 20 cm) is optional; a real leg is not
            overshoot = distance == MIN_MOVE_CM
            self._step(direction, distance, optional=overshoot)

    def move_checked(self, direction: str, distance_cm: int) -> None:
        """A4/W08-b: while modes passive/reanchor move, a background watchdog
        evaluates containment against an injectable pose-fix source; OUTSIDE
        on consecutive joint fixes ends in the existing controlled landing.
        No tag in view -> no fix -> no action.

        With ``land_during_move`` the watchdog trips ``trip_margin_cm`` inside
        the cube edge and sends `land` at once; the interrupted move then
        fails with ``error Auto land`` (bench test B1), which is the expected
        result of the abort and is reported as the containment abort it is,
        not as a movement fault. Without it the trip line is the edge itself
        and the abort waits for the move to return."""
        if self._watchdog_fix_fn is None:
            super().move_checked(direction, distance_cm)
            return
        immediate = self.land_during_move
        watchdog = PoseWatchdog(
            self._watchdog_fix_fn, self.footprint,
            poll_interval_s=self._watchdog_poll_interval_s,
            margin_cm=trip_margin_cm(COMMAND_SPEED_CM_S) if immediate else 0.0,
            on_trip=self._send_land_during_move if immediate else None)
        watchdog.start()
        move_error: Exception | None = None
        try:
            super().move_checked(direction, distance_cm)
        except (EnvelopeViolation, EmergencyAborted):
            raise
        except Exception as exc:  # noqa: BLE001 - DJITelloPy raises its own non-RuntimeError type
            move_error = exc
        finally:
            watchdog.stop()
            if watchdog.last_fix is not None:
                self._watchdog_last_fix_cm = [round(watchdog.last_fix[0], 1),
                                              round(watchdog.last_fix[1], 1)]
            self._watchdog_fix_errors += watchdog.errors
        if watchdog.abort_event.is_set():
            raise ContainmentAbort("containment: the in-move watchdog observed the drone "
                                   "leave the cube") from move_error
        if move_error is not None:
            raise move_error

    def _live_watchdog_fix(self) -> tuple[float, float] | None:
        """Default in-move fix source (production, mode "real" only): one
        frame, best-effort pose. Runs on the watchdog's own thread,
        concurrently with the blocking move on the main thread -- reads the
        client directly, not through self._call, which mutates self.history
        and is not thread-safe. No tag in view is the routine case here, not
        an error: NoTagDetected/ValueError both fail closed to "no fix",
        matching _solve_mapped_tags's own contract."""
        try:
            frame = self.client.read_frame(timeout=WATCHDOG_FRAME_TIMEOUT_S)
            report = estimate_pose(_to_bgr(frame), self.tag_map, run_id=self.run_id,
                                   report_id="watchdog", source_kind="LIVE")
        except (NoTagDetected, ValueError):
            return None
        # B1: in motion, single-tag fixes jumped by more than a metre while
        # joint fixes stayed smooth. A report lists two or more tags only
        # when the joint solve produced it (detector.estimate_pose).
        if len(report.tag_observations) < 2:
            return None
        return (report.point[0] * CM_PER_UNIT, report.point[1] * CM_PER_UNIT)

    def _send_land_during_move(self) -> None:
        """Best-effort `land` sent from the watchdog thread mid-move
        (land_during_move=True only, still default off -- bench test B1).
        Mirrors FlightTestRunner.trigger_emergency's pattern for a
        cross-thread command: attempt once, record failure, never let an
        exception escape the watchdog thread."""
        # land() waits for the command lock the in-flight move holds, so it
        # could only land after the move; land_now() is the lock-free send.
        try:
            getattr(self.client, "land_now", self.client.land)()
        except Exception as exc:  # noqa: BLE001 - must never kill the watchdog thread
            self._watchdog_land_error = exc

    # -- pose ---------------------------------------------------------------
    def _live_burst(self) -> list:
        if self.mode != "real":
            # DryRunClient frames are an undecodable placeholder, so a dry run is
            # honestly UNLOCALISED at every stop.
            return []
        count = {"n": 0}

        def on_report(_report) -> None:
            count["n"] += 1

        return run_pose_stream(
            _StreamHeld(self.client), self.tag_map, self.conn, run_id=self.run_id,
            max_frames=None, duration_s=BURST_MAX_S, source_kind="LIVE",
            on_frame=lambda _frame: count["n"] < BURST_TARGET_REPORTS, on_report=on_report)

    def _localise(self, wp: Waypoint, entry: dict) -> None:
        started = time.monotonic()
        try:
            self._localise_rounds(wp, entry)
        finally:
            entry["localise_s"] = round(time.monotonic() - started, 1)

    def _localise_rounds(self, wp: Waypoint, entry: dict) -> None:
        rounds = 0
        while True:
            self.sleep_fn(SETTLE_S)
            result = localise(self.burst_fn())
            if not isinstance(result, LocalisationFix):
                if entry["status"] == "OPEN_LOOP":
                    entry["status"] = "UNLOCALISED"
                entry["n"] = result.n
                self._emit("pose", f"{wp.name}: unlocalised ({result.reason})")
                return
            fix = (result.x_units * CM_PER_UNIT, result.y_units * CM_PER_UNIT)
            dead = self.envelope.position
            heading_error = heading_delta_deg(result.heading_deg, FACING_DEG)
            containment = check_containment(fix[0], fix[1], self.footprint)
            trims = self._moves_toward({"x_cm": wp.x_cm, "y_cm": wp.y_cm},
                                       {"x_cm": fix[0], "y_cm": fix[1]})
            entry.update({
                "fix_cm": [round(fix[0], 1), round(fix[1], 1)],
                "heading_deg": round(result.heading_deg, 1),
                "heading_error_deg": round(heading_error, 1), "n": result.n,
                "spread_p95_cm": round(result.spread_p95_cm, 2),
                "arrival_error_cm": round(math.dist(fix, (wp.x_cm, wp.y_cm)), 1),
                "containment": containment.value,
                "tolerance": tolerance_verdict(math.dist(fix, (wp.x_cm, wp.y_cm))),
                "proposed_trims": [list(t) for t in trims], "trim_rounds_sent": rounds,
            })
            if rounds == 0 and entry.get("open_loop_fix_cm") is None:
                entry["open_loop_fix_cm"] = entry["fix_cm"]
                entry["open_loop_error_cm"] = entry["arrival_error_cm"]
            if containment is Containment.OUTSIDE:
                entry["status"] = "LOCALISED"
                raise ContainmentAbort(f"containment: measured position {entry['fix_cm']} cm "
                                       "is outside the cube")
            if containment is Containment.MARGIN:
                self._emit("alert", f"{wp.name}: measured position is in the cube margin")
            if math.dist(fix, (dead.x_cm, dead.y_cm)) > PLAUSIBILITY_CM:
                entry["status"] = "IMPLAUSIBLE"
                self._implausible_streak += 1
                self._emit("alert", f"{wp.name}: fix {entry['fix_cm']} cm is implausible "
                                    f"against dead reckoning ({dead.x_cm}, {dead.y_cm})")
                if self._implausible_streak >= 2:
                    raise PoseImplausible("pose implausible at two consecutive stops")
                return
            self._implausible_streak = 0
            entry["status"] = "LOCALISED"
            if self.pose_mode != "reanchor":
                return
            self.envelope.position = replace(dead, x_cm=round(fix[0]), y_cm=round(fix[1]))
            if rounds == 0 and abs(heading_error) > YAW_TRIM_THRESHOLD_DEG:
                self._rotate("cw" if heading_error > 0 else "ccw", round(abs(heading_error)))
            # Inside the tolerance circle, a 20 cm minimum move can only make things worse
            # (2026-09-21 enlarged trial: 12 cm became 25.7 cm after two trims).
            if not trims or rounds >= MAX_TRIM_ROUNDS or entry["tolerance"] == "INSIDE":
                return self._accept_arrival(wp, entry)
            sent = [self._step(direction, distance, optional=True) for direction, distance in trims]
            if not any(sent):
                return self._accept_arrival(wp, entry)
            rounds += 1

    def _battery_pct(self) -> int | None:
        """Best-effort battery reading for the log: a low battery explains a Tello that lands itself."""
        try:
            return int(self._call(self.client.battery))
        except Exception:  # noqa: BLE001 - telemetry must never end a flight
            return None

    def _localise_start(self, height_cm: int) -> None:
        """No start pad: measure where the drone is after takeoff and plan every leg from it.
        No reliable fix, or a fix outside the cube, ends the flight before any translation."""
        entry = {"waypoint": "START", "pose_mode": self.pose_mode, "status": "UNLOCALISED",
                 "fix_cm": None, "heading_deg": None, "heading_error_deg": None, "n": 0,
                 "spread_p95_cm": None, "containment": None, "yaw_trim_deg": None,
                 "height_cm": height_cm, "battery_pct": self._battery_pct()}
        try:
            self.sleep_fn(SETTLE_S)
            result = localise(self.burst_fn())
            if not isinstance(result, LocalisationFix):
                entry["n"] = result.n
                raise StartUnlocalised(f"start: no reliable tag fix after takeoff ({result.reason}); "
                                       "the runner will not guess where it is")
            fix = (result.x_units * CM_PER_UNIT, result.y_units * CM_PER_UNIT)
            heading_error = heading_delta_deg(result.heading_deg, FACING_DEG)
            containment = check_containment(fix[0], fix[1], self.footprint)
            entry.update({"fix_cm": [round(fix[0], 1), round(fix[1], 1)],
                          "heading_deg": round(result.heading_deg, 1),
                          "heading_error_deg": round(heading_error, 1), "n": result.n,
                          "spread_p95_cm": round(result.spread_p95_cm, 2),
                          "containment": containment.value, "status": "LOCALISED"})
            if containment is Containment.OUTSIDE:
                raise ContainmentAbort(f"containment: start position {entry['fix_cm']} cm "
                                       "is outside the cube")
            self.envelope.position = Position(round(fix[0]), round(fix[1]), height_cm)
            if abs(heading_error) > YAW_TRIM_THRESHOLD_DEG:
                if self.pose_mode == "reanchor":
                    self._rotate("cw" if heading_error > 0 else "ccw", round(abs(heading_error)))
                    entry["yaw_trim_deg"] = round(abs(heading_error))
                else:
                    self._emit("alert", f"start: heading is {heading_error:+.0f} degrees off +y; "
                                        "passive mode will not correct it")
        finally:
            self._append_log(entry)

    def _accept_arrival(self, wp: Waypoint, entry: dict) -> None:
        """No trim is left to send: land if the drone is still beyond the outer circle."""
        if entry["tolerance"] == "OUTSIDE":
            raise ToleranceAbort(f"tolerance: {wp.name} is {entry['arrival_error_cm']} cm from its "
                                 f"target after {entry['trim_rounds_sent']} trim rounds, beyond the "
                                 f"{OUTER_TOLERANCE_CM:.0f} cm outer circle")
        if entry["tolerance"] == "OUTER":
            self._emit("alert", f"{wp.name}: arrived {entry['arrival_error_cm']} cm from its target, "
                                "in the outer tolerance circle")

    # -- observation --------------------------------------------------------
    def _look(self, wp: Waypoint, entry: dict) -> None:
        entry["frames"] = []
        for offset in wp.looks_deg:
            self._look_to(offset)
            self.sleep_fn(SETTLE_S)
            frame = self._call(lambda: self.client.read_frame(timeout=10))
            path, digest = None, None
            if self.recorder is not None:
                saved_path = self.recorder.save(frame)
                path = str(saved_path)
                digest = hashlib.sha256(saved_path.read_bytes()).hexdigest()
            entry["frames"].append({"look_deg": offset, "path": path, "sha256": digest,
                                    "captured_at": datetime.now(timezone.utc).isoformat()})
            self._emit("observe", f"{wp.name}: captured at look offset {offset}")
        self._look_to(0)

    def _append_log(self, entry: dict) -> None:
        self.mission_log.parent.mkdir(parents=True, exist_ok=True)
        with self.mission_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def _fly_sequence(self) -> None:
        self._enter_altitude_band()
        height = self.envelope.position.z_cm
        if self.mission.start_x_cm is None:
            self._localise_start(height)
        else:
            self.envelope.position = Position(self.mission.start_x_cm, self.mission.start_y_cm, height)
        for wp in self.mission.waypoints:
            # F1: the entry is created BEFORE _go_to and always appended
            # (try/finally). An abort raised while still travelling to this
            # waypoint (the in-move watchdog, an envelope violation, ...)
            # used to leave no log line at all for the stop the drone was
            # heading to; "EN_ROUTE" is a sentinel this method never persists
            # as-is -- _go_to succeeding replaces it with "OPEN_LOOP" below,
            # and anything _localise reaches replaces it again.
            entry = {"waypoint": wp.name, "commanded_cm": [wp.x_cm, wp.y_cm, wp.z_cm],
                     "pose_mode": self.pose_mode, "status": "EN_ROUTE", "fix_cm": None,
                     "heading_deg": None, "heading_error_deg": None, "n": 0,
                     "spread_p95_cm": None, "arrival_error_cm": None, "containment": None,
                     "tolerance": None, "proposed_trims": None,
                     "trim_rounds_sent": 0, "looks_deg": list(wp.looks_deg),
                     "watchdog_land_error": None, "watchdog_fix_errors": 0, "height_cm": None,
                     "open_loop_fix_cm": None, "open_loop_error_cm": None, "localise_s": None,
                     "battery_pct": None}
            try:
                self._go_to(wp)
                entry["status"] = "OPEN_LOOP"
                # survey bridge (Task 11): the measured altitude telemetry
                # move_checked already stamped into self.envelope.position,
                # not the commanded target -- they agree within DEAD_BAND_CM
                # by construction but are not the same number.
                entry["height_cm"] = self.envelope.position.z_cm
                entry["battery_pct"] = self._battery_pct()
                if self.pose_mode != "off" and wp.localise:
                    self._localise(wp, entry)
                self._look(wp, entry)
            finally:
                if entry["status"] == "EN_ROUTE":
                    entry["status"] = "ABORTED_EN_ROUTE"
                    if self._watchdog_last_fix_cm is not None:
                        entry["fix_cm"] = self._watchdog_last_fix_cm
                    # land_during_move (still default off, pending bench test
                    # B1): surface a failed cross-thread `land` rather than
                    # leaving it write-only -- the watchdog already ends the
                    # flight through ContainmentAbort either way.
                    if self._watchdog_land_error is not None:
                        entry["watchdog_land_error"] = str(self._watchdog_land_error)
                entry["watchdog_fix_errors"] = self._watchdog_fix_errors
                self._append_log(entry)
