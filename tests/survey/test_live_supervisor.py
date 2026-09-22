import json
from dataclasses import replace
from pathlib import Path

from sentinel.survey.flight import FlightFrame
from sentinel.survey.live_detect import DetectionCache, LiveDetectionSupervisor
from sentinel.survey.loop import run_survey
from sentinel.survey.proposer import RuleProposer
from sentinel.survey.store import SurveyStore
from sentinel.mission_observer import DetectorResultEvent
from tests.survey.test_loop import OPTIONS, _writer


def _frame(path, sha="a" * 64):
    return FlightFrame(0, 0, 1, 2, 3, 0, "COMMANDED", str(path), "artifact:r:0", sha)


class _Process:
    def __init__(self, on_wait=None):
        self.on_wait = on_wait
        self.wait_calls = []
        self.killed = False

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if self.on_wait:
            self.on_wait()
        return 0

    def kill(self):
        self.killed = True


class _TimedOutProcess(_Process):
    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if timeout is not None:
            import subprocess
            raise subprocess.TimeoutExpired("follower", timeout)
        return -9


def test_supervisor_starts_before_flight_and_consumes_exact_cached_batch(tmp_path):
    started = []
    frame_path = tmp_path / "frames" / "f.png"
    frame_path.parent.mkdir()
    frame_path.write_bytes(b"frame")
    frame = _frame(frame_path)

    def launch(command):
        started.append(command)
        process = _Process()
        out = Path(command[command.index("--out") + 1])
        out.write_text(json.dumps({
            "run_id": "r", "sweep_id": command[command.index("--sweep-id") + 1],
            "path": str(frame_path), "sha256": frame.sha256,
            "status": "OK", "detections": [{"class": "computer", "band": "high"}],
            "dropped": [], "raw": {"provider": "fake"}, "detect_s": 0.012,
        }) + "\n")
        return process

    supervisor = LiveDetectionSupervisor(
        root=tmp_path, run_id="r", detector_name="gemini", launcher=launch,
        script=tmp_path / "live_detect.py", drain_timeout_s=1,
    )
    supervisor.start()
    assert started
    cache = supervisor.finish([frame])
    batch = cache.for_frame(frame, ("computer",))
    assert batch.status == "OK"
    assert batch.detections[0]["class"] == "computer"
    assert batch.raw["provider"] == "fake"
    assert cache.for_frame(frame, ("computer",)) == batch


def test_cache_rejects_hash_mismatch_and_missing_rows_as_analysis_gaps(tmp_path):
    frame_path = tmp_path / "f.png"
    frame = _frame(frame_path)
    cache = DetectionCache(
        run_id="r",
        rows=[{"run_id": "r", "path": str(frame_path), "sha256": "b" * 64,
               "status": "OK", "detections": [], "dropped": [], "raw": {}}],
        process_status="OK",
    )
    assert cache.for_frame(frame, ("computer",)).status == "ANALYSIS_GAP_HASH_MISMATCH"
    other = replace(frame, image_path=str(tmp_path / "other.png"))
    assert cache.for_frame(other, ("computer",)).status == "ANALYSIS_GAP_MISSING"


def test_supervisor_timeout_kills_and_reaps_without_failing_the_flight(tmp_path):
    process = _TimedOutProcess()
    supervisor = LiveDetectionSupervisor(
        root=tmp_path, run_id="r", detector_name="gemini", launcher=lambda command: process,
        script=tmp_path / "live_detect.py", drain_timeout_s=0.01,
    )
    supervisor.start()
    cache = supervisor.finish()
    assert process.killed
    assert process.wait_calls == [0.01, 0.01]
    assert cache.process_status == "TIMEOUT_UNREAPED"
    assert cache.for_frame(_frame(tmp_path / "f.png")).status == "ANALYSIS_GAP_PROCESS_TIMEOUT_UNREAPED"


def test_cache_rejects_an_exact_row_from_another_run(tmp_path):
    frame = _frame(tmp_path / "f.png")
    cache = DetectionCache("new", [{"run_id": "old", "path": frame.image_path,
                                    "sha256": frame.sha256, "status": "OK",
                                    "detections": [{"class": "computer"}]}])
    assert cache.for_frame(frame).status == "ANALYSIS_GAP_STALE"


def test_strict_cache_requires_both_run_and_sweep_ids(tmp_path):
    frame = _frame(tmp_path / "f.png")
    row = {"run_id": "r", "path": frame.image_path, "sha256": frame.sha256,
           "status": "OK", "detections": [], "sweep_id": "wrong"}
    cache = DetectionCache("r", [row], sweep_id="expected", strict=True)
    assert cache.for_frame(frame).status == "ANALYSIS_GAP_STALE"
    row.pop("sweep_id")
    cache = DetectionCache("r", [row], sweep_id="expected", strict=True)
    assert cache.for_frame(frame).status == "ANALYSIS_GAP_STALE"


def test_live_cache_is_the_only_ingestion_source_and_publishes_latency_states(tmp_path):
    class Flight:
        def __init__(self):
            frame = _frame(tmp_path / "f.png")
            self.cache = DetectionCache("r", [{
                "run_id": "r", "sweep_id": "s", "path": frame.image_path,
                "sha256": frame.sha256, "status": "OK",
                "detections": [{"class": "computer", "band": "high",
                                "bbox": None, "score_kind": "NONE"}],
                "bands": ["high"], "raw": {"detect_s": 0.025},
            }], sweep_id="s", strict=True)

        def fly(self, option):
            return [_frame(tmp_path / "f.png")]

        def detection_for(self, frame, classes):
            return self.cache.for_frame(frame, classes)

    class ProviderMustNotBeCalled:
        def detect(self, *_args):
            raise AssertionError("live ingestion called the provider a second time")

    events = []
    class Observer:
        def observe(self, event):
            events.append(event)

    store = SurveyStore.open(tmp_path / "survey.sqlite")
    seen = []
    original_add_sighting = store.add_sighting
    def add_sighting(frame_id, detection, band, **kwargs):
        seen.append(detection)
        return original_add_sighting(frame_id, detection, band, **kwargs)
    store.add_sighting = add_sighting
    outcome = run_survey(
        store=store, run_id="r", request_text="count computers", options=OPTIONS,
        policy=RuleProposer(), flight=Flight(), detector=ProviderMustNotBeCalled(),
        on_approval=lambda _call: True, writer=_writer([]), observer=Observer())
    assert outcome["status"] == "REPORTED"
    detector_events = [event for event in events if isinstance(event, DetectorResultEvent)]
    assert [event.status for event in detector_events] == ["DETECTING", "OK"]
    assert detector_events[-1].latency_ms == 25.0
    assert "band" not in seen[0]
