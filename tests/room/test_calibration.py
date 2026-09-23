"""Failing-first tests for src/sentinel/room/calibration.py and scripts/room_calibrate.py
(Plan 06 Task 3, count-and-return scope).

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


# ---------------------------------------------------------------------------
# Fakes: never a real Tello/provider client.
# ---------------------------------------------------------------------------


class FakeExecutor:
    """Duck-types StepExecutor's public surface (start/execute/land/close)."""

    def __init__(self, *, telemetry_sequence, poses=None, terminal_state="LANDED"):
        self.telemetry_sequence = list(telemetry_sequence)
        self.poses = dict(poses or {})
        self.terminal_state = terminal_state
        self.started_with = None
        self.executed = []
        self.landed = False
        self.closed = False

    def start(self, home_definition):
        self.started_with = home_definition

    def execute(self, plan):
        self.executed.append(plan.checkpoint_id)
        telemetry = self.telemetry_sequence.pop(0)
        pose = self.poses.get(plan.checkpoint_id, plan.target)
        return SimpleNamespace(status="COMPLETED", telemetry=telemetry, pose=pose)

    def land(self):
        self.landed = True
        return SimpleNamespace(state=self.terminal_state)

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


RETURN_ROUTE_CLOCK = (0.0, 0.0, 10.0, 10.0, 25.0, 25.0)  # leg1 10s, leg2 15s, total 25s
TAG_FREE_CLOCK = (0.0, 0.0, 5.0, 5.0)  # one leg, 5s


def _return_executor(before=80, after=74):
    return FakeExecutor(telemetry_sequence=[
        Telemetry(battery_percent=before, height_cm=80),
        Telemetry(battery_percent=after, height_cm=80),
    ])


