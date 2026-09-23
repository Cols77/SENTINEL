"""Tests for offline replay/recording clients (src/sentinel/pose/replay.py)."""
import cv2
import numpy as np
import pytest

from sentinel.pose.replay import FrameRecorder, ReplayClient


class _F:
    def __init__(self, data):
        self.data = data


def _img(v, h=48, w=64):
    a = np.zeros((h, w, 3), dtype=np.uint8)
    a[..., 0] = v  # BGR blue channel
    return a


def _write_pngs(d, n=3):
    for i in range(n):
        cv2.imwrite(str(d / f"{i:06d}.png"), _img(50 * (i + 1)))


def test_png_dir_yields_rgb_frames_then_eof(tmp_path):
    _write_pngs(tmp_path)
    c = ReplayClient(tmp_path)
    c.start_video()
    f = c.read_frame(timeout=1.0)
    assert f.pixel_format == "rgb24"
    assert (f.width, f.height) == (64, 48)
    assert f.data.shape == (48, 64, 3) and f.data.dtype == np.uint8
    assert f.data[0, 0, 2] == 50 and f.data[0, 0, 0] == 0  # BGR->RGB
    c.read_frame(timeout=1.0)
    c.read_frame(timeout=1.0)
    with pytest.raises(RuntimeError):
        c.read_frame(timeout=1.0)
    c.stop_video()
    c.close()


def test_mp4_replay(tmp_path):
    p = tmp_path / "v.avi"
    w = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 48))
    for i in range(4):
        w.write(_img(40 * i))
    w.release()
    c = ReplayClient(p)
    c.start_video()
    n = 0
    with pytest.raises(RuntimeError):
        while True:
            f = c.read_frame(timeout=1.0)
            assert f.data.shape == (48, 64, 3)
            n += 1
    assert n == 4
    c.close()


def test_missing_source_fails_closed(tmp_path):
    with pytest.raises(FileNotFoundError):
        ReplayClient(tmp_path / "nope").start_video()


def test_empty_dir_fails_closed(tmp_path):
    with pytest.raises(RuntimeError):
        ReplayClient(tmp_path).start_video()


def test_read_before_start_raises(tmp_path):
    _write_pngs(tmp_path, 1)
    with pytest.raises(RuntimeError):
        ReplayClient(tmp_path).read_frame(timeout=1.0)


def test_recorder_roundtrip(tmp_path):
    out = tmp_path / "rec"
    rec = FrameRecorder(out)
    rgb = np.zeros((48, 64, 3), dtype=np.uint8)
    rgb[..., 0] = 200
    for _ in range(2):
        rec.save(_F(rgb))
    assert rec.count == 2
    assert len(list(out.glob("*.png"))) == 2
    c = ReplayClient(out)
    c.start_video()
    assert c.read_frame(timeout=1.0).data[0, 0, 0] == 200
