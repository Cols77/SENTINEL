"""Tests for the pose live-stream harness (src/sentinel/pose/stream.py).

Pulls frames from a client, runs them through estimate_pose, emits one
log line per frame (report_id, point, heading), and appends
DRONE_POSE_REPORTED rows to a run-scoped SQLite DB. Driven here with a
fake client; no Tello/pyav required.
"""
import sqlite3

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


def test_fail_closed_on_blank_frames(tmp_path, capsys):
    """Blank frames (no tag) fail closed per frame without crashing."""
    conn = _make_db(tmp_path)
    client = FakeClient([_blank_frame(), _blank_frame(), _blank_frame()])
    events = run_pose_stream(
        client, load_tag_map(TAG_MAP), conn, run_id="t1", max_frames=3
    )
    assert client.video_enabled is False
    assert latest_pose(conn, "t1") is None
    assert isinstance(events, list)


def test_blank_frame_emits_no_persisted_event(tmp_path):
    conn = _make_db(tmp_path)
    client = FakeClient([_blank_frame()])
    events = run_pose_stream(
        client, load_tag_map(TAG_MAP), conn, run_id="t2", max_frames=1
    )
    assert events == []
    assert latest_pose(conn, "t2") is None