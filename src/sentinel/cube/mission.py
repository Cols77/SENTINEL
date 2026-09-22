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
from sentinel.cube.checkpoint import (
    CheckpointPlan,
    CheckpointPolicy,
    CommandLimits,
    CommandOutcome,
    PlannedCommand,
    plan_checkpoint,
)
from sentinel.envelope import EnvelopeViolation
from sentinel.models import Position
from sentinel.motion_bounds import TransportLimits, move_reply_upper_bound_s
from sentinel.pose.containment import Containment, check_containment
from sentinel.pose.detector import NoTagDetected, estimate_pose
from sentinel.pose.localise import LocalisationFix, localise
from sentinel.pose.replay import FrameRecorder
from sentinel.pose.scoring import heading_delta_deg
from sentinel.pose.stream import _to_bgr, run_pose_stream
from sentinel.pose.watchdog import (POLL_INTERVAL_S, PoseWatchdog, post_land_travel_cm,
                                    trip_margin_cm)
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
#: A mission may ask for more trim rounds per stop (the convergence test), never more than this.
TRIM_ROUNDS_LIMIT = 10
#: A mission may fly its legs faster than COMMAND_SPEED_CM_S; trims always go back to it.
#: 50 cm/s needs a 40 cm inset: B1 measured 35 cm of travel after `land` at 50 (see
#: ``required_inset_cm``). Faster than 50 has no measurement at all.
LEG_SPEED_LIMIT_CM_S = 50
#: Legs up to this speed fly with the historical 30 cm inset without further checks.
INSET_FREE_SPEED_CM_S = 40
#: Margin on top of the measured travel after `land`.
LAND_TRAVEL_MARGIN_CM = 5
#: Tag-implied height against `height?` + the site's height offset: beyond this, the site's
#: vertical model (takeoff height, altitude bias or tag heights) is wrong for this room.
VERTICAL_MISMATCH_CM = 25.0
#: Pause before a move that directly follows another. The Tello answers "ok" on reaching the
#: target but still brakes after a fast leg; a move sent at once was refused with "error Not
#: joystick" at 40 cm/s (09-22 104429) and at 50 cm/s (09-22 152455), and the drone landed.
SETTLE_AFTER_FAST_MOVE_S = 1.5
SETTLE_AFTER_SLOW_MOVE_S = 0.7
#: A trim round that leaves the stop farther from its target than this beyond the previous fix
#: ends the trimming there (the burst fix itself wanders by a couple of centimetres).
WORSE_MARGIN_CM = 2.0
#: The extra stop a ``return_to_start`` mission ends with.
RETURN_NAME = "RETURN"
YAW_TRIM_THRESHOLD_DEG = 10.0
PLAUSIBILITY_CM = 60.0
TOLERANCE_CM = 15.0
OUTER_TOLERANCE_CM = 30.0
SETTLE_S = 1.5
#: Written beside the captured frames, one JSON line per frame as it is saved.
FRAMES_LOG_NAME = "frames.jsonl"
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
    max_trim_rounds: int = MAX_TRIM_ROUNDS
    leg_speed_cm_s: int = int(COMMAND_SPEED_CM_S)
    #: After the last waypoint, fly back to where the drone took off (the measured START fix or
    #: the start pad) as one more stop, RETURN, localised and trimmed like any other.
    return_to_start: bool = False

    def __post_init__(self) -> None:
        if len(self.waypoints) + int(self.return_to_start) > MAX_WAYPOINTS:
            raise ValueError(f"a mission has at most {MAX_WAYPOINTS} stops, RETURN included")
        if not 1 <= self.max_trim_rounds <= TRIM_ROUNDS_LIMIT:
            raise ValueError(f"max_trim_rounds must be 1-{TRIM_ROUNDS_LIMIT}, got {self.max_trim_rounds}")
        if (type(self.leg_speed_cm_s) is not int
                or not COMMAND_SPEED_CM_S <= self.leg_speed_cm_s <= LEG_SPEED_LIMIT_CM_S):
            raise ValueError(f"leg_speed_cm_s must be {COMMAND_SPEED_CM_S:.0f}-{LEG_SPEED_LIMIT_CM_S}, "
                             f"got {self.leg_speed_cm_s}")


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
    tuning = {"max_trim_rounds": int(doc.get("max_trim_rounds", MAX_TRIM_ROUNDS)),
              "leg_speed_cm_s": doc.get("leg_speed_cm_s", int(COMMAND_SPEED_CM_S)),
              "return_to_start": bool(doc.get("return_to_start", False))}
    start = doc.get("start")
    if start is None:
        return Mission(None, None, waypoints, **tuning)
    return Mission(int(start["x_cm"]), int(start["y_cm"]), waypoints, **tuning)


