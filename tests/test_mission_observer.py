from dataclasses import is_dataclass

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
)
from sentinel.ui.safety import SafetyRegistry


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
    registry.observe(RunnerLifecycleEvent(run_id="r", status="STARTED", runner=runner))
    assert registry.get("r") is runner
    registry.observe(RunnerLifecycleEvent(run_id="r", status="PASS", runner=runner))
    assert registry.get("r") is None


def test_pose_observer_keeps_exact_estimate_object_for_evidence_adapters():
    seen = []
    estimate = object()

    class Observer:
        def observe(self, event):
            seen.append(event)

    notify(Observer(), PoseEstimateEvent(run_id="r", estimate=estimate))
    assert seen[0].estimate is estimate
