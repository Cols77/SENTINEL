"""Live per-stop detection: the runner announces each saved frame, a separate
follower detects on it while the flight goes on, and the gates refuse before
takeoff. No network, no drone."""
import json
import sys
from pathlib import Path

import pytest

from sentinel.cube.mission import FRAMES_LOG_NAME, CubeMissionRunner, Mission, Waypoint
from sentinel.cube.site import parse_site
from sentinel.dry_run import DryRunClient
from sentinel.survey.consent import record_consent
from sentinel.survey.detector import DetectionBatch
from sentinel.survey.live_detect import (
    LiveDetectRefused, check_gates, detect_frame, follow, summarise,
)
from sentinel.survey.store import SurveyStore

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import cube_trial  # noqa: E402
import live_detect as live_detect_script  # noqa: E402

SITE = parse_site({
    "schema_version": "cube-site-v1", "line_y_m": 5.0,
    "stands": [{"x_m": x, "first_tag_id": 2 * i, "heights_m": [0.90, 1.45], "size_m": 0.192}
               for i, x in enumerate([0.25, 0.75, 2.3, 2.7, 3.1, 4.65, 5.15])],
    "cube": {"min_x_m": 0.3, "max_x_m": 5.1, "min_y_m": 2.0, "max_y_m": 4.2, "max_z_m": 2.0},
    "commanded": {"inset_m": 0.3, "min_z_cm": 80, "max_z_cm": 180},
})
MISSION = Mission(270, 250, (Waypoint("NEAR", 270, 300, 100),
                             Waypoint("WIDE", 150, 300, 140, looks_deg=(-45, 0, 45))))


class FakeRecorder:
    """Stands in for FrameRecorder: a dry-run frame is too small to be a PNG."""

    def __init__(self, directory: Path):
        self.directory, self.count = directory, 0
        directory.mkdir(parents=True, exist_ok=True)

    def save(self, frame) -> Path:
        path = self.directory / f"{self.count:06d}.png"
        path.write_bytes(b"frame %d" % self.count)
        self.count += 1
        return path


def _fly(tmp_path, recorder):
    runner = CubeMissionRunner(DryRunClient(), "dry-run", mission=MISSION, site=SITE,
                               mission_log=tmp_path / "mission.jsonl", run_id="RUN",
                               sleep_fn=lambda seconds: None)
    runner.recorder = recorder
    assert runner.run(confirm=True).status == "PASS"


def test_the_runner_announces_each_saved_frame_as_it_is_saved(tmp_path):
    _fly(tmp_path, FakeRecorder(tmp_path / "frames"))
    lines = [json.loads(line) for line in
             (tmp_path / "frames" / FRAMES_LOG_NAME).read_text().splitlines()]
    assert [(line["stop"], line["look_deg"]) for line in lines] == [
        ("NEAR", 0), ("WIDE", -45), ("WIDE", 0), ("WIDE", 45)]
    assert all(line["run_id"] == "RUN" and Path(line["path"]).exists() and line["sha256"]
               for line in lines)


def test_no_frame_is_announced_when_none_is_saved(tmp_path):
    """Dry run without capture: no recorder, no frames.jsonl, nothing to detect."""
    _fly(tmp_path, None)
    assert not list(tmp_path.rglob(FRAMES_LOG_NAME))


class FakeDetector:
    def __init__(self, batches=None, error=None):
        self.calls, self.batches, self.error = [], list(batches or []), error

    def detect(self, path, classes):
        self.calls.append((path, classes))
        if self.error:
            raise self.error
        return self.batches.pop(0) if self.batches else DetectionBatch((), "OK", raw={})


def _laptop():
    return DetectionBatch(({"class": "computer", "bbox": {"x": 1, "y": 2, "width": 3, "height": 4},
                            "description": "a laptop"},), "OK", bands=("high",), raw={"r": 1})


def _frames_log(tmp_path, stops):
    log = tmp_path / FRAMES_LOG_NAME
    log.write_text("".join(json.dumps({"stop": s, "look_deg": 0, "path": f"{s}.png",
                                       "sha256": "h"}) + "\n" for s in stops))
    return log


