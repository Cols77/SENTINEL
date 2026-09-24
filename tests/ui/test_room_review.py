from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

from sentinel.ui.room_review import RoomReview


def test_review_requires_recording_and_staged_local_model(tmp_path):
    project = tmp_path / "perception"
    project.mkdir()
    review = RoomReview(tmp_path / "run", project)
    assert review.status()["recording"]["status"] == "MISSING"
    assert review.start()["reason_code"] == "RECORDING_UNAVAILABLE"
    review.run_dir.mkdir()
    (review.run_dir / "recording.json").write_text('{"error":"Permission denied"}')
    assert review.status()["recording"]["error"] == "Permission denied"

    (review.run_dir / "flight.h264").write_bytes(b"video")
    assert review.status()["recording"]["status"] == "FAILED"
    (review.run_dir / "recording.json").unlink()
    review = RoomReview(review.run_dir, project)
    assert review.status()["recording"]["status"] == "READY"
    assert review.start()["reason_code"] == "MODEL_NOT_STAGED"
    (project / "models").mkdir()
    (project / "models" / "manifest.json").write_text("{}")
    assert review.start()["reason_code"] == "MODEL_NOT_STAGED"


def test_review_launches_once_and_exposes_result_after_process_finishes(tmp_path):
    project = tmp_path / "perception"
    (project / "models").mkdir(parents=True)
    (project / "models" / "manifest.json").write_text('{"detector_openvocab":{"id":"dino","sha":"abc"}}')
    python = project / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    run = tmp_path / "run"
    run.mkdir()
    (run / "flight.h264").write_bytes(b"video")
    launched = []

    class Process:
        def poll(self):
            return None if not launched[0]["finished"] else 0

    def launch(argv, **kwargs):
        launched.append({"argv": argv, "kwargs": kwargs, "finished": False})
        return Process()

    review = RoomReview(run, project, launch=launch)
    assert review.start()["status"] == "RUNNING"
    assert review.start()["reason_code"] == "ANALYSIS_ALREADY_STARTED"
    assert launched[0]["argv"][0] == str(python)
    assert launched[0]["argv"][1:3] == ["-m", "sentinel_perception.review"]
    assert launched[0]["kwargs"]["cwd"] == project
    assert launched[0]["kwargs"]["env"]["HF_HUB_OFFLINE"] == "1"
    assert launched[0]["kwargs"]["env"]["TRANSFORMERS_OFFLINE"] == "1"
    (run / "review" / "annotated.mp4").write_bytes(b"mp4")
    (run / "review" / "review.json").write_text(json.dumps({
        "status": "COMPLETE", "detections": [{"frame": 2, "time_s": 0.2, "label": "phone"}]}))
    launched[0]["finished"] = True
    assert review.status()["analysis"]["status"] == "COMPLETE"
    assert review.result()["detections"][0]["time_s"] == 0.2
    assert review.video_path() == run / "review" / "annotated.mp4"


def test_failed_analysis_reports_the_process_error_without_claiming_completion(tmp_path):
    project = tmp_path / "perception"
    (project / "models").mkdir(parents=True)
    (project / "models" / "manifest.json").write_text('{"detector_openvocab":{"id":"dino","sha":"abc"}}')
    python = project / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    run = tmp_path / "run"
    run.mkdir()
    (run / "flight.h264").write_bytes(b"video")

    class Failed:
        def poll(self):
            return 7

    review = RoomReview(run, project, launch=lambda *_a, **_k: Failed())
    assert review.start()["status"] == "RUNNING"
    (run / "review" / "analysis.log").write_text("model load failed")
    state = review.status()["analysis"]
    assert state["status"] == "FAILED"
    assert state["reason_code"] == "ANALYSIS_EXIT_7"
    assert "model load failed" in state["detail"]


def test_concurrent_analysis_requests_launch_only_one_process(tmp_path):
    project = tmp_path / "perception"
    (project / "models").mkdir(parents=True)
    (project / "models" / "manifest.json").write_text('{"detector_openvocab":{"id":"dino","sha":"abc"}}')
    python = project / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    run = tmp_path / "run"
    run.mkdir()
    (run / "flight.h264").write_bytes(b"video")
    launched = Event()
    second_launch = Event()
    calls = []

    class Process:
        def poll(self):
            return None

    def launch(*_args, **_kwargs):
        calls.append(1)
        launched.set()
        if len(calls) > 1:
            second_launch.set()
        second_launch.wait(timeout=1)
        return Process()

    review = RoomReview(run, project, launch=launch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(review.start)
        assert launched.wait(timeout=2)
        second = pool.submit(review.start)
        assert {first.result()["status"], second.result()["status"]} == {"RUNNING", "CONFLICT"}
    assert len(calls) == 1
