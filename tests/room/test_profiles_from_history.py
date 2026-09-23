"""Task B item 6: CALIBRATION-scope flight profiles from recorded CanningSt flights.

Offline: synthetic trial directories for the derivation rules and refusals;
the real WP5 history (read-only, when this machine has it) to prove the
committed profiles are exactly what the script derives from it.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

import scripts.room_profiles_from_history as history_cli
from sentinel.cube.energy import EnergyProfile, ExecutionMode, ReserveGuard
from sentinel.room import calibration, live_session
from tests.room.test_canningst_site import canningst_site

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "fixtures" / "room_agent" / "v1" / "profiles" / "canningst-policy.json"
PROFILES_DIR = ROOT / "fixtures" / "room_agent" / "v1" / "profiles"
REAL_TRIALS = Path("C:/coding/sentinel/.worktrees/integration/artifacts/trials")


def _write_trial(root: Path, name: str, rows: list[dict], *, mode="real", speeds=(20, 50)):
    trial = root / name
    trial.mkdir(parents=True)
    commands = [{"command": f"speed {speed}", "response": "ok"} for speed in speeds]
    (trial / "run.jsonl").write_text(json.dumps({"mode": mode, "status": "PASS", "commands": commands}) + "\n",
                                     encoding="utf-8")
    (trial / "mission.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _row(waypoint, *, commanded=None, fix=None, error=None, battery=None, at=None, heading=0.0, height=None,
         status="LOCALISED"):
    return {"waypoint": waypoint, "commanded_cm": commanded, "fix_cm": fix, "open_loop_error_cm": error,
            "battery_pct": battery, "heading_error_deg": heading, "status": status, "height_cm": height,
            "frames": [{"captured_at": at}] if at else []}


def _synthetic(tmp_path) -> Path:
    root = tmp_path / "trials"
    _write_trial(root, "t1", [
        _row("START", fix=[250, 190], battery=90, heading=0.0),
        _row("A", commanded=[250, 290, 100], fix=[252, 285], error=10.0, battery=88,
             at="2026-09-22T00:00:10+00:00", heading=1.0, height=104),
        _row("B", commanded=[250, 190, 100], fix=[250, 195], error=5.0, battery=84,
             at="2026-09-22T00:00:30+00:00", heading=0.5, height=100),
    ])
    _write_trial(root, "t2", [
        _row("START", fix=[250, 190], battery=70),
        _row("A", commanded=[300, 190, 100], fix=[310, 190], error=20.0, battery=66,
             at="2026-09-22T01:00:00+00:00", heading=3.0, height=94),
        _row("B", commanded=[250, 190, 100], error=None, battery=60, status="UNLOCALISED",
             at="2026-09-22T01:00:10+00:00"),
    ])
    _write_trial(root, "dry", [_row("START", battery=100)], mode="dry-run")
    return root


B2_POLICY_KEYS = ("excluded_runs", "declared_overrides", "robust_drift", "battery_percentile", "min_battery_window_s")


def _policy() -> dict:
    """The committed policy without its CanningSt-history-specific Task B2
    choices (run exclusions, the declared robust drift, the percentile and
    window rules), so the base derivation rules are tested on their own."""
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    return {key: value for key, value in policy.items() if key not in B2_POLICY_KEYS}


def test_profiles_use_the_worst_observed_burn_drift_and_altitude_plus_the_declared_margins(tmp_path):
    history = history_cli.read_history(_synthetic(tmp_path))
    assert history.trials == ("t1", "t2")  # the dry run is not flight evidence
    assert history.speeds_cm_s == (20, 50)
    profiles = history_cli.build_profiles(history, site=canningst_site(), policy=_policy())
    # Battery: t1 88 -> 84 over 20 s, t2 66 -> 60 over 10 s; the 1 % quantization is folded in.
    worst = max((4 + 1.0) / 20, (6 + 1.0) / 10)
    assert profiles.measurements["worst_battery_percent_per_s"] == pytest.approx(worst)
    assert profiles.energy_profile.percent_per_second_upper == pytest.approx(worst + 0.02)
    # Drift: 10 cm over 100 cm, 5 over 90, 20 over 50 -> worst 0.4 cm per cm.
    assert profiles.drift_profile.xy_drift_per_cm == pytest.approx(0.4)
    assert profiles.drift_profile.max_height_error_cm == 6 + 10  # worst altimeter error + declared margin
    assert profiles.drift_profile.validated and "wp5-trial:t1" in profiles.drift_profile.evidence_refs
    # Legal only in a TRIAL acceptance run.
    assert profiles.calibration_profile.state == "CALIBRATION"
    assert profiles.energy_profile.execution_scope == "CALIBRATION"
    assert calibration.admissible_for(profiles.calibration_profile, grant_state="TRIAL")
    assert not calibration.admissible_for(profiles.calibration_profile, grant_state="PROMOTED")


def test_written_profiles_load_through_the_live_session_loaders(tmp_path):
    profiles = history_cli.build_profiles(history_cli.read_history(_synthetic(tmp_path)),
                                          site=canningst_site(), policy=_policy())
    paths = history_cli.write_profiles(profiles, tmp_path / "out")
    assert live_session.load_drift_profile(paths["drift"]) == profiles.drift_profile
    assert live_session.load_energy_profile(paths["energy"]) == profiles.energy_profile
    assert live_session.load_command_limits(paths["limits"]).trim_rounds == 0
    assert calibration.load_profile(paths["profile"]) == profiles.calibration_profile


@pytest.mark.parametrize("mutate, missing", [
    (lambda rows: [dict(row, frames=[]) for row in rows], "battery burn"),
    (lambda rows: [dict(row, status="UNLOCALISED") for row in rows], "pose drift"),
    (lambda rows: [dict(row, height_cm=None) for row in rows], "altitude error"),
])
def test_missing_quantities_refuse_by_name(tmp_path, mutate, missing):
    root = tmp_path / "trials"
    rows = [_row("START", fix=[250, 190], battery=90),
            _row("A", commanded=[250, 290, 100], fix=[252, 285], error=10.0, battery=88,
                 at="2026-09-22T00:00:10+00:00", heading=1.0, height=104),
            _row("B", commanded=[250, 190, 100], fix=[250, 195], error=5.0, battery=84,
                 at="2026-09-22T00:00:30+00:00", heading=0.5, height=100)]
    _write_trial(root, "t1", mutate(rows))
    with pytest.raises(history_cli.HistoryError, match=missing):
        history_cli.build_profiles(history_cli.read_history(root), site=canningst_site(), policy=_policy())


def test_a_policy_missing_a_declaration_or_declaring_a_measured_value_refuses(tmp_path):
    policy = _policy()
    del policy["drift_limits"]["xy_drift_cm_s"]
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(history_cli.HistoryError, match="xy_drift_cm_s"):
        history_cli.load_policy(path)
    policy = _policy()
    policy["drift_limits"]["xy_drift_per_cm"] = 0.01
    path.write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(history_cli.HistoryError, match="measured"):
        history_cli.load_policy(path)


def test_a_declared_override_is_named_in_the_evidence_never_silent(tmp_path):
    history = history_cli.read_history(_synthetic(tmp_path))
    policy = {**_policy(), "declared_overrides": {
        "xy_drift_per_cm": {"value": 0.1, "justification": "operator decision for the demo"}}}
    profiles = history_cli.build_profiles(history, site=canningst_site(), policy=policy)
    assert profiles.drift_profile.xy_drift_per_cm == 0.1
    assert "operator-declared:xy_drift_per_cm" in profiles.drift_profile.evidence_refs
    assert profiles.measurements["worst_xy_drift_per_cm"] == pytest.approx(0.4)
    with pytest.raises(history_cli.HistoryError, match="justification"):
        history_cli.build_profiles(history, site=canningst_site(),
                                   policy={**_policy(), "declared_overrides": {"xy_drift_per_cm": {"value": 0.1}}})


def test_the_script_refuses_without_output_when_the_history_is_empty(tmp_path, capsys):
    (tmp_path / "trials").mkdir()
    exit_code = history_cli.main(["--trials-root", str(tmp_path / "trials"), "--site",
                                  str(ROOT / "fixtures/room_agent/v1/canningst-site.json"),
                                  "--policy", str(POLICY_PATH), "--out-dir", str(tmp_path / "out")])
    assert exit_code == 2 and "real recorded flight" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


@pytest.mark.skipif(not REAL_TRIALS.is_dir(), reason="the recorded WP5 history is not on this machine")
def test_the_committed_profiles_are_exactly_what_the_real_history_derives(tmp_path):
    exit_code = history_cli.main(["--trials-root", str(REAL_TRIALS),
                                  "--site", str(ROOT / "fixtures/room_agent/v1/canningst-site.json"),
                                  "--policy", str(POLICY_PATH), "--out-dir", str(tmp_path)])
    assert exit_code == 0
    for name in ("canningst-flight-profile.json", "canningst-energy.json", "canningst-drift.json",
                 "canningst-command-limits.json"):
        derived = (tmp_path / name).read_bytes().replace(b"\r\n", b"\n")
        committed = (PROFILES_DIR / name).read_bytes().replace(b"\r\n", b"\n")
        assert derived == committed, name


# ---------------------------------------------------------------------------
# Task B2: robust drift, run exclusions, whole-flight battery windows
# ---------------------------------------------------------------------------


def _timed_run(root: Path, name: str, rows: list[dict], *, takeoff_at: str, first_at: str, land_at: str):
    trial = root / name
    trial.mkdir(parents=True)
    commands = [{"command": "speed 20", "response": "ok", "timestamp": takeoff_at},
                {"command": "takeoff", "response": "ok", "timestamp": takeoff_at},
                {"command": "up 20", "response": "ok", "timestamp": first_at},
                {"command": "right 100", "response": "ok", "timestamp": first_at},
                {"command": "land", "response": "ok", "timestamp": land_at}]
    (trial / "run.jsonl").write_text(json.dumps({"mode": "real", "status": "PASS", "commands": commands}) + "\n",
                                     encoding="utf-8")
    (trial / "mission.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_drift_is_per_centimetre_of_path_since_the_last_fix_vertical_included(tmp_path):
    root = tmp_path / "trials"
    _write_trial(root, "t1", [
        _row("START", fix=[200, 200], battery=90, height=100),
        _row("A", commanded=[300, 200, 100], error=None, status="UNLOCALISED", battery=88,
             at="2026-09-22T00:00:10+00:00", height=100),
        _row("B", commanded=[300, 250, 140], fix=[301, 251], error=19.0, battery=86,
             at="2026-09-22T00:00:30+00:00", height=140),
    ])
    leg, = history_cli.read_history(root).legs
    # 100 cm to the unlocalised A, then 50 cm forward and 40 cm up to B.
    assert leg.path_cm == pytest.approx(190.0)
    assert leg.xy_per_cm == pytest.approx(0.1)


def test_excluded_runs_contribute_nothing_and_are_named_in_the_evidence(tmp_path):
    history = history_cli.read_history(_synthetic(tmp_path))
    policy = {**_policy(), "excluded_runs": {"t2": "flown below the battery floor"},
              "robust_drift": {"percentile": 90, "margin_fraction": 0.2}}
    profiles = history_cli.build_profiles(history, site=canningst_site(), policy=policy)
    assert "excluded-run:t2" in profiles.drift_profile.evidence_refs
    assert "wp5-trial:t2" not in profiles.drift_profile.evidence_refs
    assert profiles.calibration_profile.training_run_ids == ("t1",)
    assert {"run": "t2", "what": "run", "reason": "flown below the battery floor"} in profiles.measurements["excluded"]
    robust = profiles.measurements["robust_drift"]
    ratios = [leg.xy_per_cm for leg in history.legs if leg.trial == "t1"]
    assert robust["xy_drift_per_cm_percentile"] == pytest.approx(history_cli.percentile(ratios, 90))
    assert robust["xy_drift_per_cm"] == pytest.approx(robust["xy_drift_per_cm_percentile"] * 1.2)
    for bad in ({"t2": " "}, {"nope": "reason"}):
        with pytest.raises(history_cli.HistoryError, match="excluded run|not in the history"):
            history_cli.build_profiles(history, site=canningst_site(), policy={**_policy(), "excluded_runs": bad})


def test_battery_is_the_whole_flight_percentile_and_short_windows_are_dropped_by_name(tmp_path):
    root = tmp_path / "trials"
    rows = [_row("START", fix=[250, 190], battery=90, height=100, at="2026-09-22T00:00:06+00:00"),
            _row("A", commanded=[350, 190, 100], fix=[352, 190], error=4.0, battery=84,
                 at="2026-09-22T00:00:30+00:00", height=100)]
    _timed_run(root, "long", rows, takeoff_at="2026-09-22T00:00:00+00:00",
               first_at="2026-09-22T00:00:05+00:00", land_at="2026-09-22T00:01:05+00:00")
    _timed_run(root, "short", rows, takeoff_at="2026-09-22T01:00:00+00:00", first_at="2026-09-22T01:00:05+00:00",
               land_at="2026-09-22T01:00:15+00:00")
    history = history_cli.read_history(root)
    assert {w.trial: (w.source, w.elapsed_s) for w in history.battery} == {"long": ("flight", 60.0),
                                                                            "short": ("flight", 10.0)}
    policy = {**_policy(), "battery_percentile": 90, "min_battery_window_s": 30}
    profiles = history_cli.build_profiles(history, site=canningst_site(), policy=policy)
    # 90 -> 84 over the 60 s flight, plus the 1 % reading step; the 10 s flight is quantization.
    assert profiles.measurements["worst_battery_percent_per_s"] == pytest.approx((6 + 1.0) / 60)
    assert any(entry["run"] == "short" and "min_battery_window_s" in entry["reason"]
               for entry in profiles.measurements["excluded"])


@pytest.mark.skipif(not REAL_TRIALS.is_dir(), reason="the recorded WP5 history is not on this machine")
def test_the_real_history_outlier_is_the_excluded_runs_first_leg_and_the_nominal_p90_is_about_013():
    history = history_cli.read_history(REAL_TRIALS)
    worst = max(history.legs, key=lambda leg: leg.drift_cm / leg.distance_cm)
    assert (worst.trial, worst.waypoint) == ("20260923-122157-reanchor", "FAR_L_HI")
    assert worst.drift_cm / worst.distance_cm == pytest.approx(0.45, abs=0.005)  # the old straight-line ratio
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    profiles = history_cli.build_profiles(history, site=canningst_site(), policy=policy)
    robust = profiles.measurements["robust_drift"]
    assert robust["xy_drift_per_cm_percentile"] == pytest.approx(0.1312, abs=0.001)
    assert profiles.drift_profile.xy_drift_per_cm == pytest.approx(robust["xy_drift_per_cm"], abs=1e-6)
    assert profiles.energy_profile.percent_per_second_upper < 0.3


# ---------------------------------------------------------------------------
# the supervisor admits a CALIBRATION energy profile only in a TRIAL acceptance run
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("grant_state, acceptance, admitted", [
    ("TRIAL", "ACCEPTANCE", True),
    ("PROMOTED", None, False),
])
def test_calibration_energy_is_legal_only_under_a_trial_acceptance_grant(safety, grant_state, acceptance, admitted):
    from tests.room.test_count_return import bind_real_mode, room_site
    supervisor, store, clock, *_ = safety
    calibration_energy = EnergyProfile("energy", room_site(coverage=True).cube_site_sha256, "aircraft",
                                       .01, 1, 20, 2, "CALIBRATION")
    grant = bind_real_mode(safety, grant_state=grant_state, acceptance=acceptance, validate=False,
                           energy=calibration_energy)
    if admitted:
        supervisor.validate_bindings(supervisor.site)
        assert supervisor.admitted_grant == grant
    else:
        with pytest.raises(ValueError):
            supervisor.validate_bindings(supervisor.site)
        assert supervisor.admitted_grant is None


from tests.room.test_supervisor import safety  # noqa: E402,F401 - pytest fixture
