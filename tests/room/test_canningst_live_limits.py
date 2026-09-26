"""The CanningSt live limits the operator declared on 2026-09-26: faster
translations and a 22 % battery floor.

Live 2026-09-26 (l3-20260926-111405, l3-20260926-114306): every checkpoint
opened with ``SET_SPEED 20`` (the slowest speed the flight profile
validated), so each zone's legs dominated the flight; and with the
predictive energy guard disabled the batteries drained to 9 % and 14 %, the
first ending on a motor-stop error mid-turn. The profile set now declares a
40 cm/s translation speed and a 22 % landing floor, both in the history
policy the committed profiles are derived from."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from sentinel.cube.mission import INSET_FREE_SPEED_CM_S
from sentinel.models import Position
from sentinel.motion_bounds import move_reply_upper_bound_s
from sentinel.room import calibration, live_session, wiring

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ROOT / "fixtures" / "room_agent" / "v1" / "profiles"


def _policy() -> dict:
    return json.loads((PROFILES / "canningst-policy.json").read_text(encoding="utf-8"))


def test_the_committed_limits_translate_at_40_cm_s_inside_the_validated_speeds():
    limits = live_session.load_command_limits(PROFILES / "canningst-command-limits.json")
    profile = calibration.load_profile(PROFILES / "canningst-flight-profile.json")
    assert limits.translation_speed_cm_s == 40
    # Inside the speeds the flight history actually flew (20 and 50), and no
    # faster than the legs the cube mission flies without a wider land inset.
    assert min(profile.speeds_cm_s) <= limits.translation_speed_cm_s <= max(profile.speeds_cm_s)
    assert limits.translation_speed_cm_s <= INSET_FREE_SPEED_CM_S
    assert limits.trim_rounds == 0 and limits.localise is False  # unchanged
    policy = _policy()
    assert policy["command_limits"]["translation_speed_cm_s"] == 40
    assert "translation_speed_cm_s" in policy["declared_notes"]


def test_the_committed_profiles_plan_every_move_at_40_cm_s_with_bounds_derived_from_it():
    binding = live_session.load_flight_binding(
        site_path=ROOT / "fixtures" / "room_agent" / "v1" / "canningst-site.json",
        drift_profile_path=PROFILES / "canningst-drift.json",
        energy_profile_path=PROFILES / "canningst-energy.json",
        command_limits_path=PROFILES / "canningst-command-limits.json",
        profile_paths=(PROFILES / "canningst-flight-profile.json",))
    # From the pad (250, 190) to the left front zone: an x and a y leg.
    pose = SimpleNamespace(position=Position(250, 190, 100), heading_deg=90.0)
    resolve = wiring.make_transit_checkpoint(site=binding.site, profile=binding.calibration_profiles[0],
                                             pose_source=lambda: pose, limits=binding.limits)
    plan = resolve("left-front-middle-toward_targets")
    speeds = [dict(c.arguments)["speed_cm_s"] for c in plan.commands if c.kind == "SET_SPEED"]
    assert speeds == [40]
    moves = [c for c in plan.commands if c.kind == "MOVE" and dict(c.arguments)["axis"] != "z"]
    assert moves, plan.commands
    # Each move's reply bound (and so the energy forecast, priced on the
    # plan's worst case) is the backend's half-speed bound at 40 cm/s.
    for move in moves:
        assert move.upper_bound_s == move_reply_upper_bound_s(
            distance_cm=dict(move.arguments)["distance_cm"], speed_cm_s=40,
            base_timeout_s=binding.limits.transport_limits.reply_timeout_s)
    assert plan.worst_case_s == sum(c.upper_bound_s for c in plan.commands)


def test_the_committed_energy_profile_lands_at_22_percent():
    energy = live_session.load_energy_profile(PROFILES / "canningst-energy.json")
    profile = calibration.load_profile(PROFILES / "canningst-flight-profile.json")
    assert energy.landing_floor_pct == 22
    assert profile.battery.landing_floor_percent == 22.0
    # The measured burn and its identity are unchanged: only the declared floor moved.
    assert energy.percent_per_second_upper == 0.287811
    assert energy.profile_id == "canningst-history-20260923-energy"
    policy = _policy()
    assert policy["landing_floor_percent"] == 22
    assert "landing_floor_percent" in policy["declared_notes"]
