from dataclasses import is_dataclass
import json
import pytest

from sentinel.mission_observer import (
    AbortEvent,
    DetectorResultEvent,
    FrameCapturedEvent,
    MergeEvent,
    NoOpMissionObserver,
    PoseEstimateEvent,
    PoseHealthEvent,
    PublicEscalationEvent,
    ReportEvent,
    RunnerLifecycleEvent,
    StopProgressEvent,
    notify,
    pose_summary,
    evidence_summary,
)
from sentinel.ui.safety import SafetyRegistry
from sentinel.ui.runtime import RuntimeMissionObserver
from sentinel.ui.store import UIStore
from sentinel.ui.scenarios import build_rehearsal_adapters
from sentinel.ui.approval import ApprovalBroker
from sentinel.ui.runtime import SurveyRuntime
from pathlib import Path
import json


def test_mission_events_are_explicit_typed_payloads():
    event_types = (
        RunnerLifecycleEvent,
        StopProgressEvent,
        PoseEstimateEvent,
        PoseHealthEvent,
        FrameCapturedEvent,
        DetectorResultEvent,
        MergeEvent,
        ReportEvent,
        AbortEvent,
        PublicEscalationEvent,
    )
    assert all(is_dataclass(event_type) for event_type in event_types)


def test_noop_and_failing_observers_never_break_the_publisher():
    event = AbortEvent(run_id="run-1", stage="FLIGHT", reason="operator stop")
    notify(NoOpMissionObserver(), event)

    class BrokenDisplay:
        def observe(self, observed):
            raise RuntimeError(f"display failed for {observed!r}")

    notify(BrokenDisplay(), event)


def test_runner_lifecycle_observer_binds_and_unbinds_safety_registry():
    class Runner:
        def __init__(self):
            self.latched = False

        def latch_emergency(self):
            self.latched = True

    registry = SafetyRegistry()
    runner = Runner()
    registry.runner_started("r", runner)
    assert registry.get("r") is runner
    registry.runner_finished("r", runner)
    assert registry.get("r") is None


def test_safety_admission_refuses_latched_closing_and_duplicate_runners():
    class Runner:
        def latch_emergency(self):
            self.latched = True

    from sentinel.client import EmergencyAborted
    for setup in ("emergency", "closing"):
        registry = SafetyRegistry()
        if setup == "emergency":
            registry.emergency.set()
        else:
            registry.closing = True
        with pytest.raises(EmergencyAborted):
            registry.runner_started("r", Runner())
    registry = SafetyRegistry()
    first, second = Runner(), Runner()
    registry.runner_started("r", first)
    with pytest.raises(RuntimeError, match="RUNNER_ALREADY_ACTIVE"):
        registry.runner_started("r", second)


def test_pose_event_metadata_is_allowlisted_and_excludes_private_encoded_image():
    class Evidence:
        artifact_id = "a"
        sha256 = "b"
        width_px = 640
        height_px = 480
        tags = (type("Tag", (), {"tag_id": 4, "name": "T4", "distance_m": 1.2,
                                  "corners_xy_normalized": ((.1, .2),)})(),)
        private_path = "C:/private/pose.png"
    class Estimate:
        report_id = "report"
        run_id = "run"
        point = (1.0, 2.0)
        heading_deg = 90.0
        method = "PNP"
        reprojection_error_px = .5
        evidence = Evidence()
        encoded_png = b"PRIVATE-BYTES"
        provider_secret = "do-not-leak"
    pose = pose_summary(Estimate())
    evidence = evidence_summary(Estimate.evidence)
    encoded = json.dumps({"pose": pose, "evidence": evidence})
    assert set(pose) == {"report_id", "run_id", "point", "heading_deg", "method", "reprojection_error_px"}
    assert set(evidence) == {"artifact_id", "sha256", "width_px", "height_px", "tags"}
    assert "PRIVATE" not in encoded and "private" not in encoded and "secret" not in encoded


def test_pose_observer_keeps_exact_estimate_object_for_evidence_adapters():
    seen = []
    estimate = object()

    class Observer:
        def observe(self, event):
            seen.append(event)

    notify(Observer(), PoseEstimateEvent(run_id="r", estimate=estimate))
    assert seen[0].estimate is estimate


def test_runtime_normalizes_stop_payload_and_never_journals_private_frame_paths(tmp_path):
    ui = UIStore.open(tmp_path / "ui.sqlite")
    ui.ensure_run("s", "r", "REHEARSAL")
    adapters = build_rehearsal_adapters(Path("fixtures/mission-control/clean-computer.json"), tmp_path / "images")
    runtime = SurveyRuntime(tmp_path / "survey.sqlite", ui, adapters, ApprovalBroker(ui), SafetyRegistry())
    RuntimeMissionObserver(runtime, "r").observe(StopProgressEvent(
        run_id="r", stop="RAIL_2", status="LOCALISED", option_id="RAIL",
        stop_index=2, x_cm=11, y_cm=22,
        payload={"waypoint": "RAIL_2", "commanded_cm": [11, 22, 33],
                 "frames": [{"path": "C:/private/secret.png"}]}))
    events = ui.read_events("r", 0, 10)
    payload = events[0]
    assert payload["type"] == "STOP_ARRIVED"
    assert "private" not in json.dumps(payload)
    assert runtime.state["r"]["stop"] == {"option_id": "RAIL", "stop_index": 2, "x_cm": 11, "y_cm": 22}
