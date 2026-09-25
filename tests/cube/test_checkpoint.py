from __future__ import annotations

import hashlib

import pytest

from sentinel.cube.checkpoint import CheckpointPolicy, CommandLimits, PlannedCommand, plan_checkpoint
from sentinel.models import Position
from sentinel.motion_bounds import TransportLimits, move_reply_upper_bound_s


def test_checkpoint_plans_canonical_axis_moves_and_bounds():
    plan = plan_checkpoint(
        checkpoint_id="WP1",
        start=Position(0, 0, 100),
        target=Position(11, -12, 109),
        start_heading_deg=80,
        terminal_heading_deg=90,
        speed_cm_s=40,
        looks_deg=(30, -30),
        capture_count=2,
        limits=CommandLimits(),
    )

    assert plan.checkpoint_id == "WP1"
    assert plan.start == Position(0, 0, 100)
    assert plan.target == Position(11, -12, 109)
    assert [command.kind for command in plan.commands] == [
        "SET_SPEED", "READ_TELEMETRY", "WATCHDOG", "ROTATE",
        "MOVE", "SETTLE", "MOVE", "SETTLE", "TRIM_ALLOWANCE",
        "ROTATE", "SETTLE", "CAPTURE", "LOCALISE",
        "ROTATE", "SETTLE", "CAPTURE", "LOCALISE",
        "ROTATE", "READ_TELEMETRY",
    ]
    moves = [command for command in plan.commands if command.kind == "MOVE"]
    assert [dict(command.arguments) for command in moves] == [
        {"axis": "x", "direction": "right", "distance_cm": 20, "optional": "true"},
        {"axis": "y", "direction": "back", "distance_cm": 20, "optional": "true"},
    ]
    assert sum(command.upper_bound_s for command in plan.commands) == plan.worst_case_s
    allowance = next(command for command in plan.commands if command.kind == "TRIM_ALLOWANCE")
    assert allowance.upper_bound_s >= 2 * (2 * 8.0 + 1.5 + 13.5 + 7.0)
    assert dict(next(command for command in plan.commands if command.kind == "LOCALISE").arguments)["settle_s"] == 1.5


def test_a_vertical_move_is_bounded_by_the_slow_climb_floor_and_horizontal_moves_are_not():
    # Live 2026-09-25 14:10: ``up 40`` at speed 20 (bound 10 s) exceeded its
    # deadline mid-climb and the flight landed for it; this unit's vertical
    # channel is slow and its height sensor unreliable.
    plan = plan_checkpoint(
        checkpoint_id="WP-Z",
        start=Position(0, 0, 30),
        target=Position(60, 0, 70),
        start_heading_deg=90,
        terminal_heading_deg=90,
        speed_cm_s=20,
        looks_deg=(0,),
        capture_count=1,
        limits=CommandLimits(),
    )
    moves = {dict(c.arguments)["axis"]: c for c in plan.commands if c.kind == "MOVE"}
    assert moves["z"].upper_bound_s == 15
    assert moves["x"].upper_bound_s == 12  # 60/(0.5*20) + 6, unchanged by the floor


def test_legacy_checkpoint_policy_keeps_localisation_before_observation_groups():
    plan = plan_checkpoint(
        checkpoint_id="legacy", start=Position(0, 0, 100), target=Position(0, 20, 100),
        start_heading_deg=90, terminal_heading_deg=90, speed_cm_s=20,
        looks_deg=(45,), capture_count=1, limits=CommandLimits(),
        policy=CheckpointPolicy.legacy_mission(),
    )

    assert [command.kind for command in plan.commands] == [
        "SET_SPEED", "READ_TELEMETRY", "WATCHDOG", "MOVE", "TRIM_ALLOWANCE", "LOCALISE",
        "ROTATE", "SETTLE", "CAPTURE", "ROTATE", "READ_TELEMETRY",
    ]


def test_legacy_checkpoint_policy_keeps_zero_degree_look_after_localisation():
    plan = plan_checkpoint(
        checkpoint_id="legacy-zero", start=Position(0, 0, 100), target=Position(0, 20, 100),
        start_heading_deg=90, terminal_heading_deg=90, speed_cm_s=20,
        looks_deg=(0,), capture_count=1, limits=CommandLimits(),
        policy=CheckpointPolicy.legacy_mission(),
    )

    assert [command.kind for command in plan.commands] == [
        "SET_SPEED", "READ_TELEMETRY", "WATCHDOG", "MOVE", "TRIM_ALLOWANCE", "LOCALISE",
        "SETTLE", "CAPTURE", "READ_TELEMETRY",
    ]


def test_checkpoint_motion_nodes_use_the_shared_transport_bounds():
    plan = plan_checkpoint(
        checkpoint_id="bounds", start=Position(0, 0, 100), target=Position(120, 0, 100),
        start_heading_deg=-90, terminal_heading_deg=90, speed_cm_s=20,
        looks_deg=(), capture_count=0, limits=CommandLimits(),
    )

    assert next(command.upper_bound_s for command in plan.commands if command.kind == "MOVE") == 18
    assert next(command.upper_bound_s for command in plan.commands if command.kind == "ROTATE") == 12


