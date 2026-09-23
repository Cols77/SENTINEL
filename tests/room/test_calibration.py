"""Tests for src/sentinel/room/calibration.py and scripts/room_calibrate.py
(Plan 06 Task 3, count-and-return scope; fix round 1 for the review findings).

Everything here is OFFLINE: no provider, network, drone client or hardware object is
constructed anywhere in this file. ``record`` is always driven through a FakeExecutor;
tests assert that omitting an injected executor factory fails loudly instead of
reaching for a real one.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from sentinel.room import calibration
from sentinel.models import Position, Telemetry

ROUTES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "room_agent" / "v1" / "calibration"
RETURN_ROUTE = ROUTES_DIR / "return-route.json"
TAG_FREE_ROUTE = ROUTES_DIR / "tag-free-hover.json"

_HARDWARE_AIRCRAFT_ID = "calibration-aircraft-1"
_HARDWARE_FIRMWARE_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Fakes: never a real Tello/provider client.
# ---------------------------------------------------------------------------


class FakeExecutor:
    """Duck-types StepExecutor's public surface (start/execute/return_home/reanchor/
    land/close). ``return_home``/``reanchor`` delegate to the same leg bookkeeping as
    ``execute`` so ``self.executed`` always lists every flown checkpoint, while
    ``self.return_home_calls``/``self.reanchor_calls`` record which path was actually
    used for a given leg."""

    def __init__(
        self, *, telemetry_sequence, poses=None, propagated_poses=None,
        terminal_state="LANDED", start_battery=None, landing_battery=None,
    ):
        self.telemetry_sequence = list(telemetry_sequence)
        self.poses = dict(poses or {})
        self.propagated_poses = dict(propagated_poses or {})
        self.terminal_state = terminal_state
        self.start_battery = start_battery
        self.landing_battery = landing_battery
        self.hardware_provenance = None
        self.started_with = None
        self.executed = []
        self.return_home_calls = []
        self.reanchor_calls = []
        self.landed = False
        self.closed = False

    def start(self, home_definition):
        self.started_with = home_definition
        if self.start_battery is None:
            return None
        return Telemetry(battery_percent=self.start_battery, height_cm=home_definition.nominal_pose.z_cm)

    def execute(self, plan):
        self.executed.append(plan.checkpoint_id)
        telemetry = self.telemetry_sequence.pop(0)
        pose = self.poses.get(plan.checkpoint_id, plan.target)
        return SimpleNamespace(status="COMPLETED", telemetry=telemetry, pose=pose)

    def return_home(self, plan):
        self.return_home_calls.append(plan.checkpoint_id)
        return self.execute(plan)

    def reanchor(self, plan):
        self.reanchor_calls.append(plan.checkpoint_id)
        outcome = self.execute(plan)
        propagated = self.propagated_poses.get(plan.checkpoint_id, outcome.pose)
        return SimpleNamespace(
            status=outcome.status, telemetry=outcome.telemetry, pose=outcome.pose,
            propagated_pose=propagated,
        )

    def land(self):
        self.landed = True
        return SimpleNamespace(state=self.terminal_state, battery=self.landing_battery)

    def close(self):
        self.closed = True


class FailingExecutor(FakeExecutor):
    """The second leg reports a non-COMPLETED outcome."""

    def execute(self, plan):
        if len(self.executed) == 1:
            self.executed.append(plan.checkpoint_id)
            return SimpleNamespace(status="DEADLINE_EXCEEDED", telemetry=None, pose=None)
        return super().execute(plan)


def _forbidden_executor_factory():
    raise AssertionError("record must not construct an executor when it refuses before flight")


def _fake_clock(times):
    values = list(times)

    def clock():
        if not values:
            raise AssertionError("fake clock exhausted")
        return values.pop(0)

    return clock


def _hardware(executor, *, aircraft_id=_HARDWARE_AIRCRAFT_ID, firmware_version=_HARDWARE_FIRMWARE_VERSION):
    """Declare *executor* as bound to real, controller-signed hardware provenance."""
    executor.hardware_provenance = calibration.HardwareProvenance(
        aircraft_id=aircraft_id, firmware_version=firmware_version, controller_signoff="ops-controller-1",
    )
    return executor


RETURN_ROUTE_CLOCK = (0.0, 0.0, 10.0, 10.0, 25.0, 25.0)  # leg1 10s, leg2 15s, total 25s
TAG_FREE_CLOCK = (0.0, 0.0, 5.0, 5.0)  # one leg, 5s

# Holdout evidence must be genuinely independent flight data, not a replay of the
# training clock/timings (see the content-hash reuse check in `promote`): these give
# holdout legs a different wall-clock timing than training's, while still landing in
# the same pose-envelope bucket (Q2) and comfortably inside the battery bound.
_HOLDOUT_RETURN_ROUTE_CLOCK = (0.0, 0.0, 9.0, 9.0, 24.0, 24.0)  # leg1 9s, leg2 15s, total 24s
_HOLDOUT_TAG_FREE_CLOCK = (0.0, 0.0, 5.1, 5.1)  # 5.1s -- same 5.0s bucket as training's 5.0s


def _return_executor(before=80, after=74):
    return FakeExecutor(
        telemetry_sequence=[
            Telemetry(battery_percent=before, height_cm=80),
            Telemetry(battery_percent=after, height_cm=80),
        ],
        start_battery=before, landing_battery=after,
    )


def _tag_free_executor(*, drift_cm=5.0, battery=90):
    # A 3-4-5 triangle keeps the drift an exact, round number. `poses` is the
    # anchor-corrected ground truth reported by `reanchor`; `propagated_poses` is the
    # estimator's own (drifted) belief at that same moment.
    dx, dy = (3, 4) if drift_cm == 5.0 else (round(drift_cm), 0)
    anchored = Position(x_cm=0, y_cm=0, z_cm=100)
    propagated = Position(x_cm=dx, y_cm=dy, z_cm=100)
    return FakeExecutor(
        telemetry_sequence=[Telemetry(battery_percent=battery, height_cm=100)],
        poses={"hover-yaw-sweep": anchored},
        propagated_poses={"hover-yaw-sweep": propagated},
    )


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------


def test_preview_binds_role_and_split_id_into_the_digest():
    raw = calibration.preview(target_mode="record", trial_role="training", split_id="split-1")
    document = json.loads(raw)
    assert document["target_mode"] == "record"
    assert document["trial_role"] == "training"
    assert document["split_id"] == "split-1"
    assert document["authorization_digest"] == calibration.authorization_digest(
        {k: v for k, v in document.items() if k != "authorization_digest"}
    )


def test_preview_is_deterministic():
    first = calibration.preview(target_mode="record", trial_role="holdout", split_id="s")
    second = calibration.preview(target_mode="record", trial_role="holdout", split_id="s")
    assert first == second


def test_preview_rejects_an_unsupported_target_mode():
    with pytest.raises(calibration.CalibrationError):
        calibration.preview(target_mode="live", trial_role="training", split_id="s")


def test_preview_rejects_an_unknown_trial_role():
    with pytest.raises(calibration.CalibrationError):
        calibration.preview(target_mode="record", trial_role="bogus", split_id="s")


def test_preview_rejects_an_empty_split_id():
    with pytest.raises(calibration.CalibrationError):
        calibration.preview(target_mode="record", trial_role="training", split_id="")


def test_preview_touches_no_filesystem(tmp_path, monkeypatch):
    def _forbid(*args, **kwargs):
        raise AssertionError("preview must not touch the filesystem")

    monkeypatch.setattr(Path, "write_bytes", _forbid)
    monkeypatch.setattr(Path, "mkdir", _forbid)
    calibration.preview(target_mode="record", trial_role="training", split_id="s")
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# parse_record_manifest: fail closed
# ---------------------------------------------------------------------------


def test_parse_record_manifest_round_trips():
    raw = calibration.preview(target_mode="record", trial_role="training", split_id="s")
    manifest = calibration.parse_record_manifest(raw)
    assert manifest.trial_role == "training"
    assert manifest.split_id == "s"


def test_parse_record_manifest_rejects_digest_mismatch():
    raw = calibration.preview(target_mode="record", trial_role="training", split_id="s")
    document = json.loads(raw)
    document["authorization_digest"] = "0" * 64
    with pytest.raises(calibration.CalibrationError):
        calibration.parse_record_manifest(json.dumps(document).encode("utf-8"))


def test_parse_record_manifest_rejects_unknown_field():
    raw = calibration.preview(target_mode="record", trial_role="training", split_id="s")
    document = json.loads(raw)
    document["extra"] = "nope"
    with pytest.raises(calibration.CalibrationError):
        calibration.parse_record_manifest(json.dumps(document).encode("utf-8"))


def test_parse_record_manifest_rejects_missing_field():
    raw = calibration.preview(target_mode="record", trial_role="training", split_id="s")
    document = json.loads(raw)
    del document["split_id"]
    with pytest.raises(calibration.CalibrationError):
        calibration.parse_record_manifest(json.dumps(document).encode("utf-8"))


def test_parse_record_manifest_rejects_wrong_schema_version():
    raw = calibration.preview(target_mode="record", trial_role="training", split_id="s")
    document = json.loads(raw)
    document["schema_version"] = "room-calibration-record-manifest-v0"
    document["authorization_digest"] = calibration.authorization_digest(
        {k: v for k, v in document.items() if k != "authorization_digest"}
    )
    with pytest.raises(calibration.CalibrationError):
        calibration.parse_record_manifest(json.dumps(document).encode("utf-8"))


# ---------------------------------------------------------------------------
# route fixtures parse
# ---------------------------------------------------------------------------


def test_return_route_fixture_parses():
    route = calibration.load_route(RETURN_ROUTE)
    assert route.route_id == "return-route"
    assert route.route_kind == "forced_return"
    assert len(route.checkpoints) == 2
    assert route.checkpoints[1].is_return_leg is True
    assert route.checkpoints[0].is_return_leg is False


def test_tag_free_hover_fixture_parses():
    route = calibration.load_route(TAG_FREE_ROUTE)
    assert route.route_id == "tag-free-hover"
    assert route.route_kind == "tag_free"
    assert route.checkpoints[0].looks_deg == (45, -45, 90)
    assert route.checkpoints[0].reanchor is True


def test_parse_route_rejects_an_unrecognised_route_id(tmp_path):
    document = json.loads(RETURN_ROUTE.read_bytes())
    document["route_id"] = "operator-freehand-route"
    document["home"]["route_id"] = "operator-freehand-route"
    path = tmp_path / "route.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(calibration.CalibrationError):
        calibration.load_route(path)


def test_parse_route_rejects_a_route_kind_that_does_not_match_its_route_id(tmp_path):
    document = json.loads(RETURN_ROUTE.read_bytes())
    document["route_kind"] = "tag_free"
    path = tmp_path / "route.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(calibration.CalibrationError):
        calibration.load_route(path)


def test_parse_route_rejects_a_forced_return_route_without_a_marked_return_leg(tmp_path):
    document = json.loads(RETURN_ROUTE.read_bytes())
    for checkpoint in document["checkpoints"]:
        checkpoint.pop("is_return_leg", None)
    path = tmp_path / "route.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(calibration.CalibrationError):
        calibration.load_route(path)


def test_parse_route_rejects_a_tag_free_route_without_a_marked_reanchor_leg(tmp_path):
    document = json.loads(TAG_FREE_ROUTE.read_bytes())
    for checkpoint in document["checkpoints"]:
        checkpoint.pop("reanchor", None)
    path = tmp_path / "route.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(calibration.CalibrationError):
        calibration.load_route(path)


# ---------------------------------------------------------------------------
# record: happy path, fixed-route replay, and every refusal
# ---------------------------------------------------------------------------


def _write_manifest(tmp_path, *, trial_role, split_id, name="manifest.json"):
    raw = calibration.preview(target_mode="record", trial_role=trial_role, split_id=split_id)
    path = tmp_path / name
    path.write_bytes(raw)
    digest = json.loads(raw)["authorization_digest"]
    return path, digest


def test_record_flies_the_forced_return_route_and_writes_a_trial(tmp_path):
    manifest_path, digest = _write_manifest(tmp_path, trial_role="training", split_id="battery-1")
    executor = _hardware(_return_executor())
    trial = calibration.record(
        manifest_path=manifest_path, authorization_digest=digest, route_path=RETURN_ROUTE,
        executor_factory=lambda: executor, clock=_fake_clock(RETURN_ROUTE_CLOCK),
    )
    assert executor.started_with is not None
    assert executor.executed == ["outbound", "forced-return"]
    assert executor.return_home_calls == ["forced-return"]
    assert executor.landed and executor.closed
    assert trial.trial_role == "training"
    assert trial.split_id == "battery-1"
    assert trial.route_kind == "forced_return"
    assert trial.terminal_state == "LANDED"
    assert trial.total_elapsed_s == 25.0
    assert trial.battery_before_percent == 80
    assert trial.battery_after_percent == 74
    assert trial.provenance == "HARDWARE"
    assert [sample.battery_percent for sample in trial.samples] == [80, 74]
    trial_path = tmp_path / "manifest.trial.json"
    assert trial_path.exists()
    assert calibration.load_trial(trial_path) == trial


def test_record_flies_the_tag_free_hover_route(tmp_path):
    manifest_path, digest = _write_manifest(tmp_path, trial_role="training", split_id="pose-1")
    trial = calibration.record(
        manifest_path=manifest_path, authorization_digest=digest, route_path=TAG_FREE_ROUTE,
        executor_factory=lambda: _tag_free_executor(), clock=_fake_clock(TAG_FREE_CLOCK),
    )
    assert trial.route_kind == "tag_free"
    assert trial.provenance == "SIMULATION"  # no hardware_provenance declared
    sample = trial.samples[0]
    assert sample.distance_cm == 0.0
    assert sample.elapsed_s == 5.0
    assert sample.yaw_deg == 90.0
    assert sample.drift_cm == pytest.approx(5.0)


def test_record_declares_hardware_provenance_only_when_bound_to_the_flown_route(tmp_path):
    manifest_path, digest = _write_manifest(tmp_path, trial_role="training", split_id="s")
    mismatched = _return_executor()
    mismatched.hardware_provenance = calibration.HardwareProvenance(
        aircraft_id="some-other-aircraft", firmware_version=_HARDWARE_FIRMWARE_VERSION,
        controller_signoff="ops-controller-1",
    )
    trial = calibration.record(
        manifest_path=manifest_path, authorization_digest=digest, route_path=RETURN_ROUTE,
        executor_factory=lambda: mismatched, clock=_fake_clock(RETURN_ROUTE_CLOCK),
    )
    assert trial.provenance == "SIMULATION"


def test_record_refuses_a_manifest_digest_that_does_not_match_authorization_digest(tmp_path):
    manifest_path, _ = _write_manifest(tmp_path, trial_role="training", split_id="s")
    with pytest.raises(calibration.CalibrationError):
        calibration.record(
            manifest_path=manifest_path, authorization_digest="0" * 64, route_path=RETURN_ROUTE,
            executor_factory=_forbidden_executor_factory,
        )
    assert not (tmp_path / "manifest.trial.json").exists()


def test_record_refuses_an_unrecognised_route_before_touching_the_executor(tmp_path):
    manifest_path, digest = _write_manifest(tmp_path, trial_role="training", split_id="s")
    document = json.loads(RETURN_ROUTE.read_bytes())
    document["route_id"] = "operator-freehand-route"
    document["home"]["route_id"] = "operator-freehand-route"
    route_path = tmp_path / "route.json"
    route_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(calibration.CalibrationError):
        calibration.record(
            manifest_path=manifest_path, authorization_digest=digest, route_path=route_path,
            executor_factory=_forbidden_executor_factory,
        )


def test_record_refuses_to_overwrite_an_existing_trial(tmp_path):
    manifest_path, digest = _write_manifest(tmp_path, trial_role="training", split_id="s")
    calibration.record(
        manifest_path=manifest_path, authorization_digest=digest, route_path=RETURN_ROUTE,
        executor_factory=_return_executor, clock=_fake_clock(RETURN_ROUTE_CLOCK),
    )
    with pytest.raises(calibration.CalibrationError, match="already exists"):
        calibration.record(
            manifest_path=manifest_path, authorization_digest=digest, route_path=RETURN_ROUTE,
            executor_factory=_forbidden_executor_factory, clock=_fake_clock(RETURN_ROUTE_CLOCK),
        )


def test_record_refuses_when_a_leg_does_not_complete_cleanly(tmp_path):
    manifest_path, digest = _write_manifest(tmp_path, trial_role="training", split_id="s")
    executor = FailingExecutor(telemetry_sequence=[Telemetry(battery_percent=80, height_cm=80)])
    with pytest.raises(calibration.CalibrationError):
        calibration.record(
            manifest_path=manifest_path, authorization_digest=digest, route_path=RETURN_ROUTE,
            executor_factory=lambda: executor, clock=_fake_clock(RETURN_ROUTE_CLOCK),
        )
    assert executor.landed  # explicit best-effort land on failure, not just close()
    assert executor.closed
    assert not (tmp_path / "manifest.trial.json").exists()


def test_record_lands_and_closes_when_start_itself_raises(tmp_path):
    class _StartFails(FakeExecutor):
        def start(self, home_definition):
            raise RuntimeError("boom")

    manifest_path, digest = _write_manifest(tmp_path, trial_role="training", split_id="s")
    executor = _StartFails(telemetry_sequence=[])
    with pytest.raises(RuntimeError, match="boom"):
        calibration.record(
            manifest_path=manifest_path, authorization_digest=digest, route_path=RETURN_ROUTE,
            executor_factory=lambda: executor, clock=_fake_clock(RETURN_ROUTE_CLOCK),
        )
    assert executor.closed
    assert not (tmp_path / "manifest.trial.json").exists()


def test_record_refuses_when_the_route_does_not_land_cleanly(tmp_path):
    manifest_path, digest = _write_manifest(tmp_path, trial_role="training", split_id="s")
    executor = _return_executor()
    executor.terminal_state = "LANDING_FAILED"
    with pytest.raises(calibration.CalibrationError):
        calibration.record(
            manifest_path=manifest_path, authorization_digest=digest, route_path=RETURN_ROUTE,
            executor_factory=lambda: executor, clock=_fake_clock(RETURN_ROUTE_CLOCK),
        )
    assert not (tmp_path / "manifest.trial.json").exists()


def test_record_requires_an_injected_executor_factory():
    with pytest.raises(TypeError):
        calibration.record(  # type: ignore[call-arg]
            manifest_path="manifest.json", authorization_digest="0" * 64, route_path=RETURN_ROUTE,
        )


# ---------------------------------------------------------------------------
# fit: only predeclared training evidence, CALIBRATION state only
# ---------------------------------------------------------------------------


def _write_evidence_index(tmp_path, entries, *, name):
    index_path = tmp_path / name
    index_path.write_text(json.dumps({
        "schema_version": "room-calibration-evidence-index-v1",
        "entries": entries,
    }), encoding="utf-8")
    return index_path


def _entries_of(index_path):
    return json.loads(Path(index_path).read_text(encoding="utf-8"))["entries"]


def _merged_index(tmp_path, *parts, name="combined-index.json"):
    entries = []
    for part in parts:
        entries.extend(_entries_of(part))
    return _write_evidence_index(tmp_path, entries, name=name)


def _record_pair(tmp_path, *, hardware=True):
    """One training forced-return trial + one training tag-free trial, evidence-indexed."""
    wrap = _hardware if hardware else (lambda executor: executor)
    battery_manifest, battery_digest = _write_manifest(
        tmp_path, trial_role="training", split_id="battery-1", name="battery.json"
    )
    calibration.record(
        manifest_path=battery_manifest, authorization_digest=battery_digest, route_path=RETURN_ROUTE,
        executor_factory=lambda: wrap(_return_executor()), clock=_fake_clock(RETURN_ROUTE_CLOCK),
    )
    pose_manifest, pose_digest = _write_manifest(
        tmp_path, trial_role="training", split_id="pose-1", name="pose.json"
    )
    calibration.record(
        manifest_path=pose_manifest, authorization_digest=pose_digest, route_path=TAG_FREE_ROUTE,
        executor_factory=lambda: wrap(_tag_free_executor()), clock=_fake_clock(TAG_FREE_CLOCK),
    )
    entries = [
        {"manifest_path": str(battery_manifest), "trial_path": str(tmp_path / "battery.trial.json"), "role": "training"},
        {"manifest_path": str(pose_manifest), "trial_path": str(tmp_path / "pose.trial.json"), "role": "training"},
    ]
    return _write_evidence_index(tmp_path, entries, name="evidence-index.json")


def test_fit_produces_a_calibration_candidate_from_training_evidence(tmp_path):
    index_path = _record_pair(tmp_path)
    output_path = tmp_path / "candidate.json"
    profile = calibration.fit(profile_kind="flight", evidence_index_path=index_path, output_path=output_path)
    assert profile.state == "CALIBRATION"
    assert profile.kind == "flight"
    assert profile.site_sha256 == "6284a284674d2a0b2b60cacf091b6180440b839c50f7e7ae4c703bfeb2226015"
    assert profile.speeds_cm_s == (20, 30)
    assert profile.battery.worst_observed_percent_per_s == pytest.approx(7 / 25)
    assert profile.battery.conservative_percent_per_s == pytest.approx(7 / 25 + 0.02)
    assert len(profile.pose_envelope) == 1
    point = profile.pose_envelope[0]
    assert (point.distance_cm, point.time_s, point.yaw_deg) == (0.0, 5.0, 90.0)
    assert point.drift_cm == pytest.approx(5.0)
    assert len(profile.training_run_ids) == 2
    assert output_path.exists()
    assert calibration.load_profile(output_path) == profile


def test_fit_refuses_the_tracker_kind_as_deferred(tmp_path):
    index_path = _record_pair(tmp_path)
    with pytest.raises(calibration.CalibrationError, match="Plan 05"):
        calibration.fit(
            profile_kind="tracker", evidence_index_path=index_path, output_path=tmp_path / "out.json",
        )


def test_fit_refuses_without_forced_return_training_evidence(tmp_path):
    pose_manifest, pose_digest = _write_manifest(tmp_path, trial_role="training", split_id="pose-1", name="pose.json")
    calibration.record(
        manifest_path=pose_manifest, authorization_digest=pose_digest, route_path=TAG_FREE_ROUTE,
        executor_factory=_tag_free_executor, clock=_fake_clock(TAG_FREE_CLOCK),
    )
    index_path = _write_evidence_index(tmp_path, [
        {"manifest_path": str(pose_manifest), "trial_path": str(tmp_path / "pose.trial.json"), "role": "training"},
    ], name="evidence-index.json")
    with pytest.raises(calibration.CalibrationError):
        calibration.fit(profile_kind="flight", evidence_index_path=index_path, output_path=tmp_path / "out.json")


def test_fit_refuses_without_tag_free_training_evidence(tmp_path):
    battery_manifest, battery_digest = _write_manifest(tmp_path, trial_role="training", split_id="b-1", name="battery.json")
    calibration.record(
        manifest_path=battery_manifest, authorization_digest=battery_digest, route_path=RETURN_ROUTE,
        executor_factory=_return_executor, clock=_fake_clock(RETURN_ROUTE_CLOCK),
    )
    index_path = _write_evidence_index(tmp_path, [
        {"manifest_path": str(battery_manifest), "trial_path": str(tmp_path / "battery.trial.json"), "role": "training"},
    ], name="evidence-index.json")
    with pytest.raises(calibration.CalibrationError):
        calibration.fit(profile_kind="flight", evidence_index_path=index_path, output_path=tmp_path / "out.json")


def test_fit_refuses_a_manifest_not_predeclared_training(tmp_path):
    manifest_path, digest = _write_manifest(tmp_path, trial_role="holdout", split_id="s")
    calibration.record(
        manifest_path=manifest_path, authorization_digest=digest, route_path=RETURN_ROUTE,
        executor_factory=_return_executor, clock=_fake_clock(RETURN_ROUTE_CLOCK),
    )
    index_path = _write_evidence_index(tmp_path, [
        {"manifest_path": str(manifest_path), "trial_path": str(tmp_path / "manifest.trial.json"), "role": "training"},
    ], name="evidence-index.json")
    with pytest.raises(calibration.CalibrationError):
        calibration.fit(profile_kind="flight", evidence_index_path=index_path, output_path=tmp_path / "out.json")


def test_fit_refuses_a_manifest_role_changed_after_being_predeclared(tmp_path):
    # The manifest underneath was preview()'d and recorded as trial_role="training";
    # declaring it "holdout" in the index must be refused, not silently accepted.
    manifest_path, digest = _write_manifest(tmp_path, trial_role="training", split_id="s")
    calibration.record(
        manifest_path=manifest_path, authorization_digest=digest, route_path=RETURN_ROUTE,
        executor_factory=_return_executor, clock=_fake_clock(RETURN_ROUTE_CLOCK),
    )
    index_path = _write_evidence_index(tmp_path, [
        {"manifest_path": str(manifest_path), "trial_path": str(tmp_path / "manifest.trial.json"), "role": "holdout"},
    ], name="evidence-index.json")
    with pytest.raises(calibration.CalibrationError):
        calibration.fit(profile_kind="flight", evidence_index_path=index_path, output_path=tmp_path / "out.json")


def test_fit_refuses_duplicate_training_run_ids(tmp_path):
    manifest_path, digest = _write_manifest(tmp_path, trial_role="training", split_id="s")
    calibration.record(
        manifest_path=manifest_path, authorization_digest=digest, route_path=RETURN_ROUTE,
        executor_factory=_return_executor, clock=_fake_clock(RETURN_ROUTE_CLOCK),
    )
    trial_path = tmp_path / "manifest.trial.json"
    entry = {"manifest_path": str(manifest_path), "trial_path": str(trial_path), "role": "training"}
    index_path = _write_evidence_index(tmp_path, [entry, entry], name="evidence-index.json")
    with pytest.raises(calibration.CalibrationError):
        calibration.fit(profile_kind="flight", evidence_index_path=index_path, output_path=tmp_path / "out.json")


def test_fit_refuses_training_trials_that_disagree_on_a_binding(tmp_path):
    # Both fixtures declare identical site/aircraft/firmware/camera/tag_map bindings by
    # construction, so nothing in the ordinary suite ever produces two training trials
    # that disagree -- hand-edit one to prove `fit` actually checks this (a mismatched
    # binding must disable the affected capability, per the binding spec).
    index_path = _record_pair(tmp_path)
    entries = _entries_of(index_path)
    battery_entry = next(e for e in entries if e["trial_path"].endswith("battery.trial.json"))
    trial = calibration.load_trial(battery_entry["trial_path"])
    tampered = calibration.replace(trial, aircraft_id="some-other-aircraft")
    calibration.write_trial(battery_entry["trial_path"], tampered)
    with pytest.raises(calibration.CalibrationError, match="aircraft_id"):
        calibration.fit(profile_kind="flight", evidence_index_path=index_path, output_path=tmp_path / "out.json")


def test_promote_refuses_a_holdout_trial_that_disagrees_with_the_candidate_on_a_binding(tmp_path):
    candidate_path, _, training_index_path = _fit_candidate(tmp_path)
    holdout_index_path = _holdout_index(tmp_path)
    entries = _entries_of(holdout_index_path)
    battery_entry = next(e for e in entries if e["trial_path"].endswith("holdout-battery.trial.json"))
    trial = calibration.load_trial(battery_entry["trial_path"])
    tampered = calibration.replace(trial, site_sha256="1" * 64)
    calibration.write_trial(battery_entry["trial_path"], tampered)
    with pytest.raises(calibration.CalibrationError, match="site_sha256"):
        _promote(
            tmp_path, candidate_path=candidate_path, training_index_path=training_index_path,
            holdout_index_path=holdout_index_path,
        )


def test_fit_never_produces_a_promoted_profile(tmp_path):
    index_path = _record_pair(tmp_path)
    profile = calibration.fit(profile_kind="flight", evidence_index_path=index_path, output_path=tmp_path / "out.json")
    assert profile.state != "PROMOTED"


# ---------------------------------------------------------------------------
# fit/promote: synthetic evidence is SIMULATION_ONLY and can never promote (Q4)
# ---------------------------------------------------------------------------


def test_fit_marks_a_profile_simulation_only_when_evidence_is_not_hardware_provenanced(tmp_path):
    index_path = _record_pair(tmp_path, hardware=False)
    profile = calibration.fit(profile_kind="flight", evidence_index_path=index_path, output_path=tmp_path / "out.json")
    assert profile.state == "SIMULATION_ONLY"


def test_fit_marks_a_profile_simulation_only_when_only_some_evidence_is_hardware_provenanced(tmp_path):
    battery_manifest, battery_digest = _write_manifest(tmp_path, trial_role="training", split_id="b-1", name="battery.json")
    calibration.record(
        manifest_path=battery_manifest, authorization_digest=battery_digest, route_path=RETURN_ROUTE,
        executor_factory=lambda: _hardware(_return_executor()), clock=_fake_clock(RETURN_ROUTE_CLOCK),
    )
    pose_manifest, pose_digest = _write_manifest(tmp_path, trial_role="training", split_id="p-1", name="pose.json")
    calibration.record(
        manifest_path=pose_manifest, authorization_digest=pose_digest, route_path=TAG_FREE_ROUTE,
        executor_factory=_tag_free_executor,  # not hardware-declared
        clock=_fake_clock(TAG_FREE_CLOCK),
    )
    index_path = _write_evidence_index(tmp_path, [
        {"manifest_path": str(battery_manifest), "trial_path": str(tmp_path / "battery.trial.json"), "role": "training"},
        {"manifest_path": str(pose_manifest), "trial_path": str(tmp_path / "pose.trial.json"), "role": "training"},
    ], name="evidence-index.json")
    profile = calibration.fit(profile_kind="flight", evidence_index_path=index_path, output_path=tmp_path / "out.json")
    assert profile.state == "SIMULATION_ONLY"


def test_promote_refuses_a_simulation_only_candidate_produced_by_fit(tmp_path):
    index_path = _record_pair(tmp_path, hardware=False)
    candidate_path = tmp_path / "candidate.json"
    calibration.fit(profile_kind="flight", evidence_index_path=index_path, output_path=candidate_path)
    holdout_index_path = _holdout_index(tmp_path)
    promote_index_path = _merged_index(tmp_path, index_path, holdout_index_path)
    with pytest.raises(calibration.CalibrationError):
        calibration.promote(
            profile_kind="flight", candidate_path=candidate_path, evidence_index_path=promote_index_path,
            output_path=tmp_path / "out.json",
        )


# ---------------------------------------------------------------------------
# promote: offline, holdout-only, cannot self-validate
# ---------------------------------------------------------------------------


def _fit_candidate(tmp_path):
    index_path = _record_pair(tmp_path)
    candidate_path = tmp_path / "candidate.json"
    profile = calibration.fit(profile_kind="flight", evidence_index_path=index_path, output_path=candidate_path)
    return candidate_path, profile, index_path


def _holdout_index(tmp_path, *, forced_return_kwargs=None, tag_free_kwargs=None, hardware=True,
                    tag_free_clock=_HOLDOUT_TAG_FREE_CLOCK, forced_return_clock=_HOLDOUT_RETURN_ROUTE_CLOCK):
    forced_return_kwargs = forced_return_kwargs or {}
    tag_free_kwargs = tag_free_kwargs or {}
    wrap = _hardware if hardware else (lambda executor: executor)
    battery_manifest, battery_digest = _write_manifest(
        tmp_path, trial_role="holdout", split_id="holdout-battery-1", name="holdout-battery.json"
    )
    calibration.record(
        manifest_path=battery_manifest, authorization_digest=battery_digest, route_path=RETURN_ROUTE,
        executor_factory=lambda: wrap(_return_executor(**forced_return_kwargs)), clock=_fake_clock(forced_return_clock),
    )
    pose_manifest, pose_digest = _write_manifest(
        tmp_path, trial_role="holdout", split_id="holdout-pose-1", name="holdout-pose.json"
    )
    calibration.record(
        manifest_path=pose_manifest, authorization_digest=pose_digest, route_path=TAG_FREE_ROUTE,
        executor_factory=lambda: wrap(_tag_free_executor(**tag_free_kwargs)), clock=_fake_clock(tag_free_clock),
    )
    entries = [
        {"manifest_path": str(battery_manifest), "trial_path": str(tmp_path / "holdout-battery.trial.json"), "role": "holdout"},
        {"manifest_path": str(pose_manifest), "trial_path": str(tmp_path / "holdout-pose.trial.json"), "role": "holdout"},
    ]
    return _write_evidence_index(tmp_path, entries, name="holdout-index.json")


def _promote(tmp_path, *, candidate_path, training_index_path, holdout_index_path, output_name="out.json",
             profile_kind="flight"):
    promote_index_path = _merged_index(tmp_path, training_index_path, holdout_index_path)
    return calibration.promote(
        profile_kind=profile_kind, candidate_path=candidate_path, evidence_index_path=promote_index_path,
        output_path=tmp_path / output_name,
    )


def test_promote_writes_a_promoted_profile_from_passing_holdout_evidence(tmp_path):
    candidate_path, candidate, training_index_path = _fit_candidate(tmp_path)
    holdout_index_path = _holdout_index(tmp_path)
    promoted = _promote(
        tmp_path, candidate_path=candidate_path, training_index_path=training_index_path,
        holdout_index_path=holdout_index_path, output_name="promoted.json",
    )
    assert promoted.state == "PROMOTED"
    assert len(promoted.holdout_run_ids) == 2
    assert set(promoted.holdout_run_ids).isdisjoint(candidate.training_run_ids)
    assert calibration.canonical_profile_sha256(promoted) != calibration.canonical_profile_sha256(candidate)
    assert calibration.load_profile(tmp_path / "promoted.json") == promoted


def test_promote_accepts_a_holdout_whose_timing_lands_in_the_same_bucket_as_training(tmp_path):
    # 5.2s (not 5.0s) still buckets to 5.0s: real-hardware timing jitter between two
    # flights of the "same" nominal leg must not force an exact-float replay.
    candidate_path, _, training_index_path = _fit_candidate(tmp_path)
    holdout_index_path = _holdout_index(tmp_path, tag_free_clock=(0.0, 0.0, 5.2, 5.2))
    promoted = _promote(
        tmp_path, candidate_path=candidate_path, training_index_path=training_index_path,
        holdout_index_path=holdout_index_path,
    )
    assert promoted.state == "PROMOTED"


def test_promote_refuses_a_profile_that_is_not_in_calibration_state(tmp_path):
    simulation_only = calibration.CalibrationProfile(
        schema_version=calibration.SCHEMA_VERSION, kind="flight", state="SIMULATION_ONLY",
        site_sha256=None, aircraft_id=None, firmware_version=None, camera_sha256=None, tag_map_sha256=None,
        speeds_cm_s=(), battery=None, pose_envelope=(), training_run_ids=(),
    )
    candidate_path = tmp_path / "synthetic.json"
    calibration.write_profile(candidate_path, simulation_only)
    holdout_index_path = _holdout_index(tmp_path)
    with pytest.raises(calibration.CalibrationError):
        calibration.promote(
            profile_kind="flight", candidate_path=candidate_path, evidence_index_path=holdout_index_path,
            output_path=tmp_path / "out.json",
        )


def test_promote_refuses_the_tracker_kind_as_deferred(tmp_path):
    candidate_path, _, _ = _fit_candidate(tmp_path)
    holdout_index_path = _holdout_index(tmp_path)
    with pytest.raises(calibration.CalibrationError, match="Plan 05"):
        calibration.promote(
            profile_kind="tracker", candidate_path=candidate_path, evidence_index_path=holdout_index_path,
            output_path=tmp_path / "out.json",
        )


def test_promote_refuses_when_profile_kind_does_not_match_the_candidate(tmp_path):
    candidate_path, candidate, _ = _fit_candidate(tmp_path)
    tracker_shaped = calibration.replace(candidate, kind="tracker")
    tracker_path = tmp_path / "tracker-candidate.json"
    calibration.write_profile(tracker_path, tracker_shaped)
    holdout_index_path = _holdout_index(tmp_path)
    with pytest.raises(calibration.CalibrationError):
        calibration.promote(
            profile_kind="flight", candidate_path=tracker_path, evidence_index_path=holdout_index_path,
            output_path=tmp_path / "out.json",
        )


def test_promote_refuses_fewer_than_two_holdout_entries(tmp_path):
    candidate_path, _, training_index_path = _fit_candidate(tmp_path)
    battery_manifest, battery_digest = _write_manifest(
        tmp_path, trial_role="holdout", split_id="holdout-battery-1", name="holdout-battery.json"
    )
    calibration.record(
        manifest_path=battery_manifest, authorization_digest=battery_digest, route_path=RETURN_ROUTE,
        executor_factory=lambda: _hardware(_return_executor()), clock=_fake_clock(RETURN_ROUTE_CLOCK),
    )
    holdout_index_path = _write_evidence_index(tmp_path, [
        {"manifest_path": str(battery_manifest), "trial_path": str(tmp_path / "holdout-battery.trial.json"), "role": "holdout"},
    ], name="holdout-index.json")
    with pytest.raises(calibration.CalibrationError):
        _promote(
            tmp_path, candidate_path=candidate_path, training_index_path=training_index_path,
            holdout_index_path=holdout_index_path,
        )


def test_promote_refuses_without_forced_return_holdout_evidence(tmp_path):
    candidate_path, _, training_index_path = _fit_candidate(tmp_path)
    pose_manifest, pose_digest = _write_manifest(tmp_path, trial_role="holdout", split_id="h1", name="h1.json")
    calibration.record(
        manifest_path=pose_manifest, authorization_digest=pose_digest, route_path=TAG_FREE_ROUTE,
        executor_factory=lambda: _hardware(_tag_free_executor()), clock=_fake_clock(TAG_FREE_CLOCK),
    )
    other_manifest, other_digest = _write_manifest(tmp_path, trial_role="holdout", split_id="h2", name="h2.json")
    calibration.record(
        manifest_path=other_manifest, authorization_digest=other_digest, route_path=TAG_FREE_ROUTE,
        executor_factory=lambda: _hardware(_tag_free_executor()), clock=_fake_clock(TAG_FREE_CLOCK),
    )
    holdout_index_path = _write_evidence_index(tmp_path, [
        {"manifest_path": str(pose_manifest), "trial_path": str(tmp_path / "h1.trial.json"), "role": "holdout"},
        {"manifest_path": str(other_manifest), "trial_path": str(tmp_path / "h2.trial.json"), "role": "holdout"},
    ], name="holdout-index.json")
    with pytest.raises(calibration.CalibrationError):
        _promote(
            tmp_path, candidate_path=candidate_path, training_index_path=training_index_path,
            holdout_index_path=holdout_index_path,
        )


def test_promote_refuses_a_manifest_role_changed_after_being_predeclared(tmp_path):
    # A manifest that was preview()'d and recorded as trial_role="holdout" (so the
    # trial itself says trial_role="holdout"), but the evidence index declares it
    # "training" here: promote must refuse it, not accept a role changed after the
    # fact.
    candidate_path, _, training_index_path = _fit_candidate(tmp_path)
    holdout_index_path = _holdout_index(tmp_path)
    document = {"schema_version": "room-calibration-evidence-index-v1", "entries": []}
    for entry in _entries_of(holdout_index_path):
        document["entries"].append({**entry, "role": "training"})
    tampered_path = tmp_path / "tampered-index.json"
    tampered_path.write_text(json.dumps(document), encoding="utf-8")
    promote_index_path = _merged_index(tmp_path, training_index_path, tampered_path)
    with pytest.raises(calibration.CalibrationError):
        calibration.promote(
            profile_kind="flight", candidate_path=candidate_path, evidence_index_path=promote_index_path,
            output_path=tmp_path / "out.json",
        )


def test_promote_refuses_duplicate_holdout_run_ids(tmp_path):
    candidate_path, _, training_index_path = _fit_candidate(tmp_path)
    holdout_index_path = _holdout_index(tmp_path)
    entries = _entries_of(holdout_index_path)
    entries.append(entries[0])
    tampered_path = _write_evidence_index(tmp_path, entries, name="tampered-index.json")
    with pytest.raises(calibration.CalibrationError):
        _promote(
            tmp_path, candidate_path=candidate_path, training_index_path=training_index_path,
            holdout_index_path=tampered_path,
        )


def test_promote_refuses_holdout_battery_consumption_beyond_the_candidate_bound(tmp_path):
    candidate_path, _, training_index_path = _fit_candidate(tmp_path)
    holdout_index_path = _holdout_index(tmp_path, forced_return_kwargs={"before": 80, "after": 20})
    with pytest.raises(calibration.CalibrationError):
        _promote(
            tmp_path, candidate_path=candidate_path, training_index_path=training_index_path,
            holdout_index_path=holdout_index_path,
        )


def test_promote_refuses_a_holdout_forced_return_trial_landing_below_the_floor(tmp_path):
    # consumed=7 over 25s -> rate 0.28, within the candidate's ~0.30%/s bound (so the
    # battery-rate check alone would pass), but ending at 13% is below the candidate's
    # 15% landing floor -- this must be refused on that basis specifically.
    candidate_path, candidate, training_index_path = _fit_candidate(tmp_path)
    assert candidate.battery.landing_floor_percent == 15.0
    holdout_index_path = _holdout_index(tmp_path, forced_return_kwargs={"before": 20, "after": 13})
    with pytest.raises(calibration.CalibrationError, match="landing floor"):
        _promote(
            tmp_path, candidate_path=candidate_path, training_index_path=training_index_path,
            holdout_index_path=holdout_index_path,
        )


def test_promote_refuses_holdout_pose_drift_beyond_the_candidate_envelope(tmp_path):
    candidate_path, _, training_index_path = _fit_candidate(tmp_path)
    holdout_index_path = _holdout_index(tmp_path, tag_free_kwargs={"drift_cm": 6.0})
    with pytest.raises(calibration.CalibrationError):
        _promote(
            tmp_path, candidate_path=candidate_path, training_index_path=training_index_path,
            holdout_index_path=holdout_index_path,
        )


def test_promote_accepts_a_holdout_pose_query_dominated_by_but_not_equal_to_training(tmp_path):
    # The holdout leg drifts less (3 cm, not the training 5 cm triangle) at the same
    # nominal timing/yaw: a strictly less-demanding combination than what training
    # sampled. The old exact-bucket-combo lookup happened to admit this only because
    # every dimension still landed in training's bucket; this test pins the dominance
    # behaviour explicitly (a *smaller* measured drift than the trained bound) rather
    # than relying on incidental bucket equality.
    candidate_path, _, training_index_path = _fit_candidate(tmp_path)
    holdout_index_path = _holdout_index(tmp_path, tag_free_kwargs={"drift_cm": 3.0})
    promoted = _promote(
        tmp_path, candidate_path=candidate_path, training_index_path=training_index_path,
        holdout_index_path=holdout_index_path,
    )
    assert promoted.state == "PROMOTED"


def test_promote_refuses_a_holdout_combination_never_sampled_during_training(tmp_path):
    candidate_path, _, training_index_path = _fit_candidate(tmp_path)
    # A very different clock -> the tag-free leg's elapsed time buckets to 6.0s, not
    # training's 5.0s bucket -> the envelope has no entry for this combination, which
    # must fail closed (never interpolated/extrapolated).
    holdout_index_path = _holdout_index(tmp_path, tag_free_clock=(0.0, 0.0, 6.0, 6.0))
    with pytest.raises(calibration.CalibrationError):
        _promote(
            tmp_path, candidate_path=candidate_path, training_index_path=training_index_path,
            holdout_index_path=holdout_index_path,
        )


def test_promote_refuses_holdout_evidence_that_is_not_hardware_provenanced(tmp_path):
    candidate_path, _, training_index_path = _fit_candidate(tmp_path)
    holdout_index_path = _holdout_index(tmp_path, hardware=False)
    with pytest.raises(calibration.CalibrationError, match="hardware"):
        _promote(
            tmp_path, candidate_path=candidate_path, training_index_path=training_index_path,
            holdout_index_path=holdout_index_path,
        )


def test_promote_refuses_a_holdout_trial_flown_at_an_unbound_speed(tmp_path):
    candidate_path, _, training_index_path = _fit_candidate(tmp_path)
    holdout_index_path = _holdout_index(tmp_path)
    entries = _entries_of(holdout_index_path)
    battery_entry = next(e for e in entries if e["trial_path"].endswith("holdout-battery.trial.json"))
    trial = calibration.load_trial(battery_entry["trial_path"])
    tampered = calibration.replace(trial, speeds_cm_s=(99,))
    calibration.write_trial(battery_entry["trial_path"], tampered)
    with pytest.raises(calibration.CalibrationError, match="speed"):
        _promote(
            tmp_path, candidate_path=candidate_path, training_index_path=training_index_path,
            holdout_index_path=holdout_index_path,
        )


def test_promote_refuses_a_holdout_trial_that_duplicates_training_content(tmp_path):
    candidate_path, _, training_index_path = _fit_candidate(tmp_path)
    training_entries = _entries_of(training_index_path)
    battery_entry = next(e for e in training_entries if e["trial_path"].endswith("battery.trial.json"))
    duplicate_trial = calibration.load_trial(battery_entry["trial_path"])

    holdout_manifest_path, holdout_digest = _write_manifest(
        tmp_path, trial_role="holdout", split_id="holdout-battery-copy", name="holdout-copy.json"
    )
    # Every MEASURED field stays byte-identical to the training trial; only the
    # identity fields (run id / role / split / manifest binding) are relabelled --
    # exactly what a copy-pasted or replayed trial looks like.
    forged = calibration.replace(
        duplicate_trial, run_id=holdout_digest, trial_role="holdout",
        split_id="holdout-battery-copy", manifest_authorization_digest=holdout_digest,
    )
    forged_path = tmp_path / "holdout-copy.trial.json"
    calibration.write_trial(forged_path, forged)

    pose_manifest, pose_digest = _write_manifest(tmp_path, trial_role="holdout", split_id="holdout-pose-1", name="holdout-pose.json")
    calibration.record(
        manifest_path=pose_manifest, authorization_digest=pose_digest, route_path=TAG_FREE_ROUTE,
        executor_factory=lambda: _hardware(_tag_free_executor()), clock=_fake_clock(TAG_FREE_CLOCK),
    )
    holdout_index_path = _write_evidence_index(tmp_path, [
        {"manifest_path": str(holdout_manifest_path), "trial_path": str(forged_path), "role": "holdout"},
        {"manifest_path": str(pose_manifest), "trial_path": str(tmp_path / "holdout-pose.trial.json"), "role": "holdout"},
    ], name="holdout-index.json")
    with pytest.raises(calibration.CalibrationError, match="duplicate"):
        _promote(
            tmp_path, candidate_path=candidate_path, training_index_path=training_index_path,
            holdout_index_path=holdout_index_path,
        )


# ---------------------------------------------------------------------------
# promote: a candidate profile record cannot approve itself (Q1)
# ---------------------------------------------------------------------------


def test_promote_refuses_a_hand_written_candidate_with_no_backing_training_evidence(tmp_path):
    forged = calibration.CalibrationProfile(
        schema_version=calibration.SCHEMA_VERSION, kind="flight", state="CALIBRATION",
        site_sha256="0" * 64, aircraft_id=_HARDWARE_AIRCRAFT_ID, firmware_version=_HARDWARE_FIRMWARE_VERSION,
        camera_sha256="0" * 64, tag_map_sha256="0" * 64, speeds_cm_s=(999,),
        battery=calibration.BatteryBound(
            worst_observed_percent_per_s=100.0, margin_percent_per_s=0.02,
            quantization_allowance_percent=1.0, landing_floor_percent=15.0,
        ),
        pose_envelope=(), training_run_ids=(),
    )
    forged_path = tmp_path / "forged.json"
    calibration.write_profile(forged_path, forged)
    holdout_index_path = _holdout_index(tmp_path)
    with pytest.raises(calibration.CalibrationError):
        calibration.promote(
            profile_kind="flight", candidate_path=forged_path, evidence_index_path=holdout_index_path,
            output_path=tmp_path / "out.json",
        )


def test_promote_refuses_a_candidate_whose_declared_values_do_not_match_a_fresh_fit(tmp_path):
    candidate_path, candidate, training_index_path = _fit_candidate(tmp_path)
    edited = calibration.replace(candidate, speeds_cm_s=(999,))
    edited_path = tmp_path / "edited.json"
    calibration.write_profile(edited_path, edited)
    holdout_index_path = _holdout_index(tmp_path)
    with pytest.raises(calibration.CalibrationError, match="fresh fit"):
        _promote(
            tmp_path, candidate_path=edited_path, training_index_path=training_index_path,
            holdout_index_path=holdout_index_path,
        )


def test_promote_refuses_a_candidate_with_an_inflated_battery_margin(tmp_path):
    candidate_path, candidate, training_index_path = _fit_candidate(tmp_path)
    inflated = calibration.replace(candidate, battery=calibration.replace(candidate.battery, margin_percent_per_s=50.0))
    inflated_path = tmp_path / "inflated.json"
    calibration.write_profile(inflated_path, inflated)
    holdout_index_path = _holdout_index(tmp_path)
    with pytest.raises(calibration.CalibrationError, match="fresh fit"):
        _promote(
            tmp_path, candidate_path=inflated_path, training_index_path=training_index_path,
            holdout_index_path=holdout_index_path,
        )


# ---------------------------------------------------------------------------
# envelope_bound_for: dominance-based pose-envelope lookup (Plan 06 Task 4 pre-fix,
# controller ruling 2026-09-23). The old lookup required a query's (distance, time,
# yaw) bucket to exactly match a trained bucket combination on all three dimensions
# at once, so real flights whose yaw/timing did not exactly reproduce a calibration
# leg were always refused. The fix: a query is inside the envelope if some sampled
# combination dominates it (distance >= query, time >= query, |yaw| >= |query|,
# after the same bucketing used to build the envelope); the bound is the worst
# drift among every dominating sample; a query beyond every sampled maximum in any
# one dimension is never admitted (no extrapolation).
# ---------------------------------------------------------------------------


def test_envelope_bound_for_admits_an_exact_match():
    envelope = (calibration.PoseEnvelopePoint(distance_cm=0.0, time_s=5.0, yaw_deg=90.0, drift_cm=5.0),)
    bound = calibration.envelope_bound_for(envelope, distance_cm=0.0, time_s=5.0, yaw_deg=90.0)
    assert bound == pytest.approx(5.0)


def test_envelope_bound_for_admits_a_query_dominated_by_a_sampled_combination():
    # The old exact-combo match would refuse this: no trained point at
    # (distance=50, time=3.0, yaw=30), but a trained point covering a strictly more
    # demanding combination exists, so the query is conservatively admitted.
    envelope = (calibration.PoseEnvelopePoint(distance_cm=100.0, time_s=5.0, yaw_deg=90.0, drift_cm=8.0),)
    bound = calibration.envelope_bound_for(envelope, distance_cm=50.0, time_s=3.0, yaw_deg=30.0)
    assert bound == pytest.approx(8.0)


def test_envelope_bound_for_refuses_a_query_beyond_the_sampled_distance_maximum():
    envelope = (calibration.PoseEnvelopePoint(distance_cm=100.0, time_s=5.0, yaw_deg=90.0, drift_cm=8.0),)
    assert calibration.envelope_bound_for(envelope, distance_cm=150.0, time_s=3.0, yaw_deg=30.0) is None


def test_envelope_bound_for_refuses_a_query_beyond_the_sampled_time_maximum():
    envelope = (calibration.PoseEnvelopePoint(distance_cm=100.0, time_s=5.0, yaw_deg=90.0, drift_cm=8.0),)
    assert calibration.envelope_bound_for(envelope, distance_cm=50.0, time_s=6.0, yaw_deg=30.0) is None


def test_envelope_bound_for_refuses_a_query_beyond_the_sampled_yaw_maximum():
    envelope = (calibration.PoseEnvelopePoint(distance_cm=100.0, time_s=5.0, yaw_deg=90.0, drift_cm=8.0),)
    assert calibration.envelope_bound_for(envelope, distance_cm=50.0, time_s=3.0, yaw_deg=100.0) is None


def test_envelope_bound_for_uses_the_worst_drift_among_all_dominating_samples():
    envelope = (
        calibration.PoseEnvelopePoint(distance_cm=100.0, time_s=5.0, yaw_deg=90.0, drift_cm=8.0),
        calibration.PoseEnvelopePoint(distance_cm=100.0, time_s=10.0, yaw_deg=90.0, drift_cm=3.0),
    )
    bound = calibration.envelope_bound_for(envelope, distance_cm=10.0, time_s=1.0, yaw_deg=10.0)
    assert bound == pytest.approx(8.0)  # never the smaller, never an average


def test_envelope_bound_for_empty_envelope_admits_nothing():
    assert calibration.envelope_bound_for((), distance_cm=0.0, time_s=0.0, yaw_deg=0.0) is None


def test_envelope_bound_for_treats_yaw_as_an_absolute_magnitude():
    envelope = (calibration.PoseEnvelopePoint(distance_cm=0.0, time_s=5.0, yaw_deg=90.0, drift_cm=5.0),)
    bound = calibration.envelope_bound_for(envelope, distance_cm=0.0, time_s=5.0, yaw_deg=-45.0)
    assert bound == pytest.approx(5.0)


def test_envelope_bound_for_absorbs_sub_bucket_jitter_like_the_old_lookup():
    # 5.2s still buckets to the same 5.0s combination as training (regression: the
    # dominance rewrite must not lose the jitter tolerance the bucketing already gave).
    envelope = (calibration.PoseEnvelopePoint(distance_cm=0.0, time_s=5.0, yaw_deg=90.0, drift_cm=5.0),)
    bound = calibration.envelope_bound_for(envelope, distance_cm=0.0, time_s=5.2, yaw_deg=90.0)
    assert bound == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# admissible_for: CALIBRATION legal only in a trial, PROMOTED only for live use (Q10)
# ---------------------------------------------------------------------------


def _minimal_profile(state):
    return calibration.CalibrationProfile(
        schema_version=calibration.SCHEMA_VERSION, kind="flight", state=state,
        site_sha256=None, aircraft_id=None, firmware_version=None, camera_sha256=None, tag_map_sha256=None,
        speeds_cm_s=(), battery=None, pose_envelope=(), training_run_ids=(),
        holdout_run_ids=("h",) if state == "PROMOTED" else (),
    )


def test_admissible_for_calibration_profile_allows_only_trial_grants():
    profile = _minimal_profile("CALIBRATION")
    assert calibration.admissible_for(profile, grant_state="TRIAL") is True
    assert calibration.admissible_for(profile, grant_state="PROMOTED") is False


def test_admissible_for_promoted_profile_allows_only_promoted_grants():
    profile = _minimal_profile("PROMOTED")
    assert calibration.admissible_for(profile, grant_state="PROMOTED") is True
    assert calibration.admissible_for(profile, grant_state="TRIAL") is False


def test_admissible_for_simulation_only_profile_is_never_admissible():
    profile = _minimal_profile("SIMULATION_ONLY")
    assert calibration.admissible_for(profile, grant_state="TRIAL") is False
    assert calibration.admissible_for(profile, grant_state="PROMOTED") is False


# ---------------------------------------------------------------------------
# JSON Schema
# ---------------------------------------------------------------------------

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs" / "superpowers" / "specs" / "contracts" / "room-calibration-profile-v1.schema.json"
)


def test_promoted_profile_validates_against_the_schema(tmp_path):
    from jsonschema import Draft202012Validator

    candidate_path, _, training_index_path = _fit_candidate(tmp_path)
    holdout_index_path = _holdout_index(tmp_path)
    promoted = _promote(
        tmp_path, candidate_path=candidate_path, training_index_path=training_index_path,
        holdout_index_path=holdout_index_path, output_name="promoted.json",
    )
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(json.loads(promoted.canonical_bytes()))


def test_schema_rejects_an_unknown_key():
    from jsonschema import Draft202012Validator, ValidationError

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    simulation_only = calibration.CalibrationProfile(
        schema_version=calibration.SCHEMA_VERSION, kind="flight", state="SIMULATION_ONLY",
        site_sha256=None, aircraft_id=None, firmware_version=None, camera_sha256=None, tag_map_sha256=None,
        speeds_cm_s=(), battery=None, pose_envelope=(), training_run_ids=(),
    )
    document = {**simulation_only.to_document(), "unexpected_field": True}
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(document)


# ---------------------------------------------------------------------------
# module never references Jev/Tello directly
# ---------------------------------------------------------------------------


def test_calibration_module_never_imports_a_jev_or_tello_client():
    source = Path(calibration.__file__).read_text(encoding="utf-8").lower()
    for needle in ("import sentinel.jev", "from sentinel.jev", "import sentinel.client", "from sentinel.client"):
        assert needle not in source


# ---------------------------------------------------------------------------
# scripts/room_calibrate.py CLI
# ---------------------------------------------------------------------------


def test_cli_preview_prints_canonical_json_and_writes_manifest_out(tmp_path, capsys):
    import scripts.room_calibrate as cli

    manifest_out = tmp_path / "manifest.json"
    exit_code = cli.main([
        "preview", "--target-mode", "record", "--trial-role", "training",
        "--split-id", "s", "--manifest-out", str(manifest_out),
    ])
    out = capsys.readouterr().out.strip()
    assert exit_code == 0
    assert json.loads(out)["trial_role"] == "training"
    assert manifest_out.read_bytes() == out.encode("utf-8")


def test_cli_preview_refuses_an_invalid_trial_role(capsys):
    import scripts.room_calibrate as cli

    with pytest.raises(SystemExit):
        cli.main(["preview", "--target-mode", "record", "--trial-role", "bogus", "--split-id", "s"])


def test_cli_record_requires_the_execute_flag():
    import scripts.room_calibrate as cli

    with pytest.raises(SystemExit):
        cli.main(["record", "--manifest", "m.json", "--authorization-digest", "0" * 64, "--route", "r.json"])


def test_cli_record_default_executor_factory_refuses_a_real_client(tmp_path):
    import scripts.room_calibrate as cli

    manifest_path, digest = _write_manifest(tmp_path, trial_role="training", split_id="s")
    with pytest.raises(RuntimeError, match="never constructs a real"):
        cli.main([
            "record", "--execute", "--manifest", str(manifest_path),
            "--authorization-digest", digest, "--route", str(RETURN_ROUTE),
        ])


def test_cli_record_with_an_injected_fake_executor_factory_succeeds(tmp_path):
    import scripts.room_calibrate as cli

    manifest_path, digest = _write_manifest(tmp_path, trial_role="training", split_id="s")
    exit_code = cli.main(
        ["record", "--execute", "--manifest", str(manifest_path), "--authorization-digest", digest,
         "--route", str(RETURN_ROUTE)],
        executor_factory=_return_executor,
    )
    assert exit_code == 0
    assert (tmp_path / "manifest.trial.json").exists()


def test_cli_fit_and_promote_round_trip(tmp_path, capsys):
    import scripts.room_calibrate as cli

    index_path = _record_pair(tmp_path)
    candidate_path = tmp_path / "candidate.json"
    exit_code = cli.main([
        "fit", "--profile-kind", "flight", "--evidence-index", str(index_path), "--output", str(candidate_path),
    ])
    fit_digest = capsys.readouterr().out.strip()
    assert exit_code == 0
    assert calibration.load_profile(candidate_path).state == "CALIBRATION"

    holdout_index_path = _holdout_index(tmp_path)
    promote_index_path = _merged_index(tmp_path, index_path, holdout_index_path)
    promoted_path = tmp_path / "promoted.json"
    exit_code = cli.main([
        "promote", "--profile-kind", "flight", "--evidence-index", str(promote_index_path),
        "--output", str(promoted_path), "--candidate", str(candidate_path),
    ])
    promote_digest = capsys.readouterr().out.strip()
    assert exit_code == 0
    promoted = calibration.load_profile(promoted_path)
    assert promoted.state == "PROMOTED"
    assert promote_digest == calibration.canonical_profile_sha256(promoted)
    assert fit_digest != promote_digest


def test_cli_fit_refuses_tracker_and_prints_the_deferral_reason(tmp_path, capsys):
    import scripts.room_calibrate as cli

    index_path = _record_pair(tmp_path)
    exit_code = cli.main([
        "fit", "--profile-kind", "tracker", "--evidence-index", str(index_path),
        "--output", str(tmp_path / "out.json"),
    ])
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "Plan 05" in err
