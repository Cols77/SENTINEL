"""Synthetic calibrated envelopes: no fixture represents a live flight grant."""
from dataclasses import replace

import pytest

from sentinel.cube.checkpoint import CheckpointPlan, PlannedCommand
from sentinel.cube.localization import DriftProfile, LocalizationState, LocalizationTracker, PoseFix
from sentinel.models import Position


class Clock:
    def __init__(self, now=10.0):
        self.now = now

    def __call__(self):
        return self.now


def profile(**changes):
    return replace(DriftProfile(
        schema_version="pose-drift-v1", profile_id="synthetic", validated=True,
        evidence_refs=("synthetic-replay",), max_camera_age_s=5, max_fix_age_s=20,
        max_estimator_failures=3, max_look_away_s=10, max_translation_cm=100,
        max_yaw_deg=180, max_xy_uncertainty_cm=20, max_heading_uncertainty_deg=15,
        xy_drift_cm_s=0.1, xy_drift_per_cm=0.1, xy_drift_per_yaw_deg=0.01,
        heading_drift_deg_s=0.1, heading_drift_per_cm=0.01, heading_drift_per_yaw_deg=0.05,
        max_fix_age_on_arrival_s=1, max_fix_jump_cm=30, max_fix_heading_jump_deg=20,
        recovery_deadline_s=8, max_recovery_attempts=4, min_burst_reports=3,
        max_burst_spread_cm=10, max_burst_heading_spread_deg=8,
        max_height_error_cm=10, rotation_radius_cm=15,
        recovery_action_upper_bound_s=1, canonical_heading_tolerance_deg=2,
    ), **changes)


def fix(clock, **changes):
    return replace(PoseFix(Position(100, 100, 100), 90, clock.now, 1, 1, (1, 2)), **changes)


def tracker(**changes):
    clock = Clock()
    value = LocalizationTracker(profile(**changes), clock=clock)
    value.accept_fix(fix(clock))
    return value, clock


def command(kind="MOVE", seconds=1, **args):
    return PlannedCommand.from_mapping(kind, args, upper_bound_s=seconds, interruptible=False)


def plan(*commands):
    return CheckpointPlan("next", Position(100, 100, 100), Position(120, 100, 100),
                          90, 90, commands, sum(c.upper_bound_s for c in commands))


def test_one_missed_tag_coasts_and_camera_age_is_not_fix_age():
    value, clock = tracker()
    clock.now += 1
    health = value.note_camera(clock.now, estimator_failed=True)
    assert health.state == LocalizationState.COASTING
    assert health.camera_age_s == 0
    assert health.fix_age_s == 1
    assert health.estimator_failures == 1
    assert value.forecast(plan(command(direction="right", distance_cm=20)), clock.now).admitted


@pytest.mark.parametrize("changes,commands,reason", [
    ({"max_camera_age_s": 1}, (command("SETTLE"),), "camera_age"),
    ({"max_fix_age_s": 1}, (command("SETTLE"),), "fix_age"),
    ({"max_translation_cm": 20}, (command(direction="right", distance_cm=20),), "translation"),
    ({"max_yaw_deg": 30}, (command("ROTATE", degrees=30, direction="ccw"),), "yaw"),
    ({"max_xy_uncertainty_cm": 3}, (command(direction="right", distance_cm=20),), "xy_uncertainty"),
    ({"max_heading_uncertainty_deg": 2}, (command("ROTATE", degrees=30, direction="ccw"),), "heading_uncertainty"),
    ({"max_look_away_s": 1}, (command("ROTATE", degrees=30, direction="ccw", phase="look"),), "look_away"),
])
def test_forecast_recovery_before_any_calibrated_bound(changes, commands, reason):
    value, clock = tracker(**changes)
    forecast = value.forecast(plan(*commands), clock.now)
    assert not forecast.admitted
    assert reason in forecast.reasons
    assert value.health.state == LocalizationState.RECOVERY_REQUIRED
    assert value.health.translation_cm == 0  # forecast never executes the proposed movement


def test_commands_propagate_pose_heading_and_independent_history():
    value, clock = tracker()
    value.note_command(command(direction="right", distance_cm=20), completed_at=11)
    clock.now = 12
    health = value.note_command(command("ROTATE", degrees=30, direction="ccw", phase="look"), 12)
    assert health.position == Position(120, 100, 100)
    assert health.heading_deg == 120
    assert health.translation_cm == 20 and health.yaw_deg == 30
    assert health.look_away_s == 1
    assert health.xy_uncertainty_cm > 3
    assert health.heading_uncertainty_deg > 2