def test_the_follower_detects_each_frame_once_prints_a_line_and_keeps_a_row(tmp_path):
    detector, printed = FakeDetector([_laptop(), DetectionBatch((), "OK", raw={})]), []
    count = follow(_frames_log(tmp_path, ["NEAR", "FAR"]), tmp_path / "detections.jsonl",
                   detector, done=lambda: True, print_fn=printed.append, sleep_fn=lambda s: None)

    assert count == 2 and [call[0] for call in detector.calls] == ["NEAR.png", "FAR.png"]
    assert printed[0] == "[detect] NEAR: 1 computer (high), 0 pen, 0 person, 0 phone"
    rows = [json.loads(l) for l in (tmp_path / "detections.jsonl").read_text().splitlines()]
    assert rows[0]["detections"][0]["band"] == "high" and rows[0]["raw"] == {"r": 1}
    assert rows[1]["status"] == "OK" and rows[1]["detections"] == []


def test_the_follower_waits_for_frames_until_the_flight_is_done(tmp_path):
    log, polls = tmp_path / FRAMES_LOG_NAME, []

    def sleep(seconds):   # the flight saves its one frame after the first poll
        polls.append(seconds)
        if len(polls) == 1:
            log.write_text(json.dumps({"stop": "NEAR", "path": "a.png"}) + "\n")
    detector = FakeDetector()
    count = follow(log, tmp_path / "out.jsonl", detector, done=lambda: len(polls) >= 2,
                   print_fn=lambda line: None, sleep_fn=sleep)
    assert count == 1 and len(detector.calls) == 1


def test_a_half_written_line_is_left_for_the_next_poll(tmp_path):
    log = tmp_path / FRAMES_LOG_NAME
    log.write_text(json.dumps({"stop": "A", "path": "a.png"}) + "\n" + '{"stop": "B", "pa')
    detector = FakeDetector()
    follow(log, tmp_path / "out.jsonl", detector, done=lambda: True,
           print_fn=lambda line: None, sleep_fn=lambda s: None)
    assert [call[0] for call in detector.calls] == ["a.png"]


def test_a_detector_that_raises_is_a_row_not_a_stopped_follower(tmp_path):
    printed = []
    count = follow(_frames_log(tmp_path, ["NEAR", "FAR"]), tmp_path / "out.jsonl",
                   FakeDetector(error=TimeoutError("slow")), done=lambda: True,
                   print_fn=printed.append, sleep_fn=lambda s: None)
    assert count == 2
    assert printed[0].startswith("[detect] NEAR: DETECTOR_ERROR") and "slow" in printed[0]


def test_summary_names_a_look_offset_and_a_failed_frame():
    assert summarise("WIDE", -45, "OK", []) == \
        "[detect] WIDE look -45: 0 computer, 0 pen, 0 person, 0 phone"
    assert "PROVIDER_ERROR" in summarise("WIDE", 0, "PROVIDER_ERROR", [])
    assert detect_frame(FakeDetector(), {"stop": "S", "path": "p"})["status"] == "OK"


def _consent_db(tmp_path):
    db = tmp_path / "consent.sqlite3"
    store = SurveyStore.open(db)
    record_consent(store, "home-office", "2026-09-22", "operator")
    store.connection.close()
    return db


def test_the_gates_refuse_without_paid_authorisation_or_recorded_consent(tmp_path):
    db = _consent_db(tmp_path)
    with pytest.raises(LiveDetectRefused, match="paid"):
        check_gates(consent_db=db, location="home-office", date="2026-09-22", authorised=False)
    with pytest.raises(LiveDetectRefused):
        check_gates(consent_db=db, location="home-office", date="2026-09-23", authorised=True)
    with pytest.raises(LiveDetectRefused):
        check_gates(consent_db=None, location=None, date=None, authorised=True)
    check_gates(consent_db=db, location="home-office", date="2026-09-22", authorised=True)


def test_the_script_refuses_before_building_a_detector_without_consent(tmp_path, capsys):
    built = []
    code = live_detect_script.main(["--check-only", "--i-authorise-paid-calls"],
                                   build_detector=lambda name: built.append(name))
    assert code == 2 and built == [] and "refused" in capsys.readouterr().out


