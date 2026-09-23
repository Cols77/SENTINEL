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


def _policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


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