def _tag_free_executor(*, drift_cm=5.0, battery=90):
    # A 3-4-5 triangle keeps the drift an exact, round number.
    dx, dy = (3, 4) if drift_cm == 5.0 else (round(drift_cm), 0)
    measured = Position(x_cm=dx, y_cm=dy, z_cm=100)
    return FakeExecutor(
        telemetry_sequence=[Telemetry(battery_percent=battery, height_cm=100)],
        poses={"hover-yaw-sweep": measured},
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


def test_tag_free_hover_fixture_parses():
    route = calibration.load_route(TAG_FREE_ROUTE)
    assert route.route_id == "tag-free-hover"
    assert route.route_kind == "tag_free"
    assert route.checkpoints[0].looks_deg == (45, -45, 90)


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
    executor = _return_executor()
    trial = calibration.record(
        manifest_path=manifest_path, authorization_digest=digest, route_path=RETURN_ROUTE,
        executor_factory=lambda: executor, clock=_fake_clock(RETURN_ROUTE_CLOCK),
    )
    assert executor.started_with is not None
    assert executor.executed == ["outbound", "forced-return"]
    assert executor.landed and executor.closed
    assert trial.trial_role == "training"
    assert trial.split_id == "battery-1"
    assert trial.route_kind == "forced_return"
    assert trial.terminal_state == "LANDED"
    assert trial.total_elapsed_s == 25.0
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
    sample = trial.samples[0]
    assert sample.distance_cm == 0.0
    assert sample.elapsed_s == 5.0
    assert sample.yaw_deg == 90.0
    assert sample.drift_cm == pytest.approx(5.0)


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


def test_record_refuses_when_a_leg_does_not_complete_cleanly(tmp_path):
    manifest_path, digest = _write_manifest(tmp_path, trial_role="training", split_id="s")
    executor = FailingExecutor(telemetry_sequence=[Telemetry(battery_percent=80, height_cm=80)])
    with pytest.raises(calibration.CalibrationError):
        calibration.record(
            manifest_path=manifest_path, authorization_digest=digest, route_path=RETURN_ROUTE,
            executor_factory=lambda: executor, clock=_fake_clock(RETURN_ROUTE_CLOCK),
        )
    assert executor.closed  # cleanup still runs
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


def _record_pair(tmp_path):
    """One training forced-return trial + one training tag-free trial, evidence-indexed."""
    battery_manifest, battery_digest = _write_manifest(
        tmp_path, trial_role="training", split_id="battery-1", name="battery.json"
    )
    calibration.record(
        manifest_path=battery_manifest, authorization_digest=battery_digest, route_path=RETURN_ROUTE,
        executor_factory=_return_executor, clock=_fake_clock(RETURN_ROUTE_CLOCK),
    )
    pose_manifest, pose_digest = _write_manifest(
        tmp_path, trial_role="training", split_id="pose-1", name="pose.json"
    )
    calibration.record(
        manifest_path=pose_manifest, authorization_digest=pose_digest, route_path=TAG_FREE_ROUTE,
        executor_factory=_tag_free_executor, clock=_fake_clock(TAG_FREE_CLOCK),
    )
    index = calibration.CalibrationEvidenceIndex(
        schema_version=calibration._EVIDENCE_INDEX_SCHEMA_VERSION,
        entries=(
            calibration.EvidenceEntry(str(battery_manifest), str(tmp_path / "battery.trial.json"), "training"),
            calibration.EvidenceEntry(str(pose_manifest), str(tmp_path / "pose.trial.json"), "training"),
        ),
    )
    index_path = tmp_path / "evidence-index.json"
    index_path.write_text(json.dumps({
        "schema_version": index.schema_version,
        "entries": [
            {"manifest_path": e.manifest_path, "trial_path": e.trial_path, "role": e.role}
            for e in index.entries
        ],
    }), encoding="utf-8")
    return index_path


def test_fit_produces_a_calibration_candidate_from_training_evidence(tmp_path):
    index_path = _record_pair(tmp_path)
    output_path = tmp_path / "candidate.json"
    profile = calibration.fit(profile_kind="flight", evidence_index_path=index_path, output_path=output_path)
    assert profile.state == "CALIBRATION"
    assert profile.kind == "flight"
    assert profile.site_sha256 == "6284a284674d2a0b2b60cacf091b6180440b839c50f7e7ae4c703bfeb2226015"
    assert profile.speeds_cm_s == (20, 30)
    assert profile.battery.worst_observed_percent_per_s == pytest.approx(6 / 25)
    assert profile.battery.conservative_percent_per_s == pytest.approx(6 / 25 + 0.02 + 1.0)
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
    index_path = tmp_path / "evidence-index.json"
    index_path.write_text(json.dumps({
        "schema_version": "room-calibration-evidence-index-v1",
        "entries": [{"manifest_path": str(pose_manifest), "trial_path": str(tmp_path / "pose.trial.json"), "role": "training"}],
    }), encoding="utf-8")
    with pytest.raises(calibration.CalibrationError):
        calibration.fit(profile_kind="flight", evidence_index_path=index_path, output_path=tmp_path / "out.json")


def test_fit_refuses_without_tag_free_training_evidence(tmp_path):
    battery_manifest, battery_digest = _write_manifest(tmp_path, trial_role="training", split_id="b-1", name="battery.json")
    calibration.record(
        manifest_path=battery_manifest, authorization_digest=battery_digest, route_path=RETURN_ROUTE,
        executor_factory=_return_executor, clock=_fake_clock(RETURN_ROUTE_CLOCK),
    )
    index_path = tmp_path / "evidence-index.json"
    index_path.write_text(json.dumps({
        "schema_version": "room-calibration-evidence-index-v1",
        "entries": [{"manifest_path": str(battery_manifest), "trial_path": str(tmp_path / "battery.trial.json"), "role": "training"}],
    }), encoding="utf-8")
    with pytest.raises(calibration.CalibrationError):
        calibration.fit(profile_kind="flight", evidence_index_path=index_path, output_path=tmp_path / "out.json")


def test_fit_refuses_a_manifest_not_predeclared_training(tmp_path):
    manifest_path, digest = _write_manifest(tmp_path, trial_role="holdout", split_id="s")
    calibration.record(
        manifest_path=manifest_path, authorization_digest=digest, route_path=RETURN_ROUTE,
        executor_factory=_return_executor, clock=_fake_clock(RETURN_ROUTE_CLOCK),
    )
    index_path = tmp_path / "evidence-index.json"
    index_path.write_text(json.dumps({
        "schema_version": "room-calibration-evidence-index-v1",
        "entries": [{"manifest_path": str(manifest_path), "trial_path": str(tmp_path / "manifest.trial.json"), "role": "training"}],
    }), encoding="utf-8")
    with pytest.raises(calibration.CalibrationError):
        calibration.fit(profile_kind="flight", evidence_index_path=index_path, output_path=tmp_path / "out.json")


def test_fit_refuses_duplicate_training_run_ids(tmp_path):
    manifest_path, digest = _write_manifest(tmp_path, trial_role="training", split_id="s")
    calibration.record(
        manifest_path=manifest_path, authorization_digest=digest, route_path=RETURN_ROUTE,
        executor_factory=_return_executor, clock=_fake_clock(RETURN_ROUTE_CLOCK),
    )
    trial_path = tmp_path / "manifest.trial.json"
    index_path = tmp_path / "evidence-index.json"
    entry = {"manifest_path": str(manifest_path), "trial_path": str(trial_path), "role": "training"}
    index_path.write_text(json.dumps({
        "schema_version": "room-calibration-evidence-index-v1", "entries": [entry, entry],
    }), encoding="utf-8")
    with pytest.raises(calibration.CalibrationError):
        calibration.fit(profile_kind="flight", evidence_index_path=index_path, output_path=tmp_path / "out.json")


def test_fit_never_produces_a_promoted_or_simulation_only_profile(tmp_path):
    index_path = _record_pair(tmp_path)
    profile = calibration.fit(profile_kind="flight", evidence_index_path=index_path, output_path=tmp_path / "out.json")
    assert profile.state not in ("PROMOTED", "SIMULATION_ONLY")


# ---------------------------------------------------------------------------
# promote: offline, holdout-only, cannot self-validate
# ---------------------------------------------------------------------------


def _fit_candidate(tmp_path):
    index_path = _record_pair(tmp_path)
    candidate_path = tmp_path / "candidate.json"
    profile = calibration.fit(profile_kind="flight", evidence_index_path=index_path, output_path=candidate_path)
    return candidate_path, profile


def _holdout_index(tmp_path, *, forced_return_kwargs=None, tag_free_kwargs=None):
    forced_return_kwargs = forced_return_kwargs or {}
    tag_free_kwargs = tag_free_kwargs or {}
    battery_manifest, battery_digest = _write_manifest(
        tmp_path, trial_role="holdout", split_id="holdout-battery-1", name="holdout-battery.json"
    )
    calibration.record(
        manifest_path=battery_manifest, authorization_digest=battery_digest, route_path=RETURN_ROUTE,
        executor_factory=lambda: _return_executor(**forced_return_kwargs), clock=_fake_clock(RETURN_ROUTE_CLOCK),
    )
    pose_manifest, pose_digest = _write_manifest(
        tmp_path, trial_role="holdout", split_id="holdout-pose-1", name="holdout-pose.json"
    )
    calibration.record(
        manifest_path=pose_manifest, authorization_digest=pose_digest, route_path=TAG_FREE_ROUTE,
        executor_factory=lambda: _tag_free_executor(**tag_free_kwargs), clock=_fake_clock(TAG_FREE_CLOCK),
    )
    index_path = tmp_path / "holdout-index.json"
    index_path.write_text(json.dumps({
        "schema_version": "room-calibration-evidence-index-v1",
        "entries": [
            {"manifest_path": str(battery_manifest), "trial_path": str(tmp_path / "holdout-battery.trial.json"), "role": "holdout"},
            {"manifest_path": str(pose_manifest), "trial_path": str(tmp_path / "holdout-pose.trial.json"), "role": "holdout"},
        ],
    }), encoding="utf-8")
    return index_path


def test_promote_writes_a_promoted_profile_from_passing_holdout_evidence(tmp_path):
    candidate_path, candidate = _fit_candidate(tmp_path)
    holdout_index_path = _holdout_index(tmp_path)
    output_path = tmp_path / "promoted.json"
    promoted = calibration.promote(
        profile_kind="flight", candidate_path=candidate_path, evidence_index_path=holdout_index_path,
        output_path=output_path,
    )
    assert promoted.state == "PROMOTED"
    assert len(promoted.holdout_run_ids) == 2
    assert set(promoted.holdout_run_ids).isdisjoint(candidate.training_run_ids)
    assert calibration.canonical_profile_sha256(promoted) != calibration.canonical_profile_sha256(candidate)
    assert calibration.load_profile(output_path) == promoted


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
    candidate_path, _ = _fit_candidate(tmp_path)
    holdout_index_path = _holdout_index(tmp_path)
    with pytest.raises(calibration.CalibrationError, match="Plan 05"):
        calibration.promote(
            profile_kind="tracker", candidate_path=candidate_path, evidence_index_path=holdout_index_path,
            output_path=tmp_path / "out.json",
        )


def test_promote_refuses_when_profile_kind_does_not_match_the_candidate(tmp_path):
    candidate_path, candidate = _fit_candidate(tmp_path)
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
    candidate_path, _ = _fit_candidate(tmp_path)
    battery_manifest, battery_digest = _write_manifest(
        tmp_path, trial_role="holdout", split_id="holdout-battery-1", name="holdout-battery.json"
    )
    calibration.record(
        manifest_path=battery_manifest, authorization_digest=battery_digest, route_path=RETURN_ROUTE,
        executor_factory=_return_executor, clock=_fake_clock(RETURN_ROUTE_CLOCK),
    )
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps({
        "schema_version": "room-calibration-evidence-index-v1",
        "entries": [{"manifest_path": str(battery_manifest), "trial_path": str(tmp_path / "holdout-battery.trial.json"), "role": "holdout"}],
    }), encoding="utf-8")
    with pytest.raises(calibration.CalibrationError):
        calibration.promote(
            profile_kind="flight", candidate_path=candidate_path, evidence_index_path=index_path,
            output_path=tmp_path / "out.json",
        )


