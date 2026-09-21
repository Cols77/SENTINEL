"""Offline frame sources/sinks for the pose stream (no drone required).

``ReplayClient`` duck-types the live video client used by
``run_pose_stream`` (start_video/read_frame/stop_video/close) over an
mp4/avi file or a directory of PNGs. ``FrameRecorder`` saves frames to
PNGs so a live run can be replayed later.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp"}


def _frame(bgr: np.ndarray) -> SimpleNamespace:
    rgb = np.ascontiguousarray(bgr[..., ::-1])
    return SimpleNamespace(
        data=rgb, pixel_format="rgb24", width=rgb.shape[1], height=rgb.shape[0]
    )


class ReplayClient:
    def __init__(self, source):
        self._source = Path(source)
        self._files: list[Path] = []
        self._idx = 0
        self._cap = None
        self._started = False

    def start_video(self) -> None:
        if not self._source.exists():
            raise FileNotFoundError(f"replay source not found: {self._source}")
        if self._source.is_dir():
            self._files = sorted(
                p for p in self._source.iterdir() if p.suffix.lower() in _IMAGE_EXTS
            )
            if not self._files:
                raise RuntimeError(f"no image frames in {self._source}")
            self._idx = 0
        else:
            cap = cv2.VideoCapture(str(self._source))
            if not cap.isOpened():
                cap.release()
                raise RuntimeError(f"cannot open video: {self._source}")
            self._cap = cap
        self._started = True

    def read_frame(self, timeout: float | None = None):
        if not self._started:
            raise RuntimeError("replay not started")
        if self._cap is not None:
            ok, bgr = self._cap.read()
            if not ok or bgr is None:
                raise RuntimeError("end of replay")
            return _frame(bgr)
        while self._idx < len(self._files):
            path = self._files[self._idx]
            self._idx += 1
            bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if bgr is not None:
                return _frame(bgr)
        raise RuntimeError("end of replay")

    def stop_video(self) -> None:
        self._started = False
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def close(self) -> None:
        self.stop_video()


class FrameRecorder:
    """Save client frames (rgb24 by default) as sequentially numbered PNGs."""

    def __init__(self, directory):
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self.count = 0

    def save(self, frame) -> Path:
        arr = np.asarray(frame.data, dtype=np.uint8)
        if arr.ndim == 1 and arr.size:
            arr = arr.reshape(int(frame.height), int(frame.width), -1)
        if getattr(frame, "pixel_format", "rgb24") == "rgb24":
            arr = arr[..., ::-1]
        path = self._dir / f"{self.count:06d}.png"
        if not cv2.imwrite(str(path), np.ascontiguousarray(arr)):
            raise RuntimeError(f"failed to write {path}")
        self.count += 1
        return path
