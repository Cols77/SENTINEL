"""Tests for src/sentinel/room/wiring.py (Plan 06 Task 4: default production
factories for RoomController's injected observation collaborators).

Every test here builds and calls these factories with fakes only. The tests
on the *default* (production) construction path assert the opposite of what
they sound like: that building a default collaborator constructs nothing
real merely by being built, and that its one real call site fails loudly and
safely (never silently reaching a real client) when the credentials/session
it would need are absent.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from sentinel.cube.checkpoint import CommandLimits
from sentinel.cube.localization import LocalizationTracker
from sentinel.models import Position, VideoFrame
from sentinel.room import calibration, wiring
from sentinel.room.ingest import capture_request, validate_request
from sentinel.room.models import CapabilityProfile
from sentinel.room.semantic import resolve_pose_id
from sentinel.room.store import RoomStore
from sentinel.survey.detector import DetectionBatch
from tests.room.test_count_return import room_site

POSE_ID = "left-rear-bottom-toward_targets"


def _limits(**overrides):
    return CommandLimits(**overrides)


def _profile(speeds=(30, 20)):
    return calibration.CalibrationProfile(
        schema_version=calibration.SCHEMA_VERSION, kind="flight", state="PROMOTED",
        site_sha256="0" * 64, aircraft_id="aircraft-1", firmware_version="1.0.0",
        camera_sha256="0" * 64, tag_map_sha256="0" * 64, speeds_cm_s=speeds,
        battery=None, pose_envelope=(), training_run_ids=("t",), holdout_run_ids=("h",),
    )


def _candidate(pose_id=POSE_ID, objective="g1"):
    return SimpleNamespace(candidate_id=f"OBSERVE:{objective}:{pose_id}")


# ---------------------------------------------------------------------------
# make_resolve_checkpoint
# ---------------------------------------------------------------------------


def test_resolve_checkpoint_targets_the_resolved_pose_at_its_declared_heading():
    site = room_site(coverage=True)
    resolved = resolve_pose_id(site, POSE_ID)
    pose = SimpleNamespace(position=Position(0, 0, 100), heading_deg=45.0)
    resolve_checkpoint = wiring.make_resolve_checkpoint(
        site=site, profile=_profile(), pose_source=lambda: pose, limits=_limits(),
    )
    plan = resolve_checkpoint(_candidate(), snapshot=None)
    assert plan.target == Position(resolved.x_cm, resolved.y_cm, resolved.z_cm)
    assert plan.terminal_heading_deg == pytest.approx(float(resolved.yaw_deg))
    assert plan.start == pose.position
    assert plan.start_heading_deg == pose.heading_deg
    assert any(command.kind == "CAPTURE" for command in plan.commands)


def test_resolve_checkpoint_uses_the_slowest_validated_calibration_speed():
    site = room_site(coverage=True)
    pose = SimpleNamespace(position=Position(0, 0, 100), heading_deg=90.0)
    resolve_checkpoint = wiring.make_resolve_checkpoint(
        site=site, profile=_profile(speeds=(30, 20, 50)), pose_source=lambda: pose, limits=_limits(),
    )
    plan = resolve_checkpoint(_candidate(), snapshot=None)
    speeds = {dict(command.arguments)["speed_cm_s"] for command in plan.commands if command.kind == "SET_SPEED"}
    assert speeds == {20}


def test_resolve_checkpoint_reads_a_fresh_pose_on_every_call():
    site = room_site(coverage=True)
    poses = [SimpleNamespace(position=Position(0, 0, 100), heading_deg=90.0),
             SimpleNamespace(position=Position(10, 10, 100), heading_deg=90.0)]
    resolve_checkpoint = wiring.make_resolve_checkpoint(
        site=site, profile=_profile(), pose_source=poses.pop, limits=_limits(),
    )
    first = resolve_checkpoint(_candidate(), snapshot=None)
    second = resolve_checkpoint(_candidate(), snapshot=None)
    assert first.start != second.start


def test_resolve_checkpoint_refuses_eagerly_without_a_validated_speed():
    site = room_site(coverage=True)
    with pytest.raises(wiring.WiringError):
        wiring.make_resolve_checkpoint(
            site=site, profile=_profile(speeds=()), pose_source=lambda: None, limits=_limits(),
        )


def test_resolve_checkpoint_refuses_a_pose_id_that_does_not_resolve():
    site = room_site(coverage=True)
    pose = SimpleNamespace(position=Position(0, 0, 100), heading_deg=90.0)
    resolve_checkpoint = wiring.make_resolve_checkpoint(
        site=site, profile=_profile(), pose_source=lambda: pose, limits=_limits(),
    )
    with pytest.raises(wiring.WiringError):
        resolve_checkpoint(_candidate(pose_id="bogus-pose-id-x"), snapshot=None)


# ---------------------------------------------------------------------------
# make_resolve_checkpoint: fix round 1 (Q1) -- translations must land on the
# declared target regardless of the resolved pose's own yaw, and (Q3) must
# use the injected CommandLimits unchanged, never a fresh default.
# ---------------------------------------------------------------------------


def _propagate(plan):
    """Replay *plan*'s commands through a bare LocalizationTracker, the same
    way SafetySupervisor/the real flight loop would, and return the position
    the aircraft actually ends up at."""
    from dataclasses import replace as _replace

    tracker = LocalizationTracker(None, clock=lambda: 0.0)
    health = _replace(tracker.health, position=plan.start, heading_deg=plan.start_heading_deg)
    for command in plan.commands:
        health = tracker._command(health, command)
    return health


@pytest.mark.parametrize("pose_id", [
    "left-rear-bottom-right",       # yaw 0
    "left-rear-bottom-toward_targets",  # yaw 90 (the canonical default)
    "left-rear-bottom-left",        # yaw -180 / 180
])
def test_resolve_checkpoint_reaches_the_declared_target_position_at_every_yaw(pose_id):
    site = room_site(coverage=True)
    resolved = resolve_pose_id(site, pose_id)
    assert resolved is not None
    pose = SimpleNamespace(position=Position(0, 0, 100), heading_deg=90.0)
    resolve_checkpoint = wiring.make_resolve_checkpoint(
        site=site, profile=_profile(), pose_source=lambda: pose, limits=_limits(),
    )
    plan = resolve_checkpoint(_candidate(pose_id=pose_id), snapshot=None)
    reached = _propagate(plan)
    target = Position(resolved.x_cm, resolved.y_cm, resolved.z_cm)
    assert reached.position.x_cm == pytest.approx(target.x_cm, abs=1e-6)
    assert reached.position.y_cm == pytest.approx(target.y_cm, abs=1e-6)
    assert reached.position.z_cm == pytest.approx(target.z_cm, abs=1e-6)
    assert reached.heading_deg % 360 == pytest.approx(float(resolved.yaw_deg) % 360, abs=1e-6)


def test_resolve_checkpoint_uses_the_injected_limits_unchanged():
    # A fresh default CommandLimits() has trim_rounds=2, which
    # SafetySupervisor._path_clear always refuses (fix round 1, Q3): the
    # production wiring must plan against whatever CommandLimits it is
    # actually given, never silently substitute its own default.
    site = room_site(coverage=True)
    pose = SimpleNamespace(position=Position(0, 0, 100), heading_deg=90.0)
    injected = _limits(trim_rounds=0, localise=False)
    resolve_checkpoint = wiring.make_resolve_checkpoint(
        site=site, profile=_profile(), pose_source=lambda: pose, limits=injected,
    )
    plan = resolve_checkpoint(_candidate(pose_id="left-rear-bottom-right"), snapshot=None)
    trims = [dict(c.arguments) for c in plan.commands if c.kind == "TRIM_ALLOWANCE"]
    assert trims and trims[0]["max_rounds"] == 0
    assert not any(c.kind == "LOCALISE" for c in plan.commands)


def test_resolve_checkpoint_center_pose_also_reaches_its_declared_target():
    # A second, independent (position, yaw) combination from Q1's own probe
    # (center-middle-middle-right), pinned separately from the parametrized
    # left-rear-bottom cases above.
    site = room_site(coverage=True)
    resolved = resolve_pose_id(site, "center-middle-middle-right")  # yaw 0
    pose = SimpleNamespace(position=Position(0, 0, 100), heading_deg=90.0)
    resolve_checkpoint = wiring.make_resolve_checkpoint(
        site=site, profile=_profile(), pose_source=lambda: pose, limits=_limits(),
    )
    plan = resolve_checkpoint(_candidate(pose_id="center-middle-middle-right"), snapshot=None)
    reached = _propagate(plan)
    assert reached.position.x_cm == pytest.approx(resolved.x_cm, abs=1e-6)
    assert reached.position.y_cm == pytest.approx(resolved.y_cm, abs=1e-6)


# ---------------------------------------------------------------------------
# make_perception_capture
# ---------------------------------------------------------------------------


class FakeFlightClient:
    def __init__(self, frame):
        self.frame = frame
        self.calls = []

    def read_frame(self, timeout=None):
        self.calls.append(timeout)
        return self.frame


def _small_frame():
    # 2x2 rgb24 pixels: 12 bytes, deterministic content.
    data = bytes(range(12))
    return VideoFrame(width=2, height=2, data=data, pixel_format="rgb24")


def _session(tmp_path):
    store = RoomStore.open(tmp_path / "room.sqlite3")
    store.create_session("session", "count and return", CapabilityProfile(()))
    return store


def test_perception_capture_writes_a_valid_correlated_request(tmp_path):
    store = _session(tmp_path)
    client = FakeFlightClient(_small_frame())
    capture = wiring.make_perception_capture(
        store=store, flight_client=client, frames_dir=tmp_path / "frames",
        requested_model="test-model-v1", run_id="run-1",
    )
    snapshot = SimpleNamespace(session_id="session", pose_revision=7)
    request = capture(_candidate(), None, snapshot)
    validate_request(request)  # raises on any malformed/incorrectly-hashed request
    assert Path(request.frame_path).exists()
    assert request.pose_revision_id == "7"
    assert request.requested_model == "test-model-v1"
    assert request.action_id == _candidate().candidate_id
    assert client.calls == [10.0]


def test_perception_capture_reads_the_session_version_fresh_not_stale(tmp_path):
    store = _session(tmp_path)
    client = FakeFlightClient(_small_frame())
    capture = wiring.make_perception_capture(
        store=store, flight_client=client, frames_dir=tmp_path / "frames",
        requested_model="test-model-v1", run_id="run-1",
    )
    # Advance the session's version *after* a caller might have built a stale
    # snapshot, simulating the real controller's post-dispatch capture timing
    # (fix round 1, Q4/S1).
    store.append_event(session_id="session", expected_version=0, event_type="DRONE_STATE_RECORDED",
                       payload={"observed_at": "2026-09-23T00:00:00+00:00", "scope": "aircraft:fixture",
                                "uncertainty": "u", "state": {"battery_percent": 80, "flight_state": "LANDED"}},
                       correlation_id="c", causation_id=None, idempotency_key="k")
    stale_snapshot = SimpleNamespace(session_id="session", pose_revision=1, state_version=0)
    request = capture(_candidate(), None, stale_snapshot)
    assert request.snapshot_version == store.session_version("session")
    assert request.snapshot_version != stale_snapshot.state_version


def test_perception_capture_respects_frame_timeout(tmp_path):
    store = _session(tmp_path)
    client = FakeFlightClient(_small_frame())
    capture = wiring.make_perception_capture(
        store=store, flight_client=client, frames_dir=tmp_path / "frames",
        requested_model="test-model-v1", run_id="run-1", frame_timeout_s=2.5,
    )
    capture(_candidate(), None, SimpleNamespace(session_id="session", pose_revision=0))
    assert client.calls == [2.5]


# ---------------------------------------------------------------------------
# make_perception_provider
# ---------------------------------------------------------------------------


class FakeDetector:
    provider = "fake-provider"

    def __init__(self, batch):
        self.batch = batch
        self.calls = []

    def detect(self, image_path, classes):
        self.calls.append((image_path, tuple(classes)))
        return self.batch


def _detection_batch(model="test-model-v1"):
    return DetectionBatch(
        detections=({"class": "computer", "bbox": {"x": 1, "y": 2, "width": 10, "height": 10},
                     "raw_score": None, "score_kind": None},),
        status="OK", bands=("high",), dropped=(), raw={"model": model},
    )


def _write_png(path, size=(50, 50)):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (10, 20, 30)).save(path)


def _request(tmp_path, *, model="test-model-v1"):
    frame_path = tmp_path / "frame.png"
    _write_png(frame_path)
    return capture_request(
        {"session_id": "session", "action_id": "OBSERVE:g1:pose", "snapshot_version": 3,
         "pose_revision_id": "1", "requested_model": model},
        frame_path=frame_path, run_id="run-1", captured_at="2026-09-23T00:00:00+00:00",
        classes=("computer",),
    )


def test_perception_provider_builds_the_detector_lazily_and_caches_it(tmp_path):
    built = []
    provide = wiring.make_perception_provider(
        classes=("computer",),
        detector_factory=lambda: built.append(1) or FakeDetector(_detection_batch()),
    )
    assert built == []  # never constructed merely by building the collaborator
    request = _request(tmp_path)
    provide(request)
    provide(_request(tmp_path))
    assert len(built) == 1  # cached, not rebuilt per call


def test_perception_provider_returns_a_validated_correlated_result(tmp_path):
    detector = FakeDetector(_detection_batch())
    provide = wiring.make_perception_provider(classes=("computer",), detector_factory=lambda: detector)
    request = _request(tmp_path)
    result = provide(request)
    assert result.status == "SUCCEEDED"
    assert result.request == request
    assert [d.class_name for d in result.detections] == ["computer"]
    assert detector.calls == [(request.frame_path, ("computer",))]


def test_perception_provider_refuses_when_no_correlated_result_comes_back(tmp_path):
    detector = FakeDetector(_detection_batch())
    provide = wiring.make_perception_provider(classes=("computer",), detector_factory=lambda: detector)
    request = _request(tmp_path)
    tampered = request.__class__(**{**request.__dict__, "frame_sha256": "1" * 64})
    with pytest.raises(wiring.WiringError):
        provide(tampered)


def test_perception_provider_default_construction_touches_nothing_real(monkeypatch):
    def _forbidden(*args, **kwargs):
        raise AssertionError("must not build a real detector merely by constructing perception_provider")

    monkeypatch.setattr("scripts.survey_run.build_provider_detector", _forbidden)
    wiring.make_perception_provider()  # must not call _forbidden


def test_perception_provider_default_fails_closed_without_credentials(tmp_path):
    provide = wiring.make_perception_provider(environ={})
    request = _request(tmp_path)
    with pytest.raises(RuntimeError):
        provide(request)