def test_promote_refuses_without_forced_return_holdout_evidence(tmp_path):
    candidate_path, _ = _fit_candidate(tmp_path)
    pose_manifest, pose_digest = _write_manifest(tmp_path, trial_role="holdout", split_id="h1", name="h1.json")
    calibration.record(
        manifest_path=pose_manifest, authorization_digest=pose_digest, route_path=TAG_FREE_ROUTE,
        executor_factory=_tag_free_executor, clock=_fake_clock(TAG_FREE_CLOCK),
    )
    other_manifest, other_digest = _write_manifest(tmp_path, trial_role="holdout", split_id="h2", name="h2.json")
    calibration.record(
        manifest_path=other_manifest, authorization_digest=other_digest, route_path=TAG_FREE_ROUTE,
        executor_factory=_tag_free_executor, clock=_fake_clock(TAG_FREE_CLOCK),
    )
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps({
        "schema_version": "room-calibration-evidence-index-v1",
        "entries": [
            {"manifest_path": str(pose_manifest), "trial_path": str(tmp_path / "h1.trial.json"), "role": "holdout"},
            {"manifest_path": str(other_manifest), "trial_path": str(tmp_path / "h2.trial.json"), "role": "holdout"},
        ],
    }), encoding="utf-8")
    with pytest.raises(calibration.CalibrationError):
        calibration.promote(
            profile_kind="flight", candidate_path=candidate_path, evidence_index_path=index_path,
            output_path=tmp_path / "out.json",
        )