def required_inset_cm(leg_speed_cm_s: float) -> float:
    """How far inside the cube the commanded area must end for a leg at this speed: the travel
    after `land` (measured at 20 and 50 cm/s) plus a margin. Legs up to 40 cm/s keep the
    historical 30 cm inset they were flown with."""
    if leg_speed_cm_s <= INSET_FREE_SPEED_CM_S:
        return 0.0
    return post_land_travel_cm(leg_speed_cm_s) + LAND_TRAVEL_MARGIN_CM


def tag_implied_height_cm(reports, tag_map, fix_cm) -> float | None:
    """Drone height above the floor implied by the tag distances of a burst, from the horizontal
    fix and each tag's mapped height. Coarse: the vertical is a small leg of a long triangle, so a
    1 cm range error moves it ~4 cm. The median over the burst is used; good to ~10 cm."""
    if tag_map is None or fix_cm is None:
        return None
    heights = []
    for report in reports:
        for obs in report.tag_observations:
            try:
                tag = tag_map.tag(obs.tag_id)
            except (KeyError, ValueError):
                continue
            scale_cm = tag_map.room_scale_m_per_unit * 100.0
            horizontal = math.dist(fix_cm, (tag.x_units * scale_cm, tag.y_units * scale_cm))
            slant = obs.distance_m * 100.0
            if slant > horizontal:
                heights.append(tag.z_units * 100.0 + math.sqrt(slant ** 2 - horizontal ** 2))
    if not heights:
        return None
    heights.sort()
    return round(heights[len(heights) // 2], 1)


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
                 watchdog_poll_interval_s: float = POLL_INTERVAL_S,
                 coverage=None, correlation_context: dict | None = None, **runner_kwargs) -> None:
        if pose_mode not in POSE_MODES:
            raise ValueError(f"pose_mode must be one of {POSE_MODES}, got {pose_mode!r}")
        if pose_mode != "off" and burst_fn is None and (tag_map is None or conn is None):
            raise ValueError("pose modes other than 'off' need a tag map and a pose database")
        runner_kwargs["retain_video_stream"] = True
        runner_kwargs["envelope"] = site.envelope()
        super().__init__(client, mode, minimum_battery, **runner_kwargs)
        self.mission, self.site, self.pose_mode = mission, site, pose_mode
        self.tag_map, self.conn, self.run_id = tag_map, conn, run_id
        self.correlation_context = dict(correlation_context) if correlation_context is not None else None
        self.mission_log = Path(mission_log)
        self.recorder = FrameRecorder(capture_dir) if capture_dir is not None and mode == "real" else None
        self.burst_fn = burst_fn if burst_fn is not None else self._live_burst
        self.sleep_fn = sleep_fn
        self.footprint = site.footprint()
        #: cube.coverage.CoverageModel: refuses stops the camera cannot localise from (None: unchecked)
        self.coverage = coverage
        self._start_fix_cm: tuple[float, float] | None = None
        #: speed of the last translation if nothing has let the drone settle since, else None
        self._unsettled_speed: int | None = None
        self._implausible_streak = 0
        # FlightTestRunner._preflight sets and reads back COMMAND_SPEED_CM_S.
        self._speed_cm_s = int(COMMAND_SPEED_CM_S)
        self._checkpoint_limits = CommandLimits(
            canonical_heading_deg=FACING_DEG,
            dead_band_cm=DEAD_BAND_CM,
            min_move_cm=MIN_MOVE_CM,
            slow_settle_s=SETTLE_AFTER_SLOW_MOVE_S,
            fast_settle_s=SETTLE_AFTER_FAST_MOVE_S,
            fast_speed_threshold_cm_s=int(COMMAND_SPEED_CM_S),
            trim_rounds=mission.max_trim_rounds,
            trim_distance_cm=int(PLAUSIBILITY_CM),
            transport_limits=self._transport_limits_for(client),
        )
        self._checkpoint_plans: list[CheckpointPlan] = []
        self._active_checkpoint_plan: CheckpointPlan | None = None
        self._active_checkpoint_entry: dict | None = None
        self._active_waypoint: Waypoint | None = None
        self._checkpoint_effects: list[tuple[str, str]] = []
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

    @staticmethod
    def _transport_limits_for(client) -> TransportLimits:
        limits = getattr(client, "transport_limits", None)
        return limits if isinstance(limits, TransportLimits) else TransportLimits()

    # -- planning -----------------------------------------------------------
    def _validate_mission(self) -> None:
        env = self.envelope
        in_xy = lambda x, y: env.min_x_cm <= x <= env.max_x_cm and env.min_y_cm <= y <= env.max_y_cm
        inset_cm = self.site.inset_m * 100.0
        speed = self.mission.leg_speed_cm_s
        if inset_cm < required_inset_cm(speed):
            raise PlanError(f"legs at {speed} cm/s need the commanded area {required_inset_cm(speed):.0f} cm "
                            f"inside the cube (travel after `land`); this site insets {inset_cm:.0f} cm")
        if self.land_during_move and trip_margin_cm(speed) > inset_cm:
            raise PlanError(f"land_during_move at {speed} cm/s trips {trip_margin_cm(speed):.0f} cm inside "
                            f"the cube edge, beyond the {inset_cm:.0f} cm inset: the watchdog would "
                            "land the drone at stops the mission is allowed to fly to")
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
            if self.coverage is not None and self.pose_mode != "off" and wp.localise:
                seen = self.coverage.verdict(wp.x_cm, wp.y_cm, wp.z_cm)
                if seen.level == "BLIND":
                    raise PlanError(f"waypoint {wp.name}: the tags can be lost from there ({seen.describe()}); "
                                    "move it, or mark it \"localise\": false to fly it open loop")
                if seen.level == "MARGINAL":
                    self._emit("alert", f"{wp.name}: tag coverage is marginal ({seen.describe()})")
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

    def _set_speed(self, cm_per_s: int) -> None:
        if cm_per_s == self._speed_cm_s:
            return
        self._emit("flight", f"setting speed {cm_per_s} cm/s")
        response = self._call(lambda: self.client.set_speed(cm_per_s))
        if not self._is_ok(response):
            raise RuntimeError(f"speed {cm_per_s} failed: {response}")
        self._speed_cm_s = cm_per_s

    def _step(self, direction: str, distance_cm: int, *, optional: bool,
              validate_vertical_telemetry: bool = True) -> bool:
        """One checked move. An optional step that would leave the bounds is skipped."""
        try:
            self.move_checked(direction, distance_cm,
                              validate_vertical_telemetry=validate_vertical_telemetry)
            self._unsettled_speed = self._speed_cm_s
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

    @property
    def checkpoint_plans(self) -> tuple[CheckpointPlan, ...]:
        """The exact immutable plans dispatched for this mission run."""

        return tuple(self._checkpoint_plans)

    @property
    def checkpoint_effects(self) -> tuple[tuple[str, str], ...]:
        """Effect trace for the current run, keyed to dispatched plan nodes."""

        return tuple(self._checkpoint_effects)

    def _plan_for_waypoint(self, wp: Waypoint) -> CheckpointPlan:
        """Translate a legacy waypoint once, then retain that object for all consumers."""

        start = self.envelope.position
        target = Position(wp.x_cm, wp.y_cm, wp.z_cm)
        trim_distance_cm = self._trim_distance_limit(start, target)
        limits = replace(
            self._checkpoint_limits,
            localise=self.pose_mode != "off" and wp.localise,
            trim_rounds=self.mission.max_trim_rounds if self.pose_mode == "reanchor" and wp.localise else 0,
            trim_distance_cm=trim_distance_cm,
        )
        plan = plan_checkpoint(
            checkpoint_id=wp.name,
            start=start,
            target=target,
            start_heading_deg=FACING_DEG,
            terminal_heading_deg=FACING_DEG,
            speed_cm_s=self.mission.leg_speed_cm_s,
            looks_deg=wp.looks_deg,
            capture_count=len(wp.looks_deg),
            limits=limits,
            policy=CheckpointPolicy.legacy_mission(),
        )
        # A reanchored estimate can lie a few centimetres outside a commanded
        # x/y bound.  Such an axis needs a minimum correction even when it is
        # inside the normal dead band.  Add that correction to the immutable
        # plan, rather than issuing an unplanned recovery movement.
        env = self.envelope
        bad_axes = [
            axis for axis, _, _ in _AXES
            if axis in ("x_cm", "y_cm")
            and not (getattr(env, f"min_{axis}") <= getattr(start, axis) <= getattr(env, f"max_{axis}"))
        ]
        recovery: list[PlannedCommand] = []
        targets = {"x_cm": wp.x_cm, "y_cm": wp.y_cm, "z_cm": wp.z_cm}
        for axis, negative, positive in _AXES:
            if axis not in bad_axes:
                continue
            delta = targets[axis] - getattr(start, axis)
            recovery.append(PlannedCommand(
                "MOVE", tuple(sorted((("axis", axis[:-3]),
                                       ("direction", positive if delta > 0 else negative),
                                       ("distance_cm", max(MIN_MOVE_CM, round(abs(delta)))),
                                       ("optional", "false")))),
                move_reply_upper_bound_s(
                    distance_cm=max(MIN_MOVE_CM, round(abs(delta))), speed_cm_s=self.mission.leg_speed_cm_s,
                    base_timeout_s=limits.transport_limits.reply_timeout_s,
                ), False,
            ))
            recovery.append(PlannedCommand(
                "SETTLE", (("after", "move"),),
                limits.fast_settle_s if self.mission.leg_speed_cm_s > limits.fast_speed_threshold_cm_s
                else limits.slow_settle_s, True,
            ))
        if recovery:
            # The normal graph was calculated from the pre-recovery position.
            # Replace each stale-axis leg with its leading correction; sending
            # both would turn a recovery into an overshoot.
            commands_without_recovered_axes: list[PlannedCommand] = []
            skip_settle = False
            for command in plan.commands:
                arguments = dict(command.arguments)
                if command.kind == "MOVE" and f"{arguments['axis']}_cm" in bad_axes:
                    skip_settle = True
                    continue
                if skip_settle and command.kind == "SETTLE" and arguments.get("after") == "move":
                    skip_settle = False
                    continue
                commands_without_recovered_axes.append(command)
            first_motion = next(
                (index for index, command in enumerate(commands_without_recovered_axes)
                 if command.kind in ("MOVE", "TRIM_ALLOWANCE", "LOCALISE", "CAPTURE")
                 or dict(command.arguments).get("phase") == "look"),
                len(commands_without_recovered_axes),
            )
            commands = (tuple(commands_without_recovered_axes[:first_motion]) + tuple(recovery)
                        + tuple(commands_without_recovered_axes[first_motion:]))
            plan = replace(plan, commands=commands,
                           worst_case_s=sum(command.upper_bound_s for command in commands))
        self._checkpoint_plans.append(plan)
        self._active_checkpoint_plan = plan
        return plan

    def _trim_distance_limit(self, start: Position, target: Position) -> int:
        """Bound a plausible reanchor's trim from both planned and skipped endpoints."""

        deviations = []
        for source, destination in ((start.x_cm, target.x_cm), (start.y_cm, target.y_cm)):
            delta = destination - source
            if abs(delta) <= DEAD_BAND_CM:
                endpoint = source
            else:
                endpoint = source + (1 if delta > 0 else -1) * max(MIN_MOVE_CM, round(abs(delta)))
            deviations.append(max(abs(destination - source), abs(destination - endpoint)))
        return math.ceil(max(deviations, default=0) + PLAUSIBILITY_CM)

    def _go_to(self, wp: Waypoint, entry: dict) -> None:
        self._active_checkpoint_entry = entry
        self._active_waypoint = wp
        try:
            self.step_executor.execute_legacy(self._plan_for_waypoint(wp), self.execute_command)
        finally:
            self._active_checkpoint_entry = None
            self._active_waypoint = None

    def execute_command(self, command: PlannedCommand) -> CommandOutcome:
        """Dispatch a planned command without re-deriving its path or duration."""

        arguments = dict(command.arguments)
        entry, waypoint = self._active_checkpoint_entry, self._active_waypoint
        if command.kind == "SET_SPEED":
            self._checkpoint_effects.append((command.kind, "speed"))
            speed_cm_s = arguments["speed_cm_s"]
            if type(speed_cm_s) is not int:
                raise ValueError("planned speed_cm_s must be an integer")
            self._set_speed(speed_cm_s)
        elif command.kind == "READ_TELEMETRY":
            if arguments["phase"] == "vertical":
                self._checkpoint_effects.append((command.kind, "vertical_height"))
                measured_height = self._require_height(self._call(self.client.height_cm))
                if not self.envelope.min_z_cm <= measured_height <= self.envelope.max_z_cm:
                    raise EnvelopeViolation(
                        f"measured vertical position is outside the cube: {measured_height} cm"
                    )
                self.envelope.position = replace(self.envelope.position, z_cm=measured_height)
            else:
                self._checkpoint_effects.append((command.kind, "telemetry"))
                battery = self._battery_pct()
            if entry is not None and arguments["phase"] == "final":
                entry["battery_pct"] = battery
                entry["height_cm"] = self.envelope.position.z_cm
        elif command.kind == "WATCHDOG":
            self._checkpoint_effects.append((command.kind, "watchdog"))
            self._watchdog_last_fix_cm = None
            self._watchdog_land_error = None
            self._watchdog_fix_errors = 0
        elif command.kind == "MOVE":
            self._checkpoint_effects.append((command.kind, "move"))
            self._execute_planned_move(arguments)
        elif command.kind == "SETTLE":
            self._checkpoint_effects.append((command.kind, "settle"))
            self.sleep_fn(command.upper_bound_s)
            self._unsettled_speed = None
        elif command.kind == "ROTATE":
            self._checkpoint_effects.append((command.kind, "rotation"))
            self._rotate(str(arguments["direction"]), round(float(arguments["degrees"])))
        elif command.kind == "TRIM_ALLOWANCE":
            self._checkpoint_effects.append((command.kind, "trim_allowance"))
            if entry is not None and entry["status"] == "EN_ROUTE":
                entry["status"] = "OPEN_LOOP"
                entry["height_cm"] = self.envelope.position.z_cm
        elif command.kind == "LOCALISE":
            self._checkpoint_effects.append((command.kind, "localise"))
            if waypoint is not None and entry is not None:
                entry["height_cm"] = self.envelope.position.z_cm
                self._localise(waypoint, entry)
        elif command.kind == "CAPTURE":
            self._checkpoint_effects.append((command.kind, "capture"))
            if waypoint is not None and entry is not None:
                self._capture(waypoint, entry, int(arguments["look_deg"]))
        elif command.kind == "LAND":
            self._checkpoint_effects.append((command.kind, "land"))
            response = self._call(self.client.land)
            if not self._is_ok(response):
                raise RuntimeError(f"land failed: {response}")
        return CommandOutcome()

    def _execute_planned_move(self, arguments: dict[str, object]) -> None:
        direction, distance = str(arguments["direction"]), int(arguments["distance_cm"])
        axis = f"{arguments['axis']}_cm"
        optional = arguments.get("optional") == "true"
        env = self.envelope
        bad_axes = [
            name for name, _, _ in _AXES
            if name in ("x_cm", "y_cm")
            and not (getattr(env, f"min_{name}") <= getattr(env.position, name) <= getattr(env, f"max_{name}"))
        ]
        widened = []
        if axis in bad_axes:
            optional = False
            for other in bad_axes:
                if other == axis:
                    continue
                current = getattr(env.position, other)
                lo_attr, hi_attr = f"min_{other}", f"max_{other}"
                lo, hi = getattr(env, lo_attr), getattr(env, hi_attr)
                widened.append((lo_attr, lo, hi_attr, hi))
                setattr(env, lo_attr, min(lo, current))
                setattr(env, hi_attr, max(hi, current))
        try:
            self._step(direction, distance, optional=optional,
                       validate_vertical_telemetry=axis != "z_cm")
        finally:
            for lo_attr, lo, hi_attr, hi in widened:
                setattr(env, lo_attr, lo)
                setattr(env, hi_attr, hi)

    def move_checked(self, direction: str, distance_cm: int, *,
                     validate_vertical_telemetry: bool = True,
                     commit_position: bool = True) -> Position:
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
            return super().move_checked(direction, distance_cm,
                                        validate_vertical_telemetry=validate_vertical_telemetry,
                                        commit_position=commit_position)
        immediate = self.land_during_move
        watchdog = PoseWatchdog(
            self._watchdog_fix_fn, self.footprint,
            poll_interval_s=self._watchdog_poll_interval_s,
            margin_cm=trip_margin_cm(self._speed_cm_s) if immediate else 0.0,
            on_trip=self._send_land_during_move if immediate else None)
        watchdog.start()
        move_error: Exception | None = None
        try:
            candidate = super().move_checked(direction, distance_cm,
                                             validate_vertical_telemetry=validate_vertical_telemetry,
                                             commit_position=commit_position)
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
        return candidate

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
            self.step_executor.land_during_command(getattr(self.client, "land_now", self.client.land))
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
        plan_commands = self._active_checkpoint_plan.commands if self._active_checkpoint_plan else ()
        allowance = next(
            (dict(command.arguments) for command in plan_commands
             if command.kind == "TRIM_ALLOWANCE"),
            {"max_rounds": self.mission.max_trim_rounds, "max_distance_cm": int(PLAUSIBILITY_CM),
             "max_yaw_deg": 180, "move_upper_bound_s": 8.0, "rotate_upper_bound_s": 4.0,
             "speed_cm_s": int(COMMAND_SPEED_CM_S), "speed_upper_bound_s": 1.0,
             "settle_s": SETTLE_AFTER_SLOW_MOVE_S},
        )
        max_rounds = int(allowance["max_rounds"])
        max_distance_cm = int(allowance["max_distance_cm"])
        localise_arguments = next(
            (dict(command.arguments) for command in plan_commands if command.kind == "LOCALISE"),
            {"settle_s": SETTLE_S},
        )
        localise_settle_s = float(localise_arguments["settle_s"])
        while True:
            self._checkpoint_effects.append(("LOCALISE", "settle"))
            self.sleep_fn(localise_settle_s)
            self._unsettled_speed = None
            self._checkpoint_effects.append(("LOCALISE", "burst"))
            reports = self.burst_fn()
            result = localise(reports)
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
                self._check_vertical(wp.name, reports, fix, entry)
            entry.setdefault("trim_errors_cm", []).append(entry["arrival_error_cm"])
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
                degrees = round(abs(heading_error))
                if degrees > int(allowance["max_yaw_deg"]):
                    raise PlanError(f"{wp.name}: yaw trim exceeds the checkpoint allowance")
                self._checkpoint_effects.append(("TRIM_ALLOWANCE", "yaw_trim"))
                self.execute_command(PlannedCommand(
                    "ROTATE", tuple(sorted((("degrees", degrees),
                                             ("direction", "cw" if heading_error > 0 else "ccw"),
                                             ("phase", "yaw_trim")))),
                    float(allowance["rotate_upper_bound_s"]), False,
                ))
            # Inside the tolerance circle, a 20 cm minimum move can only make things worse
            # (2026-09-21 enlarged trial: 12 cm became 25.7 cm after two trims).
            if not trims or rounds >= max_rounds or entry["tolerance"] == "INSIDE":
                return self._accept_arrival(wp, entry)
            errors = entry["trim_errors_cm"]
            if rounds > 0 and errors[-1] > errors[-2] + WORSE_MARGIN_CM:
                # 20 cm minimum trims overshoot errors of 15-25 cm; another round can grow it
                # further (09-22 FAR_MID_HI: 19.4 -> 23.4 -> 34.2 cm).
                entry["trims_stopped"] = "WORSE"
                self._emit("alert", f"{wp.name}: the last trim made it worse ({errors[-2]} -> "
                                    f"{errors[-1]} cm); no more trims at this stop")
                return self._accept_arrival(wp, entry)
            self._checkpoint_effects.append(("TRIM_ALLOWANCE", "trim_speed"))
            self.execute_command(PlannedCommand(
                "SET_SPEED", (("speed_cm_s", int(allowance["speed_cm_s"])),),
                float(allowance["speed_upper_bound_s"]), False,
            ))
            if any(distance > max_distance_cm for _, distance in trims):
                raise PlanError(f"{wp.name}: trim exceeds the checkpoint allowance of {max_distance_cm} cm")
            axis_by_direction = {"left": "x", "right": "x", "back": "y", "forward": "y"}
            sent = []
            for index, (direction, distance) in enumerate(trims):
                before = self.envelope.position
                self._checkpoint_effects.append(("TRIM_ALLOWANCE", "trim_move"))
                self.execute_command(PlannedCommand(
                    "MOVE", tuple(sorted((("axis", axis_by_direction[direction]), ("direction", direction),
                                           ("distance_cm", distance), ("optional", "true"),
                                           ("phase", "trim")))),
                    float(allowance["move_upper_bound_s"]), False,
                ))
                sent.append(self.envelope.position != before)
                if index < len(trims) - 1:
                    self._checkpoint_effects.append(("TRIM_ALLOWANCE", "trim_settle"))
                    self.execute_command(PlannedCommand(
                        "SETTLE", (("after", "trim_move"),), float(allowance["settle_s"]), True,
                    ))
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
            self._start_fix_cm = fix
            if abs(heading_error) > YAW_TRIM_THRESHOLD_DEG:
                if self.pose_mode == "reanchor":
                    self._rotate("cw" if heading_error > 0 else "ccw", round(abs(heading_error)))
                    entry["yaw_trim_deg"] = round(abs(heading_error))
                else:
                    self._emit("alert", f"start: heading is {heading_error:+.0f} degrees off +y; "
                                        "passive mode will not correct it")
        finally:
            self._append_log(entry)

    def _check_vertical(self, name: str, reports, fix, entry: dict) -> None:
        """Log the tag-implied height beside `height?` + takeoff height; alert when they disagree.
        At a new venue a wrong takeoff height or tag height shows here on the first fix, before a
        stop the coverage guard passed turns out blind."""
        implied = tag_implied_height_cm(reports, self.tag_map, fix)
        entry["tag_height_cm"] = implied
        if implied is None or entry.get("height_cm") is None:
            return
        expected = entry["height_cm"] + self.site.height_offset_cm
        entry["vertical_mismatch_cm"] = round(implied - expected, 1)
        if abs(implied - expected) > VERTICAL_MISMATCH_CM:
            self._emit("alert", f"{name}: the tags put the drone at {implied:.0f} cm, `height?` plus the "
                                f"site's {self.site.height_offset_cm:.0f} cm offset says {expected:.0f} cm: the "
                                "site's takeoff height, altitude bias or tag heights are wrong, and so "
                                "is its tag coverage")

    def _return_waypoint(self) -> Waypoint:
        """RETURN: where the drone took off, at the last waypoint's altitude. A takeoff point outside
        the commanded area (allowed: takeoff only has to be inside the cube) is replaced by the
        nearest commanded point."""
        env = self.envelope
        x, y = self._start_fix_cm
        target = (min(max(round(x), env.min_x_cm), env.max_x_cm),
                  min(max(round(y), env.min_y_cm), env.max_y_cm))
        if math.dist(target, (x, y)) > 1:
            self._emit("alert", f"RETURN: takeoff point ({x:.0f}, {y:.0f}) is outside the commanded area; "
                                f"returning to the nearest commanded point {target}")
        return Waypoint(RETURN_NAME, target[0], target[1], self.mission.waypoints[-1].z_cm)

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
    def _capture(self, wp: Waypoint, entry: dict, offset: int) -> None:
        """Capture one planned look.  Rotation and settling are separate plan nodes."""

        entry.setdefault("frames", [])
        frame = self._call(lambda: self.client.read_frame(timeout=10))
        path, digest = None, None
        if self.recorder is not None:
            saved_path = self.recorder.save(frame)
            path = str(saved_path)
            digest = hashlib.sha256(saved_path.read_bytes()).hexdigest()
        record = {"look_deg": offset, "path": path, "sha256": digest,
                  "captured_at": datetime.now(timezone.utc).isoformat()}
        if path is not None and self.correlation_context is not None:
            from dataclasses import asdict
            from sentinel.room.ingest import capture_request
            from sentinel.survey.live_detect import LIVE_CLASSES
            record["perception_request"] = asdict(capture_request(
                self.correlation_context, frame_path=path, run_id=self.run_id,
                captured_at=record["captured_at"], classes=LIVE_CLASSES,
            ))
        entry["frames"].append(record)
        if path is not None:
            self._announce_frame(wp.name, record)
        self._emit("observe", f"{wp.name}: captured at look offset {offset}")

    def _announce_frame(self, stop: str, record: dict) -> None:
        """One line per saved frame in ``frames.jsonl`` beside the frames, written the
        moment the frame is on disk, so a separate process (scripts/live_detect.py)
        can detect on it while the drone flies on. The mission log is only written
        when a stop ends. A write failure is an alert, never a flight failure."""
        from sentinel.room.ingest import InvalidPerception
        try:
            log = Path(record["path"]).parent / FRAMES_LOG_NAME
            document = {"stop": stop, "run_id": self.run_id, **record}
            if self.correlation_context is not None:
                from sentinel.room.ingest import append_jsonl_record
                append_jsonl_record(log, document)
            else:
                with log.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(document) + "\n")
        except (OSError, InvalidPerception) as error:
            self._emit("alert", f"{stop}: frame not announced for live detection ({error})")

    def _append_log(self, entry: dict) -> None:
        self.mission_log.parent.mkdir(parents=True, exist_ok=True)
        with self.mission_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def _fly_sequence(self) -> None:
        self._checkpoint_plans.clear()
        self._checkpoint_effects.clear()
        self._enter_altitude_band()
        height = self.envelope.position.z_cm
        if self.mission.start_x_cm is None:
            self._localise_start(height)
        else:
            self.envelope.position = Position(self.mission.start_x_cm, self.mission.start_y_cm, height)
            self._start_fix_cm = (float(self.mission.start_x_cm), float(self.mission.start_y_cm))
        stops = list(self.mission.waypoints)
        if self.mission.return_to_start:
            stops.append(self._return_waypoint())
        for wp in stops:
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
                     "battery_pct": None, "leg_speed_cm_s": self.mission.leg_speed_cm_s,
                     "trim_errors_cm": []}
            try:
                self._go_to(wp, entry)
                if wp.name == RETURN_NAME and entry["fix_cm"] is not None:
                    entry["return_error_cm"] = round(math.dist(entry["fix_cm"], self._start_fix_cm), 1)
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
