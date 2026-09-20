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
import json
import math
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from sentinel.envelope import EnvelopeViolation
from sentinel.models import Position
from sentinel.pose.containment import Containment, check_containment
from sentinel.pose.localise import LocalisationFix, localise
from sentinel.pose.replay import FrameRecorder
from sentinel.pose.scoring import heading_delta_deg
from sentinel.pose.stream import run_pose_stream
from sentinel.runner import FlightTestRunner

POSE_MODES = ("off", "passive", "reanchor")
MAX_WAYPOINTS = 8
MAX_LOOK_DEG = 170
FACING_DEG = 90.0
DEAD_BAND_CM = 10
MIN_MOVE_CM = 20
MAX_TRIM_ROUNDS = 2
YAW_TRIM_THRESHOLD_DEG = 10.0
PLAUSIBILITY_CM = 60.0
SETTLE_S = 1.5
BURST_MAX_S = 12.0
BURST_TARGET_REPORTS = 12
CM_PER_UNIT = 10.0


class PlanError(ValueError):
    """The mission does not fit the commanded bounds."""


class ContainmentAbort(RuntimeError):
    """The measured position left the operating cube."""


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
    start_x_cm: int
    start_y_cm: int
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
    return Mission(int(doc["start"]["x_cm"]), int(doc["start"]["y_cm"]), waypoints)


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
                 sleep_fn: Callable[[float], None] = time.sleep, **runner_kwargs) -> None:
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
        self._validate_mission()

    # -- planning -----------------------------------------------------------
    def _validate_mission(self) -> None:
        env = self.envelope
        in_xy = lambda x, y: env.min_x_cm <= x <= env.max_x_cm and env.min_y_cm <= y <= env.max_y_cm
        if not in_xy(self.mission.start_x_cm, self.mission.start_y_cm):
            raise PlanError("the start pad is outside the commanded bounds")
        for wp in self.mission.waypoints:
            if not (in_xy(wp.x_cm, wp.y_cm) and env.min_z_cm <= wp.z_cm <= env.max_z_cm):
                raise PlanError(f"waypoint {wp.name} is outside the commanded bounds")
            if any(abs(look) > MAX_LOOK_DEG for look in wp.looks_deg):
                raise PlanError(f"waypoint {wp.name} has a look offset beyond {MAX_LOOK_DEG} degrees")

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
        for direction, distance in self._moves_toward(target, self.envelope.position.as_dict()):
            # a minimum-move overshoot (delta under 20 cm) is optional; a real leg is not
            overshoot = distance == MIN_MOVE_CM
            self._step(direction, distance, optional=overshoot)

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
                "heading_error_deg": round(heading_error, 1), "n": result.n,
                "spread_p95_cm": round(result.spread_p95_cm, 2),
                "arrival_error_cm": round(math.dist(fix, (wp.x_cm, wp.y_cm)), 1),
                "containment": containment.value,
                "proposed_trims": [list(t) for t in trims], "trim_rounds_sent": rounds,
            })
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
            if not trims or rounds >= MAX_TRIM_ROUNDS:
                return
            sent = [self._step(direction, distance, optional=True) for direction, distance in trims]
            if not any(sent):
                return
            rounds += 1

    # -- observation --------------------------------------------------------
    def _look(self, wp: Waypoint) -> None:
        for offset in wp.looks_deg:
            self._look_to(offset)
            self.sleep_fn(SETTLE_S)
            frame = self._call(lambda: self.client.read_frame(timeout=10))
            if self.recorder is not None:
                self.recorder.save(frame)
            self._emit("observe", f"{wp.name}: captured at look offset {offset}")
        self._look_to(0)

    def _append_log(self, entry: dict) -> None:
        self.mission_log.parent.mkdir(parents=True, exist_ok=True)
        with self.mission_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def _fly_sequence(self) -> None:
        self._enter_altitude_band()
        height = self.envelope.position.z_cm
        self.envelope.position = Position(self.mission.start_x_cm, self.mission.start_y_cm, height)
        for wp in self.mission.waypoints:
            self._go_to(wp)
            entry = {"waypoint": wp.name, "commanded_cm": [wp.x_cm, wp.y_cm, wp.z_cm],
                     "pose_mode": self.pose_mode, "status": "OPEN_LOOP", "fix_cm": None,
                     "heading_error_deg": None, "n": 0, "spread_p95_cm": None,
                     "arrival_error_cm": None, "containment": None, "proposed_trims": None,
                     "trim_rounds_sent": 0, "looks_deg": list(wp.looks_deg)}
            try:
                if self.pose_mode != "off" and wp.localise:
                    self._localise(wp, entry)
                self._look(wp)
            finally:
                self._append_log(entry)
