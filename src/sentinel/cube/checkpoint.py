"""Immutable, heading-aware command plans for one cube checkpoint.

The module deliberately has no dependency on :mod:`sentinel.cube.mission` so
estimation and execution can consume the exact same command graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping, Protocol, TypeAlias

from sentinel.models import Position
from sentinel.motion_bounds import move_reply_upper_bound_s, rotate_reply_upper_bound_s


CommandKind: TypeAlias = Literal[
    "SET_SPEED", "READ_TELEMETRY", "MOVE", "ROTATE", "SETTLE", "WATCHDOG",
    "LOCALISE", "TRIM_ALLOWANCE", "CAPTURE", "LAND",
]
ArgumentValue: TypeAlias = int | float | str


@dataclass(frozen=True, slots=True)
class PlannedCommand:
    kind: CommandKind
    arguments: tuple[tuple[str, ArgumentValue], ...]
    upper_bound_s: float
    interruptible: bool

    def __post_init__(self) -> None:
        if tuple(sorted(self.arguments)) != self.arguments:
            raise ValueError("planned command arguments must be key-sorted")
        if any(not isinstance(key, str) or isinstance(value, bool)
               or not isinstance(value, (int, float, str)) for key, value in self.arguments):
            raise TypeError("planned command arguments must be str keys and int, float, or str values")
        if self.upper_bound_s < 0:
            raise ValueError("planned command upper_bound_s must not be negative")

    @classmethod
    def from_mapping(cls, kind: CommandKind, arguments: Mapping[str, ArgumentValue], *,
                     upper_bound_s: float, interruptible: bool) -> PlannedCommand:
        """Freeze a caller-owned mapping before it can affect an audit digest."""

        return cls(kind, tuple(sorted(arguments.items())), upper_bound_s, interruptible)


@dataclass(frozen=True, slots=True)
class CheckpointPlan:
    checkpoint_id: str
    start: Position
    target: Position
    start_heading_deg: float
    terminal_heading_deg: float
    commands: tuple[PlannedCommand, ...]
    worst_case_s: float

    def __post_init__(self) -> None:
        total = sum(command.upper_bound_s for command in self.commands)
        if self.worst_case_s != total:
            raise ValueError("worst_case_s must equal the command upper-bound sum")


@dataclass(frozen=True, slots=True)
class HomeDefinition:
    pad_id: str
    nominal_pose: Position
    heading_deg: float
    route_id: str


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """A sink's result for one dispatched command.

    Later lifecycle work adds command-state and telemetry details; this small
    immutable result is enough to make dispatch explicit at the extraction
    boundary.
    """

    completed: bool = True
    detail: str | None = None


class CheckpointCommandSink(Protocol):
    def execute_command(self, command: PlannedCommand) -> CommandOutcome: ...


@dataclass(frozen=True, slots=True)
class CheckpointPolicy:
    """Ordering policy for an immutable command graph.

    The public default is the checkpoint-native look/capture/localise group.
    The named legacy policy is used only while translating historical
    ``Mission`` waypoints whose public log order predates this graph.
    """

    localise_after_each_capture: bool = True

    @classmethod
    def legacy_mission(cls) -> "CheckpointPolicy":
        return cls(localise_after_each_capture=False)


@dataclass(frozen=True, slots=True)
class CommandLimits:
    """The calibrated constants used to build a checkpoint command graph."""

    canonical_heading_deg: float = 90.0
    dead_band_cm: int = 10
    min_move_cm: int = 20
    slow_settle_s: float = 0.7
    fast_settle_s: float = 1.5
    fast_speed_threshold_cm_s: int = 20
    set_speed_s: float = 1.0
    telemetry_s: float = 1.0
    watchdog_s: float = 0.0
    motion_base_timeout_s: float = 7.0
    capture_s: float = 10.0
    #: Stationary settle plus the bounded pose-burst window.
    localise_s: float = 13.5
    look_settle_s: float = 1.5
    localise: bool = True
    trim_rounds: int = 2
    trim_distance_cm: int = 80
    trim_speed_cm_s: int = 20
    trim_yaw_limit_deg: int = 180


def _arguments(**values: ArgumentValue) -> tuple[tuple[str, ArgumentValue], ...]:
    """Copy and canonicalize user-facing arguments before they enter a plan."""

    return tuple(sorted(values.items()))


def _command(kind: CommandKind, upper_bound_s: float, interruptible: bool, **arguments: ArgumentValue) -> PlannedCommand:
    return PlannedCommand(kind, _arguments(**arguments), upper_bound_s, interruptible)


def _rotation(current_deg: float, target_deg: float, limits: CommandLimits, phase: str) -> tuple[PlannedCommand, float]:
    delta = (target_deg - current_deg + 180.0) % 360.0 - 180.0
    if abs(delta) < 1e-9:
        return (), target_deg
    direction = "ccw" if delta > 0 else "cw"
    return (_command(
        "ROTATE", rotate_reply_upper_bound_s(degrees=abs(delta), base_timeout_s=limits.motion_base_timeout_s),
        False, degrees=abs(delta), direction=direction, phase=phase,
    ),), target_deg


def plan_checkpoint(*, checkpoint_id: str, start: Position, target: Position,
                    start_heading_deg: float, terminal_heading_deg: float,
                    speed_cm_s: int, looks_deg: tuple[int, ...], capture_count: int,
                    limits: CommandLimits, policy: CheckpointPolicy | None = None) -> CheckpointPlan:
    """Build the one immutable command sequence shared by runtime and forecasts."""

    if isinstance(speed_cm_s, bool) or speed_cm_s <= 0:
        raise ValueError("speed_cm_s must be a positive integer")
    if capture_count < 0:
        raise ValueError("capture_count must not be negative")
    if terminal_heading_deg != limits.canonical_heading_deg:
        raise ValueError("checkpoint terminal heading must be canonical")
    if capture_count and not looks_deg:
        raise ValueError("captures require at least one requested look")
    if limits.trim_rounds < 0:
        raise ValueError("trim_rounds must not be negative")
    policy = policy or CheckpointPolicy()

    commands: list[PlannedCommand] = [
        _command("SET_SPEED", limits.set_speed_s, False, speed_cm_s=speed_cm_s),
        _command("READ_TELEMETRY", limits.telemetry_s, True, phase="admission"),
        _command("WATCHDOG", limits.watchdog_s, True, phase="admission"),
    ]
    heading = start_heading_deg
    rotation, heading = _rotation(heading, limits.canonical_heading_deg, limits, "admission")
    commands.extend(rotation)

    axes = (
        ("x", start.x_cm, target.x_cm, "left", "right"),
        ("y", start.y_cm, target.y_cm, "back", "forward"),
        ("z", start.z_cm, target.z_cm, "down", "up"),
    )
    moves: list[PlannedCommand] = []
    for axis, source, destination, negative, positive in axes:
        delta = destination - source
        if abs(delta) <= limits.dead_band_cm:
            continue
        distance = max(limits.min_move_cm, round(abs(delta)))
        moves.append(_command(
            "MOVE", move_reply_upper_bound_s(
                distance_cm=distance, speed_cm_s=speed_cm_s, base_timeout_s=limits.motion_base_timeout_s,
            ), False, axis=axis,
            direction=positive if delta > 0 else negative, distance_cm=distance,
            optional="true" if distance == limits.min_move_cm else "false",
        ))
    for index, move in enumerate(moves):
        commands.append(move)
        # A localisation node begins with its own calibrated stationary hover,
        # so the final leg does not need a duplicate standalone settle.
        if index < len(moves) - 1 or not limits.localise or policy.localise_after_each_capture:
            commands.append(_command(
                "SETTLE",
                limits.fast_settle_s if speed_cm_s > limits.fast_speed_threshold_cm_s else limits.slow_settle_s,
                True,
                after="move",
            ))

    # A trim round can set precision speed, correct X and Y, wait for the
    # hover/burst, and restore canonical yaw.  Reserve every round even when
    # the first accepted localisation needs no correction.
    commands.append(_command(
        "TRIM_ALLOWANCE",
        limits.trim_rounds * (
            limits.set_speed_s
            + 2 * move_reply_upper_bound_s(
                distance_cm=limits.trim_distance_cm, speed_cm_s=limits.trim_speed_cm_s,
                base_timeout_s=limits.motion_base_timeout_s,
            )
            + limits.localise_s + limits.look_settle_s
            + rotate_reply_upper_bound_s(
                degrees=limits.trim_yaw_limit_deg, base_timeout_s=limits.motion_base_timeout_s,
            )
        ),
        False,
        max_distance_cm=limits.trim_distance_cm, max_rounds=limits.trim_rounds,
        max_yaw_deg=limits.trim_yaw_limit_deg,
        move_upper_bound_s=move_reply_upper_bound_s(
            distance_cm=limits.trim_distance_cm, speed_cm_s=limits.trim_speed_cm_s,
            base_timeout_s=limits.motion_base_timeout_s,
        ),
        rotate_upper_bound_s=rotate_reply_upper_bound_s(
            degrees=limits.trim_yaw_limit_deg, base_timeout_s=limits.motion_base_timeout_s,
        ), speed_cm_s=limits.trim_speed_cm_s,
        speed_upper_bound_s=limits.set_speed_s, settle_s=limits.slow_settle_s,
    ))
    for index in range(capture_count):
        look = looks_deg[index % len(looks_deg)]
        rotation, heading = _rotation(heading, limits.canonical_heading_deg + look, limits, "look")
        commands.extend(rotation)
        commands.append(_command("SETTLE", limits.look_settle_s, True, after="look"))
        commands.append(_command("CAPTURE", limits.capture_s, True, index=index, look_deg=look))
        if limits.localise and policy.localise_after_each_capture:
            commands.append(_command(
                "LOCALISE", limits.localise_s, True, phase="capture", settle_s=limits.look_settle_s,
            ))

    if limits.localise and not policy.localise_after_each_capture:
        # Compatibility ordering for legacy mission logs: arrival is measured
        # and trimmed before the camera is pointed at observation looks.
        insertion = next(index for index, command in enumerate(commands)
                         if command.kind == "TRIM_ALLOWANCE") + 1
        commands.insert(insertion, _command(
            "LOCALISE", limits.localise_s, True, phase="arrival", settle_s=limits.look_settle_s,
        ))

    rotation, heading = _rotation(heading, terminal_heading_deg, limits, "restore")
    commands.extend(rotation)
    commands.append(_command("READ_TELEMETRY", limits.telemetry_s, True, phase="final"))
    frozen_commands = tuple(commands)
    return CheckpointPlan(
        checkpoint_id=checkpoint_id, start=start, target=target,
        start_heading_deg=start_heading_deg, terminal_heading_deg=terminal_heading_deg,
        commands=frozen_commands,
        worst_case_s=sum(command.upper_bound_s for command in frozen_commands),
    )