def test_repeated_estimator_failures_require_recovery_but_lookaway_is_not_failure():
    value, clock = tracker()
    for _ in range(5):
        value.note_camera(clock.now, intentional_look_away=True)
    assert value.health.estimator_failures == 0
    for _ in range(3):
        value.note_camera(clock.now, estimator_failed=True)
    assert value.health.state == LocalizationState.RECOVERY_REQUIRED


@pytest.mark.parametrize("changes", [{"position": Position(500, 100, 100)}, {"heading_deg": 170}])
def test_contradictory_fix_loses_integrity_and_cannot_be_cleared_by_ordinary_fix(changes):
    value, clock = tracker()
    assert value.accept_fix(fix(clock, **changes)).state == LocalizationState.LOST
    assert value.accept_fix(fix(clock)).state == LocalizationState.LOST
    for cmd in (command(direction="right", distance_cm=20), command("ROTATE", degrees=30, direction="cw")):
        assert not value.forecast(plan(cmd), clock.now).admitted
        with pytest.raises(ValueError, match="LOST"):
            value.note_command(cmd, clock.now)


@pytest.mark.parametrize("drift", [None, profile(validated=False), profile(evidence_refs=())])
def test_missing_validated_profile_disables_real_tag_free_execution(drift):
    clock = Clock()
    value = LocalizationTracker(drift, clock=clock, real_mode=True)
    value.accept_fix(fix(clock))
    result = value.forecast(plan(command(direction="right", distance_cm=20)), clock.now)
    assert not result.admitted
    assert "validated_drift_profile" in result.reasons


def test_forecast_accounts_for_trim_allowances_and_never_assumes_future_fix_succeeds():
    value, clock = tracker(max_translation_cm=100)
    result = value.forecast(plan(command("LOCALISE"), command(
        "TRIM_ALLOWANCE", max_distance_cm=30, max_rounds=2, max_yaw_deg=20)), clock.now)
    assert not result.admitted
    assert result.projected.translation_cm == 120  # two axes per trim round


def test_stale_future_nonfinite_and_clock_reset_fail_closed():
    value, clock = tracker()
    for changed in ({"measured_at": 8}, {"measured_at": 11}, {"heading_deg": float("nan")}):
        assert value.accept_fix(fix(clock, **changed)).state != LocalizationState.ANCHORED
    clock.now = 1
    assert value.forecast(plan(command("SETTLE")), clock.now).projected.state == LocalizationState.LOST


def test_drift_profile_rejects_unknown_version_and_invalid_limits():
    for changes in ({"schema_version": "future"}, {"max_translation_cm": 0},
                    {"xy_drift_cm_s": -1}, {"max_yaw_deg": float("nan")}):
        with pytest.raises(ValueError):
            profile(**changes)


@pytest.mark.parametrize("changes", [{"validated": "false"}, {"evidence_refs": ("",)}])
def test_profile_validation_cannot_be_enabled_by_truthy_untyped_values(changes):
    with pytest.raises(ValueError):
        profile(**changes)


def test_intentional_lookaway_elapsed_time_is_not_camera_age_or_estimator_failure():
    value, clock = tracker()
    value.note_camera(clock.now, intentional_look_away=True)
    clock.now += 2
    health = value.note_camera(clock.now, intentional_look_away=True, estimator_failed=True)
    assert health.look_away_s == 2
    assert health.camera_age_s == 0 and health.estimator_failures == 0


def test_ordinary_fix_must_have_joint_tag_provenance():
    value, clock = tracker()
    for changed in ({"tag_ids": (1,)}, {"source": "guessed"}):
        assert value.accept_fix(fix(clock, **changed)).state != LocalizationState.ANCHORED


def test_fresh_validation_blocks_resume_without_requesting_another_reanchor():
    value, clock = tracker()
    value.accept_fix(fix(clock))
    result = value.forecast(plan(command(direction="right", distance_cm=20)), clock.now)
    assert not result.admitted
    assert {"fresh_route_validation", "fresh_energy_validation"} <= set(result.reasons)
    assert value.health.state == LocalizationState.ANCHORED


def test_heading_uncertainty_propagates_into_translational_uncertainty():
    value, clock = tracker(xy_drift_cm_s=0, xy_drift_per_cm=0, heading_drift_per_cm=0,
                           max_translation_cm=200, max_xy_uncertainty_cm=50)
    value.accept_fix(fix(clock, heading_uncertainty_deg=10))
    health = value.note_command(command(direction="right", distance_cm=100), 10)
    assert health.xy_uncertainty_cm >= 18  # 1 cm fix error + ~17.43 cm heading error
