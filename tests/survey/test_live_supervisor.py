import json
from dataclasses import replace
from pathlib import Path

from sentinel.survey.flight import FlightFrame
from sentinel.survey.live_detect import DetectionCache, LiveDetectionSupervisor, detect_frame
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


class _ReapedAfterTreeKill(_Process):
    def __init__(self):
        super().__init__()
        self.pid = 12345
        self.terminal = False

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if timeout is not None and not self.terminal:
            import subprocess
            raise subprocess.TimeoutExpired("follower", timeout)
        return 0

    def poll(self):
        return 0 if self.terminal else None


def _detection(**extra):
    result = {"class": "computer", "matched_prompt": "laptop", "raw_score": None,
              "score_kind": "VLM_CONFIDENCE_BAND", "bbox": None,
              "bbox_coordinate_space": "PIXELS_TOP_LEFT", "description": "laptop",
              "provider_item_id": "item-1"}
    result.update(extra)
    return result


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
            "status": "OK", "detections": [{**_detection(), "band": "high"}],
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
    assert process.wait_calls == [0.01, 0.01, 0.01]
    assert cache.process_status == "FATAL_CLEANUP_UNCONFIRMED"
    assert cache.for_frame(_frame(tmp_path / "f.png")).status == "ANALYSIS_GAP_PROCESS_FATAL_CLEANUP_UNCONFIRMED"


def test_supervisor_uses_injected_tree_terminator_and_confirms_terminal_pid(tmp_path):
    process = _ReapedAfterTreeKill()
    terminated = []
    def terminate_tree(proc, timeout):
        terminated.append((proc.pid, timeout))
        proc.terminal = True
        return True
    supervisor = LiveDetectionSupervisor(
        root=tmp_path, run_id="r", detector_name="gemini", launcher=lambda command: process,
        tree_terminator=terminate_tree, script=tmp_path / "live_detect.py", drain_timeout_s=0.01,
    )
    supervisor.start()
    cache = supervisor.finish()
    assert terminated == [(12345, 0.01)]
    assert process.poll() == 0 and cache.process_status == "TIMEOUT"


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


def test_cache_rejects_missing_or_extra_detection_fields_without_sightings(tmp_path):
    frame = _frame(tmp_path / "f.png")
    base = {"run_id": "r", "sweep_id": "s", "path": frame.image_path,
            "sha256": frame.sha256, "status": "OK", "dropped": [], "raw": {}}
    for detection in ({**_detection(), "unexpected": 1}, {"class": "computer"}):
        cache = DetectionCache("r", [{**base, "detections": [detection]}],
                               sweep_id="s", strict=True)
        assert cache.for_frame(frame).status == "ANALYSIS_GAP_INVALID_RESULT"


def test_cache_rejects_malformed_tile_provenance_and_latency(tmp_path):
    frame = _frame(tmp_path / "f.png")
    base = {"run_id": "r", "sweep_id": "s", "path": frame.image_path,
            "sha256": frame.sha256, "status": "OK", "detections": [_detection()],
            "dropped": []}
    for raw, detect_s in (({"tile_only": "not-a-list"}, 0.1),
                          ({"tile_only": [{"index": 0}]}, 0.1),
                          ({}, "nan"), ({}, float("inf"))):
        cache = DetectionCache("r", [{**base, "raw": raw, "detect_s": detect_s}],
                               sweep_id="s", strict=True)
        assert cache.for_frame(frame).status == "ANALYSIS_GAP_INVALID_RESULT"


def test_detect_frame_rejects_detection_band_length_mismatch():
    from sentinel.survey.detector import DetectionBatch
    class Mismatched:
        def detect(self, path, classes):
            return DetectionBatch((_detection(),), "OK", bands=())
    row = detect_frame(Mismatched(), {"path": "frame.png", "stop": "S"})
    assert row["status"] == "DETECTOR_ERROR"
    assert row["detections"] == [] and "bands" in row["error"]
def test_live_cache_is_the_only_ingestion_source_and_publishes_latency_states(tmp_path):
    class Flight:
        def __init__(self):
            frame = _frame(tmp_path / "f.png")
            self.cache = DetectionCache("r", [{
                "run_id": "r", "sweep_id": "s", "path": frame.image_path,
                "sha256": frame.sha256, "status": "OK",
                "detections": [{**_detection(), "band": "high", "tile": "r1c1"}],
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


def test_invalid_cached_result_is_an_analysis_gap_with_zero_sightings(tmp_path):
    frame = _frame(tmp_path / "f.png")
    cache = DetectionCache("r", [{
        "run_id": "r", "sweep_id": "s", "path": frame.image_path,
        "sha256": frame.sha256, "status": "OK", "detections": [{"class": "computer"}],
        "dropped": [], "raw": {},
    }], sweep_id="s", strict=True)

    class Flight:
        def fly(self, option):
            return [frame]
        def detection_for(self, frame, classes):
            return cache.for_frame(frame, classes)

    class MustNotCall:
        def detect(self, *_args):
            raise AssertionError("invalid cache must not fall back to provider")

    store = SurveyStore.open(tmp_path / "survey.sqlite")
    outcome = run_survey(
        store=store, run_id="r", request_text="count computers", options=OPTIONS,
        policy=RuleProposer(), flight=Flight(), detector=MustNotCall(),
        on_approval=lambda _call: True, writer=_writer([]))
    assert outcome["status"] == "REPORTED"
    assert store.unanalysed_count("r") == 1
    assert store.sightings("r") == []


def test_live_flight_close_retries_all_pending_supervisors(tmp_path):
    from sentinel.ui.live import LiveFlight
    class Supervisor:
        def __init__(self, pending):
            self.pending = pending
            self.calls = 0
        def close(self):
            self.calls += 1
            self.pending = False
            return True
    first, second = Supervisor(True), Supervisor(False)
    flight = LiveFlight.__new__(LiveFlight)
    flight._supervisors = [first, second]
    flight._supervisor = second
    flight.close()
    assert first.calls == second.calls == 1
    assert flight._supervisors == []
