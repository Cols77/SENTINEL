from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np


@dataclass(frozen=True)
class VideoInfo:
    path: str
    width: int
    height: int
    fps: float
    frame_count: int      # container metadata; may lie, see note below
    duration_s: float

    def to_dict(self) -> dict:
        return asdict(self)


def probe(path: str | Path) -> VideoInfo:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    cap = cv2.VideoCapture(str(p))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {p}")
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        cap.release()
    return VideoInfo(str(p), w, h, fps, n, round(n / fps, 3) if fps else 0.0)


def iter_frames(path: str | Path) -> Iterator[tuple[int, np.ndarray]]:
    """Yield (index, BGR frame). Indices are decode order, starting at 0.

    Tello video arrives over UDP and drops packets, so the stream contains
    corrupt macroblocks and the container's frame count is not always the
    number of frames you can actually decode. Everything downstream therefore
    keys off the index yielded here -- the frames that really decoded -- rather
    than trusting CAP_PROP_FRAME_COUNT.
    """
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {path}")
    try:
        i = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            yield i, frame
            i += 1
    finally:
        cap.release()


def read_frames(path: str | Path, indices: set[int]) -> dict[int, np.ndarray]:
    """Second pass pulling only specific frames.

    Sequential decode rather than CAP_PROP_POS_FRAMES seeking: seeking in H.264
    lands on the nearest keyframe, and this stream has sparse keyframes plus
    corruption, so a seek to frame 299 can silently hand you frame 280.
    """
    return {i: f.copy() for i, f in iter_frames(path) if i in indices}