"""Room footage review is visual evidence, not a yaw-scan scene report."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import av
import numpy as np


PACKAGE = Path(__file__).resolve().parents[2] / "fixtures" / "perception" / "src"
sys.path.insert(0, str(PACKAGE))
from sentinel_perception.review import review_video  # noqa: E402
from sentinel_perception import review  # noqa: E402


def test_review_emits_annotated_video_and_timestamped_detections_without_scan_claims(tmp_path):
    source = tmp_path / "flight.mp4"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))
    assert writer.isOpened()
    for index in range(6):
        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        frame[:, :, 0] = index * 20
        writer.write(frame)
    writer.release()
    seen = []

    def detect(frame, index):
        seen.append(index)
        return [dict(label="phone", score=0.7, box_xyxy=[5, 6, 20, 25])]

    output = tmp_path / "review"
    summary = review_video(source, output, detect, stride=2, model="fake-dino")

    assert seen == [0, 2, 4]
    assert summary["status"] == "COMPLETE"
    assert summary["model"] == "fake-dino"
    assert summary["timestamp_basis"] == "container_fps"
    assert [row["time_s"] for row in summary["detections"]] == [0.0, 0.2, 0.4]
    assert all(row["label"] == "phone" for row in summary["detections"])
    assert "bearing" not in json.dumps(summary)
    assert "count" not in json.dumps(summary)
    assert (output / "annotated.mp4").stat().st_size > 0
    with av.open(str(output / "annotated.mp4")) as rendered:
        assert rendered.streams.video[0].codec_context.name == "h264"
    assert json.loads((output / "review.json").read_text()) == summary


def test_raw_h264_timestamps_are_labeled_as_estimates(tmp_path):
    source = tmp_path / "flight.h264"
    with av.open(str(source), mode="w", format="h264") as container:
        stream = container.add_stream("libx264", rate=12)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        for _ in range(6):
            frame = av.VideoFrame.from_ndarray(np.zeros((48, 64, 3), dtype=np.uint8), format="bgr24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)

    summary = review_video(source, tmp_path / "review", lambda _frame, _index: [],
                           stride=2, model="fake-dino")

    assert summary["timestamp_basis"] == "estimated_30fps"
    assert summary["fps"] == 30.0
    assert summary["frames"] == 6


def test_model_review_uses_dzivha_detector_and_filters_for_room_classes(tmp_path, monkeypatch):
    import types

    calls = []

    class Detector:
        def __init__(self, model_id, model_sha, **kwargs):
            calls.append((model_id, model_sha, kwargs))

        def detect_array(self, frame, queries, index, sector):
            calls.append((tuple(queries), index, sector))
            detection = types.SimpleNamespace(query="phone", score=0.8,
                                              box_xyxy=(5, 6, 20, 25), label="phone")
            return types.SimpleNamespace(error=None, detections=[detection])

    fake_detection = types.ModuleType("sentinel_perception.detection")
    fake_detection.GroundingDinoDetector = Detector
    fake_detection.clean = lambda rows, *_a, **_k: (rows, {})
    fake_detection.deduplicate = lambda rows: rows
    fake_models = types.ModuleType("sentinel_perception.models")
    fake_models.load_manifest = lambda: {"detector_openvocab": {"id": "dino-test", "sha": "abc"}}
    monkeypatch.setitem(sys.modules, fake_detection.__name__, fake_detection)
    monkeypatch.setitem(sys.modules, fake_models.__name__, fake_models)
    monkeypatch.setattr(review, "review_video", lambda _src, _out, detect, **kw: {
        "model": kw["model"], "detections": detect(np.zeros((48, 64, 3), dtype=np.uint8), 2)})

    result = review.run_model_review(tmp_path / "flight.h264", tmp_path / "review")

    assert calls[0][0:2] == ("dino-test", "abc")
    assert calls[0][2]["revision"] == "abc"
    assert calls[0][2]["local_files_only"] is True
    assert {row[0] for row in calls[1:]} == {("computer", "person", "phone"), ("pen",)}
    assert result["detections"][0]["label"] == "phone"