def test_promote_refuses_a_manifest_role_changed_after_being_predeclared(tmp_path):
    candidate_path, _ = _fit_candidate(tmp_path)
    holdout_index_path = _holdout_index(tmp_path)
    document = json.loads(holdout_index_path.read_text(encoding="utf-8"))
    # The manifests underneath were preview()'d and recorded as trial_role="holdout";
    # declaring them "training" here must be refused, not silently accepted.
    for entry in document["entries"]:
        entry["role"] = "training"
    tampered_path = tmp_path / "tampered-index.json"
    tampered_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(calibration.CalibrationError):
        calibration.fit(profile_kind="flight", evidence_index_path=tampered_path, output_path=tmp_path / "out.json")


def test_promote_refuses_duplicate_holdout_run_ids(tmp_path):
    candidate_path, _ = _fit_candidate(tmp_path)
    holdout_index_path = _holdout_index(tmp_path)
    document = json.loads(holdout_index_path.read_text(encoding="utf-8"))
    document["entries"].append(document["entries"][0])
    tampered_path = tmp_path / "tampered-index.json"
    tampered_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(calibration.CalibrationError):
        calibration.promote(
            profile_kind="flight", candidate_path=candidate_path, evidence_index_path=tampered_path,
            output_path=tmp_path / "out.json",
        )