def test_checkpoint_uses_configured_transport_limits_for_speed_and_vertical_telemetry():
    plan = plan_checkpoint(
        checkpoint_id="configured", start=Position(0, 0, 100), target=Position(0, 0, 120),
        start_heading_deg=90, terminal_heading_deg=90, speed_cm_s=20,
        looks_deg=(), capture_count=0,
        limits=CommandLimits(transport_limits=TransportLimits(
            reply_timeout_s=30.0, control_attempts=3, read_timeout_s=30.0,
        )),
    )

    speed = next(command for command in plan.commands if command.kind == "SET_SPEED")
    move, vertical_read = [command for command in plan.commands if command.kind in {"MOVE", "READ_TELEMETRY"}
                           and (command.kind == "MOVE" or dict(command.arguments).get("phase") == "vertical")]
    assert speed.upper_bound_s == 90
    assert move.upper_bound_s == 30
    assert vertical_read.upper_bound_s == 30
    assert dict(vertical_read.arguments)["phase"] == "vertical"


def test_checkpoint_preserves_sdk_default_timeout_floor_for_speed_and_vertical_query():
    plan = plan_checkpoint(
        checkpoint_id="sdk-floor", start=Position(0, 0, 100), target=Position(0, 0, 120),
        start_heading_deg=90, terminal_heading_deg=90, speed_cm_s=20,
        looks_deg=(), capture_count=0,
        limits=CommandLimits(transport_limits=TransportLimits(
            reply_timeout_s=1.0, control_attempts=3,
            control_timeout_s=7.0, read_timeout_s=7.0,
        )),
    )

    assert next(command.upper_bound_s for command in plan.commands if command.kind == "SET_SPEED") == 21
    vertical_read = next(
        command for command in plan.commands
        if command.kind == "READ_TELEMETRY" and dict(command.arguments).get("phase") == "vertical"
    )
    assert vertical_read.upper_bound_s == 7


@pytest.mark.parametrize("speed_cm_s", [True, 20.0, 20.9])
def test_checkpoint_rejects_boolean_and_fractional_speed_values(speed_cm_s):
    with pytest.raises(ValueError, match="speed_cm_s must be a positive integer"):
        plan_checkpoint(
            checkpoint_id="invalid-speed", start=Position(), target=Position(240, 0, 0),
            start_heading_deg=90, terminal_heading_deg=90, speed_cm_s=speed_cm_s,
            looks_deg=(), capture_count=0, limits=CommandLimits(),
        )


def test_checkpoint_uses_exact_integer_speed_for_a_240_cm_move_bound():
    plan = plan_checkpoint(
        checkpoint_id="long-leg", start=Position(), target=Position(240, 0, 0),
        start_heading_deg=90, terminal_heading_deg=90, speed_cm_s=20,
        looks_deg=(), capture_count=0, limits=CommandLimits(),
    )

    planned_speed = dict(next(command.arguments for command in plan.commands if command.kind == "SET_SPEED"))["speed_cm_s"]
    move = next(command for command in plan.commands if command.kind == "MOVE")
    assert type(planned_speed) is int
    assert move.upper_bound_s == 30
    assert move.upper_bound_s == move_reply_upper_bound_s(
        distance_cm=dict(move.arguments)["distance_cm"], speed_cm_s=planned_speed,
        base_timeout_s=CommandLimits().transport_limits.reply_timeout_s,
    )


@pytest.mark.parametrize("speed_cm_s", [True, 20.0, 20.9])
def test_command_limits_rejects_non_exact_trim_speeds(speed_cm_s):
    with pytest.raises(ValueError, match="trim_speed_cm_s must be a positive integer"):
        CommandLimits(trim_speed_cm_s=speed_cm_s)


def test_checkpoint_enforces_dead_band_settle_speed_and_canonical_terminal_heading():
    slow = plan_checkpoint(
        checkpoint_id="slow", start=Position(0, 0, 100), target=Position(10, 11, 100),
        start_heading_deg=90, terminal_heading_deg=90, speed_cm_s=20,
        looks_deg=(), capture_count=0, limits=CommandLimits(localise=False),
    )
    assert [dict(command.arguments)["axis"] for command in slow.commands if command.kind == "MOVE"] == ["y"]
    assert [command.upper_bound_s for command in slow.commands if command.kind == "SETTLE"] == [0.7]
    with pytest.raises(ValueError, match="canonical"):
        plan_checkpoint(
            checkpoint_id="bad-heading", start=Position(), target=Position(),
            start_heading_deg=90, terminal_heading_deg=45, speed_cm_s=20,
            looks_deg=(), capture_count=0, limits=CommandLimits(),
        )


def test_planned_command_copies_and_sorts_caller_arguments_for_stable_digests():
    source = {"distance_cm": 20, "axis": "x", "direction": "right"}
    command = PlannedCommand.from_mapping("MOVE", source, upper_bound_s=8.0, interruptible=False)
    before = hashlib.sha256(repr(command).encode()).hexdigest()
    source["distance_cm"] = 999
    source["new"] = "ignored"

    assert command.arguments == (("axis", "x"), ("direction", "right"), ("distance_cm", 20))
    assert hashlib.sha256(repr(command).encode()).hexdigest() == before
