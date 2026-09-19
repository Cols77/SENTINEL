"""Tests for the pose live-stream harness (src/sentinel/pose/stream.py).

Pulls frames from a client, runs them through estimate_pose, emits one
log line per frame (report_id, point, heading), and appends
DRONE_POSE_REPORTED rows to a run-scoped SQLite DB. Driven here with a
fake client; no Tello/pyav required.
"""
import hashlib
import json
import sqlite3

import cv2
import numpy as np

from sentinel.pose.persistence import ensure_schema, latest_pose
from sentinel.pose.stream import run_pose_stream
from sentinel.pose.tagmap import load_tag_map

TAG_MAP = "docs/superpowers/specs/contracts/fixtures/tag-map-v1.valid.json"


class _FrameLike:
    def __init__(self, data, pixel_format):
        self.data = data
        self.pixel_format = pixel_format


def _blank_frame(size=320, pixel_format="rgb24"):
    return _FrameLike(np.zeros((size, size, 3), dtype=np.uint8), pixel_format)


class FakeClient:
    def __init__(self, frames, *, pixel_format="rgb24"):
        self._frames = list(frames)
        self._pixel_format = pixel_format
        self.video_enabled = False
        self.frames_read = 0

    def start_video(self):
        self.video_enabled = True

    def stop_video(self):
        self.video_enabled = False

    def read_frame(self, timeout):
        if not self.video_enabled:
            raise RuntimeError("stream not enabled")
        if not self._frames:
            raise RuntimeError("no more frames")
        self.frames_read += 1
        raw = self._frames.pop(0)
        return _FrameLike(raw, self._pixel_format)


def _make_db(tmp_path):
    conn = sqlite3.connect(tmp_path / "pose.db")
    ensure_schema(conn)
    return conn


def _render_mapped_tag(tag_id=0, *, distance_m=0.6):
    """Render a real 36h11 marker using the fixture's camera calibration."""
    tag_map = load_tag_map(TAG_MAP)
    tag = tag_map.tag(tag_id)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    marker = cv2.aruco.generateImageMarker(dictionary, tag_id, 400)
    half = tag.size_m / 2.0
    object_points = np.array(
        [[-half, half, 0], [half, half, 0], [half, -half, 0], [-half, -half, 0]],
        dtype=np.float64,
    )
    corners, _ = cv2.projectPoints(
        object_points,
        np.zeros((3, 1), dtype=np.float64),
        np.array([[0.0], [0.0], [distance_m]], dtype=np.float64),
        tag_map.K,
        tag_map.dist,
    )
    destination = corners.reshape(-1, 2).astype(np.float32)
    height, width = marker.shape
    source = np.array([[0, height], [width, height], [width, 0], [0, 0]], dtype=np.float32)
    transform = cv2.getPerspectiveTransform(source, destination)
    frame = np.full((720, 960, 3), 255, dtype=np.uint8)
    warped = cv2.warpPerspective(
        marker,
        transform,
        (960, 720),
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=255,
    )
    frame[warped < 255] = 0
    return frame


def test_fail_closed_on_blank_frames(tmp_path, caplog):
    """Blank frames (no tag) fail closed per frame without crashing."""
    conn = _make_db(tmp_path)
    client = FakeClient([_blank_frame(), _blank_frame(), _blank_frame()])
    events = run_pose_stream(
        client, load_tag_map(TAG_MAP), conn, run_id="t1", max_frames=3
    )
    assert client.video_enabled is False
    assert latest_pose(conn, "t1") is None
    assert isinstance(events, list)
    assert "fail-closed" in caplog.text


def test_success_path_persists_synthetic_mapped_tag(tmp_path):
    """A real mapped marker produces a report and hash-verified DB event."""
    conn = _make_db(tmp_path)
    client = FakeClient([_render_mapped_tag(0)])

    reports = run_pose_stream(
        client, load_tag_map(TAG_MAP), conn, run_id="success", max_frames=1
    )

    assert len(reports) == 1
    report = reports[0]
    assert report.tag_observations[0].tag_id == 0
    assert np.isfinite(report.point[0]) and np.isfinite(report.point[1])
    assert report.point[0] > 0 and report.point[1] > 0

    snapshot = latest_pose(conn, "success")
    assert snapshot is not None
    assert snapshot["pose"]["event_type"] == "DRONE_POSE_REPORTED"
    assert snapshot["pose"]["report_id"] == report.report_id
    row = conn.execute(
        "SELECT event_type, payload_json, payload_sha256 FROM pose_events "
        "WHERE run_id = ?",
        ("success",),
    ).fetchone()
    assert row is not None
    event_type, payload_json, payload_sha256 = row
    assert event_type == "DRONE_POSE_REPORTED"
    assert payload_sha256 == hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    assert json.loads(payload_json)["report_id"] == report.report_id


def test_blank_frame_emits_no_persisted_event(tmp_path):
    conn = _make_db(tmp_path)
    client = FakeClient([_blank_frame()])
    events = run_pose_stream(
        client, load_tag_map(TAG_MAP), conn, run_id="t2", max_frames=1
    )
    assert events == []
    assert latest_pose(conn, "t2") is None