def test_promote_refuses_holdout_battery_consumption_beyond_the_candidate_bound(tmp_path):
    candidate_path, _ = _fit_candidate(tmp_path)
    holdout_index_path = _holdout_index(tmp_path, forced_return_kwargs={"before": 80, "after": 20})
    with pytest.raises(calibration.CalibrationError):
        calibration.promote(
            profile_kind="flight", candidate_path=candidate_path, evidence_index_path=holdout_index_path,
            output_path=tmp_path / "out.json",
        )


def test_promote_refuses_holdout_pose_drift_beyond_the_candidate_envelope(tmp_path):
    candidate_path, _ = _fit_candidate(tmp_path)
    holdout_index_path = _holdout_index(tmp_path, tag_free_kwargs={"drift_cm": 6.0})
    with pytest.raises(calibration.CalibrationError):
        calibration.promote(
            profile_kind="flight", candidate_path=candidate_path, evidence_index_path=holdout_index_path,
            output_path=tmp_path / "out.json",
        )


def test_promote_refuses_a_holdout_combination_never_sampled_during_training(tmp_path):
    candidate_path, _ = _fit_candidate(tmp_path)
    # A different clock -> a different (time_s) leg for the tag-free hover -> the
    # envelope simply has no entry for this combination, which must fail closed.
    battery_manifest, battery_digest = _write_manifest(
        tmp_path, trial_role="holdout", split_id="holdout-battery-1", name="holdout-battery.json"
    )
    calibration.record(
        manifest_path=battery_manifest, authorization_digest=battery_digest, route_path=RETURN_ROUTE,
        executor_factory=_return_executor, clock=_fake_clock(RETURN_ROUTE_CLOCK),
    )
    pose_manifest, pose_digest = _write_manifest(
        tmp_path, trial_role="holdout", split_id="holdout-pose-1", name="holdout-pose.json"
    )
    calibration.record(
        manifest_path=pose_manifest, authorization_digest=pose_digest, route_path=TAG_FREE_ROUTE,
        executor_factory=_tag_free_executor, clock=_fake_clock((0.0, 0.0, 6.0, 6.0)),
    )
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps({
        "schema_version": "room-calibration-evidence-index-v1",
        "entries": [
            {"manifest_path": str(battery_manifest), "trial_path": str(tmp_path / "holdout-battery.trial.json"), "role": "holdout"},
            {"manifest_path": str(pose_manifest), "trial_path": str(tmp_path / "holdout-pose.trial.json"), "role": "holdout"},
        ],
    }), encoding="utf-8")
    with pytest.raises(calibration.CalibrationError):
        calibration.promote(
            profile_kind="flight", candidate_path=candidate_path, evidence_index_path=index_path,
            output_path=tmp_path / "out.json",
        )


# ---------------------------------------------------------------------------
# JSON Schema
# ---------------------------------------------------------------------------

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs" / "superpowers" / "specs" / "contracts" / "room-calibration-profile-v1.schema.json"
)


def test_promoted_profile_validates_against_the_schema(tmp_path):
    from jsonschema import Draft202012Validator

    candidate_path, _ = _fit_candidate(tmp_path)
    holdout_index_path = _holdout_index(tmp_path)
    promoted = calibration.promote(
        profile_kind="flight", candidate_path=candidate_path, evidence_index_path=holdout_index_path,
        output_path=tmp_path / "promoted.json",
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
    promoted_path = tmp_path / "promoted.json"
    exit_code = cli.main([
        "promote", "--profile-kind", "flight", "--evidence-index", str(holdout_index_path),
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
