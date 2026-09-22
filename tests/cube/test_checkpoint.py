from __future__ import annotations

import hashlib

import pytest

from sentinel.cube.checkpoint import CommandLimits, PlannedCommand, plan_checkpoint
from sentinel.models import Position


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
        "MOVE", "SETTLE", "MOVE", "SETTLE",
        "ROTATE", "SETTLE", "CAPTURE", "LOCALISE",
        "ROTATE", "SETTLE", "CAPTURE", "LOCALISE",
        "ROTATE", "TRIM_ALLOWANCE", "READ_TELEMETRY",
    ]
    moves = [command for command in plan.commands if command.kind == "MOVE"]
    assert [dict(command.arguments) for command in moves] == [
        {"axis": "x", "direction": "right", "distance_cm": 20, "optional": "true"},
        {"axis": "y", "direction": "back", "distance_cm": 20, "optional": "true"},
    ]
    assert sum(command.upper_bound_s for command in plan.commands) == plan.worst_case_s


def test_checkpoint_enforces_dead_band_settle_speed_and_canonical_terminal_heading():
    slow = plan_checkpoint(
        checkpoint_id="slow", start=Position(0, 0, 100), target=Position(10, 11, 100),
        start_heading_deg=90, terminal_heading_deg=90, speed_cm_s=20,
        looks_deg=(), capture_count=0, limits=CommandLimits(),
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