def test_cube_trial_with_detect_refuses_before_takeoff_when_the_gates_fail(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []
    code = cube_trial.main(["--dry-run", "--detect", "gemini-openrouter"],
                           run=lambda cmd, **kw: calls.append(cmd) or 2,
                           call=lambda cmd, **kw: calls.append(cmd) or 0,
                           popen=lambda cmd, **kw: calls.append(cmd))
    assert code == 2
    assert len(calls) == 1 and "--check-only" in calls[0]   # cube-run never started


class FakeFollower:
    def __init__(self):
        self.waited = None

    def wait(self, timeout):
        self.waited = timeout


def test_cube_trial_with_detect_captures_frames_and_runs_the_follower_beside_the_flight(
        tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls, follower = [], FakeFollower()
    code = cube_trial.main(
        ["--real", "--confirm", "--detect", "gemini-openrouter", "--consent-db", "c.sqlite3",
         "--location", "home-office", "--date", "2026-09-22", "--i-authorise-paid-calls"],
        run=lambda cmd, **kw: calls.append(("run", cmd)) or 0,
        call=lambda cmd, **kw: calls.append(("call", cmd)) or 0,
        popen=lambda cmd, **kw: calls.append(("popen", cmd)) or follower)

    assert code == 0
    kinds = [kind for kind, _ in calls]
    assert kinds[:3] == ["run", "popen", "call"]     # gate check, follower, then the flight
    flight = calls[2][1]
    assert "--capture-dir" in flight and flight[flight.index("--capture-dir") + 1].endswith("frames")
    assert "--i-authorise-paid-calls" in calls[1][1] and "--location=home-office" in calls[1][1]
    assert follower.waited == cube_trial.FOLLOWER_DRAIN_S
    assert list(Path("artifacts/trials").glob("*/flight.done"))   # the follower was told


def test_cube_trial_without_detect_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []
    cube_trial.main(["--dry-run"], run=lambda cmd, **kw: calls.append(("run", cmd)) or 0,
                    call=lambda cmd, **kw: calls.append(("call", cmd)) or 0,
                    popen=lambda cmd, **kw: pytest.fail("no follower without --detect"))
    flight = next(cmd for kind, cmd in calls if kind == "call")
    assert "--capture-dir" not in flight


def test_tiles_wrap_the_detector_and_reach_the_follower(tmp_path, monkeypatch, capsys):
    from sentinel.survey.store import SurveyStore
    from sentinel.survey.consent import record_consent
    from sentinel.survey.tiling import TiledDetector
    db = tmp_path / "c.sqlite3"
    record_consent(SurveyStore.open(db), "home-office", "2026-09-22", "test")
    built = []
    code = live_detect_script.main(
        ["--check-only", "--tiles", "2x2", "--consent-db", str(db), "--location", "home-office",
         "--date", "2026-09-22", "--i-authorise-paid-calls"],
        build_detector=lambda name: built.append(name) or object())
    assert code == 0 and "with 2x2 crops" in capsys.readouterr().out

    monkeypatch.chdir(tmp_path)
    calls = []
    cube_trial.main(
        ["--real", "--confirm", "--detect", "gemini-openrouter", "--detect-tiles", "2x2",
         "--consent-db", "c.sqlite3", "--location", "home-office", "--date", "2026-09-22",
         "--i-authorise-paid-calls"],
        run=lambda cmd, **kw: calls.append(cmd) or 0, call=lambda cmd, **kw: 0,
        popen=lambda cmd, **kw: calls.append(cmd) or FakeFollower())
    detect_calls = [cmd for cmd in calls if cube_trial.LIVE_DETECT in cmd]
    assert len(detect_calls) == 2 and all("--tiles=2x2" in cmd for cmd in detect_calls)


def test_a_find_only_a_crop_made_is_marked_in_the_row_and_the_line():
    from sentinel.survey.detector import DetectionBatch
    from sentinel.survey.live_detect import detect_frame, summarise

    class Tiled:
        def detect(self, path, classes):
            det = {"class": "phone", "bbox": {"x": 1, "y": 1, "width": 9, "height": 9}}
            return DetectionBatch((det,), "OK", ("medium",),
                                  raw={"tile_only": [{"index": 0, "tile": "r1c1", "class": "phone"}]})

    row = detect_frame(Tiled(), {"stop": "S", "look_deg": 0, "path": "x.png"})
    assert row["detections"][0]["tile"] == "r1c1"
    assert "1 phone (medium crop)" in summarise("S", 0, "OK", row["detections"])


def _correlated_record(tmp_path):
    from dataclasses import asdict
    from PIL import Image
    from sentinel.room.ingest import capture_request
    path = tmp_path / "correlated.png"
    Image.new("RGB", (640, 480)).save(path)
    request = capture_request(
        {"session_id": "s1", "action_id": "a1", "snapshot_version": 0,
         "pose_revision_id": "pose1", "requested_model": "pinned-model"},
        frame_path=path, run_id="RUN", captured_at="2026-09-22T00:00:00+00:00",
        classes=("computer",),
    )
    return {"stop": "NEAR", "look_deg": 0, "path": str(path), "sha256": request.frame_sha256,
            "captured_at": request.captured_at, "perception_request": asdict(request)}


def test_correlated_detection_preserves_contract_and_never_invents_a_score(tmp_path):
    from sentinel.room.ingest import result_from_dict
    record = _correlated_record(tmp_path)
    batch = DetectionBatch(_laptop().detections, "OK", ("high",),
                           raw={"provider": "fixture", "model": "pinned-model"})
    row = detect_frame(FakeDetector([batch]), record, classes=("computer",))
    result = result_from_dict(row["perception_result"])
    assert result.request.observation_id == record["perception_request"]["observation_id"]
    assert result.status == "SUCCEEDED" and result.identity_status == "VERIFIED"
    assert result.provider == "fixture" and result.resolved_model == "pinned-model"
    assert result.detections[0].raw_score is None and result.detections[0].confidence_band == "high"
    assert result.detections[0].frame_width_px == 640


@pytest.mark.parametrize("exception_name,category", [
    ("ProviderTimeout", "TIMEOUT"), ("ProviderAuthError", "AUTH"),
    ("ProviderTransportError", "TRANSPORT"),
])
def test_correlated_failure_is_typed_without_leaking_exception_text(tmp_path, exception_name, category):
    from sentinel.survey import providers
    row = detect_frame(FakeDetector(error=getattr(providers, exception_name)("api-key-secret")),
                       _correlated_record(tmp_path))
    result = row["perception_result"]
    assert result["status"] == "FAILED" and result["error_category"] == category
    assert result["resolved_model"] is None and result["identity_status"] == "UNVERIFIED"
    assert "api-key-secret" not in json.dumps(row)


def test_correlated_success_without_provider_identity_fails_closed(tmp_path):
    row = detect_frame(FakeDetector([_laptop()]), _correlated_record(tmp_path))
    assert row["perception_result"]["status"] == "FAILED"
    assert row["perception_result"]["error_category"] == "IDENTITY_UNVERIFIED"
    assert row["detections"] == []


def test_correlated_hash_failure_does_not_call_provider(tmp_path):
    record = _correlated_record(tmp_path)
    Path(record["path"]).write_bytes(b"changed")
    detector = FakeDetector()
    row = detect_frame(detector, record)
    assert detector.calls == []
    assert row["status"] == "DETECTOR_ERROR" and row["error"] == "INVALID_REQUEST"


def test_follower_writes_correlated_inbox_and_skips_partial_last_record(tmp_path):
    from sentinel.room.ingest import result_from_dict
    record = _correlated_record(tmp_path)
    frames, inbox, output = tmp_path / "frames.jsonl", tmp_path / "inbox.jsonl", tmp_path / "out.jsonl"
    frames.write_text(json.dumps(record) + '\n{"partial":', encoding="utf-8")
    batch = DetectionBatch((), "OK", raw={"provider": "fixture", "model": "pinned-model"})
    assert follow(frames, output, FakeDetector([batch]), done=lambda: True,
                  inbox_log=inbox, print_fn=lambda _: None) == 1
    result = result_from_dict(json.loads(inbox.read_text()))
    assert result.status == "SUCCEEDED"
    assert live_detect_script.build_parser().parse_args(["--inbox", str(inbox)]).inbox == inbox


def test_producer_assigns_unique_correlation_at_capture(tmp_path):
    runner = CubeMissionRunner(
        DryRunClient(), "dry-run", mission=MISSION, site=SITE, run_id="RUN",
        mission_log=tmp_path / "mission.jsonl", sleep_fn=lambda _: None,
        correlation_context={"session_id": "s1", "action_id": "a1", "snapshot_version": 0,
                             "pose_revision_id": "pose1", "requested_model": "pinned-model"},
    )
    runner.recorder = FakeRecorder(tmp_path / "frames")
    assert runner.run(confirm=True).status == "PASS"
    records = [json.loads(line) for line in (tmp_path / "frames" / FRAMES_LOG_NAME).read_text().splitlines()]
    requests = [row["perception_request"] for row in records]
    assert len({request["observation_id"] for request in requests}) == 4
    assert len({request["frame_id"] for request in requests}) == 4
    assert all(request["session_id"] == "s1" and request["run_id"] == "RUN" for request in requests)


def test_correlated_mismatch_reports_actual_identity_in_the_inbox(tmp_path):
    from sentinel.room.ingest import append_inbox_result, result_from_dict
    record = _correlated_record(tmp_path)
    batch = DetectionBatch((), "OK", raw={"provider": "fixture", "model": "wrong-model"})
    row = detect_frame(FakeDetector([batch]), record)
    result = result_from_dict(row["perception_result"])
    assert result.status == "FAILED" and result.identity_status == "MISMATCH"
    assert result.resolved_model == "wrong-model" and result.error_category == "IDENTITY_MISMATCH"
    append_inbox_result(tmp_path / "inbox.jsonl", result)


def test_malformed_provider_metadata_still_emits_a_valid_typed_failure(tmp_path):
    from sentinel.room.ingest import result_from_dict
    row = detect_frame(FakeDetector([DetectionBatch((), "OK", raw={"provider": None, "model": []})]),
                       _correlated_record(tmp_path))
    result = result_from_dict(row["perception_result"])
    assert result.status == "FAILED" and result.error_category == "SCHEMA"


@pytest.mark.parametrize("invalid_identity", [
    {"provider": "p" * 4097}, {"model": "m" * 4097}, {"provider": None}, {"model": []},
], ids=["long-provider", "long-model", "null-provider", "non-string-model"])
def test_follower_publishes_safe_schema_failure_and_continues(tmp_path, invalid_identity):
    from sentinel.room.ingest import result_from_dict, poll_inbox
    from sentinel.room.models import CapabilityProfile
    from sentinel.room.store import RoomStore
    records = [_correlated_record(tmp_path), _correlated_record(tmp_path)]
    frames, inbox, output = tmp_path / "frames.jsonl", tmp_path / "inbox.jsonl", tmp_path / "out.jsonl"
    frames.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    valid_raw = {"provider": "fixture", "model": "pinned-model"}
    batches = [DetectionBatch((), "OK", raw={**valid_raw, **invalid_identity}),
               DetectionBatch((), "OK", raw=valid_raw)]
    assert follow(frames, output, FakeDetector(batches), done=lambda: True,
                  inbox_log=inbox, print_fn=lambda _: None) == 2
    results = [result_from_dict(json.loads(line)) for line in inbox.read_text().splitlines()]
    assert results[0].status == "FAILED" and results[0].error_category == "SCHEMA"
    assert results[0].identity_status == "UNVERIFIED" and results[0].resolved_model is None
    assert isinstance(results[0].provider, str) and 0 < len(results[0].provider) <= 4096
    assert results[1].status == "SUCCEEDED"
    store = RoomStore.open(tmp_path / "room.sqlite3")
    store.create_session("s1", "look", CapabilityProfile(()))
    outcomes = poll_inbox(store, inbox, current_snapshot_version=0)
    assert len(outcomes) == 2 and not outcomes[0].authorizes_current_state and outcomes[1].authorizes_current_state


def test_detector_raw_score_survives_follower_inbox_ingest_and_consumer(tmp_path):
    from sentinel.room.ingest import poll_inbox
    from sentinel.room.models import CapabilityProfile
    from sentinel.room.store import RoomStore
    record = _correlated_record(tmp_path)
    frames, inbox, output = tmp_path / "frames.jsonl", tmp_path / "inbox.jsonl", tmp_path / "out.jsonl"
    frames.write_text(json.dumps(record) + "\n", encoding="utf-8")
    detection = {**_laptop().detections[0], "raw_score": 0.37, "score_kind": "DETECTOR_RAW"}
    # The supplied band is preserved, rather than recomputed from the score.
    batch = DetectionBatch((detection,), "OK", ("medium",),
                           raw={"provider": "fixture", "model": "pinned-model"})
    assert follow(frames, output, FakeDetector([batch]), done=lambda: True,
                  inbox_log=inbox, print_fn=lambda _: None) == 1
    store = RoomStore.open(tmp_path / "room.sqlite3")
    store.create_session("s1", "look", CapabilityProfile(()))
    assert poll_inbox(store, inbox, current_snapshot_version=0)[0].authorizes_current_state
    current = store.current_hypotheses("s1", snapshot_version=0)
    assert len(current) == 1
    assert (current[0]["raw_score"], current[0]["score_kind"], current[0]["confidence_band"]) == (
        0.37, "DETECTOR_RAW", "medium",
    )


def test_raised_structured_mismatch_preserves_actual_identity_through_follower(tmp_path):
    from sentinel.survey.providers import ModelIdentityMismatch
    from sentinel.room.ingest import result_from_dict
    record = _correlated_record(tmp_path)
    error = ModelIdentityMismatch("do not serialize this api-key-secret", provider="actual-provider",
                                  requested_model="pinned-model", resolved_model="actual-model")
    frames, inbox, output = tmp_path / "frames.jsonl", tmp_path / "inbox.jsonl", tmp_path / "out.jsonl"
    frames.write_text(json.dumps(record) + "\n", encoding="utf-8")
    assert follow(frames, output, FakeDetector(error=error), done=lambda: True,
                  inbox_log=inbox, print_fn=lambda _: None) == 1
    result = result_from_dict(json.loads(inbox.read_text()))
    assert (result.status, result.identity_status, result.provider, result.resolved_model) == (
        "FAILED", "MISMATCH", "actual-provider", "actual-model",
    )
    assert result.error_category == "IDENTITY_MISMATCH"
    assert "api-key-secret" not in inbox.read_text() + output.read_text()


def test_legacy_mismatch_exception_remains_a_valid_unverified_failure(tmp_path):
    from sentinel.survey.providers import ModelIdentityMismatch
    from sentinel.room.ingest import result_from_dict
    error = ModelIdentityMismatch("drift")
    assert str(error) == "drift"
    result = result_from_dict(detect_frame(FakeDetector(error=error), _correlated_record(tmp_path))["perception_result"])
    assert result.status == "FAILED" and result.identity_status == "UNVERIFIED"
    assert result.resolved_model is None and result.error_category == "IDENTITY_MISMATCH"


@pytest.mark.parametrize("metadata", [
    {"provider": "x" * 4097}, {"resolved_model": "x" * 4097},
    {"requested_model": "another-request"}, {"resolved_model": "pinned-model"},
])
def test_malformed_raised_identity_metadata_fails_closed(tmp_path, metadata):
    from sentinel.survey.providers import ModelIdentityMismatch
    from sentinel.room.ingest import result_from_dict
    identity = {"provider": "actual-provider", "requested_model": "pinned-model", "resolved_model": "actual-model"}
    error = ModelIdentityMismatch(**{**identity, **metadata})
    result = result_from_dict(detect_frame(FakeDetector(error=error), _correlated_record(tmp_path))["perception_result"])
    assert result.status == "FAILED" and result.error_category == "SCHEMA"
    assert result.resolved_model is None and result.identity_status == "UNVERIFIED"


def test_partial_frame_announcement_log_alerts_without_failing_the_mission(tmp_path):
    events = []
    runner = CubeMissionRunner(
        DryRunClient(), "dry-run", mission=MISSION, site=SITE, run_id="RUN",
        mission_log=tmp_path / "mission.jsonl", sleep_fn=lambda _: None,
        progress=lambda event, message: events.append((event, message)),
        correlation_context={"session_id": "s1", "action_id": "a1", "snapshot_version": 0,
                             "pose_revision_id": "pose1", "requested_model": "pinned-model"},
    )
    runner.recorder = FakeRecorder(tmp_path / "frames")
    log = tmp_path / "frames" / FRAMES_LOG_NAME
    log.write_bytes(b'{"partial":')
    assert runner.run(confirm=True).status == "PASS"
    assert log.read_bytes() == b'{"partial":'
    assert any(event == "alert" and "frame not announced" in message for event, message in events)


def test_260_detection_oversized_success_publishes_bounded_failure_and_follower_continues(tmp_path):
    from dataclasses import asdict
    from sentinel.room.ingest import MAX_INBOX_RECORD_BYTES, poll_inbox, result_from_dict
    from sentinel.room.models import CapabilityProfile
    from sentinel.room.store import RoomStore
    records = [_correlated_record(tmp_path), _correlated_record(tmp_path)]
    # Each resulting detection ID remains legal (<4096 characters), but the
    # complete 260-detection success would exceed the canonical inbox limit.
    records[0]["perception_request"]["observation_id"] = "observation:" + "o" * 3980
    frames, inbox, output = tmp_path / "frames.jsonl", tmp_path / "inbox.jsonl", tmp_path / "out.jsonl"
    frames.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    identity = {"provider": "fixture", "model": "pinned-model", "secret": "private-provider-secret"}
    batches = [DetectionBatch(_laptop().detections * 260, "OK", ("high",) * 260, raw=identity),
               DetectionBatch(_laptop().detections, "OK", ("high",), raw=identity)]
    assert follow(frames, output, FakeDetector(batches), done=lambda: True,
                  inbox_log=inbox, print_fn=lambda _: None) == 2
    lines = inbox.read_bytes().splitlines(keepends=True)
    assert len(lines) == 2 and all(len(line) <= MAX_INBOX_RECORD_BYTES for line in lines)
    failed, success = [result_from_dict(json.loads(line)) for line in lines]
    assert failed.status == "FAILED" and failed.error_category == "RESPONSE_TOO_LARGE"
    assert failed.identity_status == "UNVERIFIED" and failed.resolved_model is None
    assert failed.detections == ()
    assert asdict(failed.request) == records[0]["perception_request"]
    assert success.status == "SUCCEEDED" and len(success.detections) == 1
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert rows[0]["detections"] == [] and rows[0]["raw"] is None
    assert rows[0]["error"] == "RESPONSE_TOO_LARGE"
    assert "private-provider-secret" not in inbox.read_text() + output.read_text()
    store = RoomStore.open(tmp_path / "room.sqlite3")
    store.create_session("s1", "look", CapabilityProfile(()))
    outcomes = poll_inbox(store, inbox, current_snapshot_version=0)
    assert len(outcomes) == 2 and not outcomes[0].authorizes_current_state and outcomes[1].authorizes_current_state
    assert store.connection.execute("SELECT COUNT(*) FROM room_inbox_rejections").fetchone()[0] == 0
    assert store.connection.execute("SELECT COUNT(*) FROM room_perception_results").fetchone()[0] == 2
    store.close()


def test_oversized_success_fallback_bounds_request_timestamp_without_losing_capture_instant(tmp_path):
    from datetime import datetime
    from sentinel.room.ingest import MAX_INBOX_RECORD_BYTES, canonical_line, result_from_dict
    record = _correlated_record(tmp_path)
    captured_at = "2026-09-22T00:00:00." + "0" * MAX_INBOX_RECORD_BYTES + "+00:00"
    record["perception_request"]["captured_at"] = captured_at
    batch = DetectionBatch((), "OK", raw={"provider": "fixture", "model": "pinned-model"})
    row = detect_frame(FakeDetector([batch]), record)
    result = result_from_dict(row["perception_result"])
    assert result.status == "FAILED" and result.error_category == "RESPONSE_TOO_LARGE"
    assert len(canonical_line(row["perception_result"])) <= MAX_INBOX_RECORD_BYTES
    assert datetime.fromisoformat(result.request.captured_at) == datetime.fromisoformat(captured_at)
    assert result.request.observation_id == record["perception_request"]["observation_id"]
