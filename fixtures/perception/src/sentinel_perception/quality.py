from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Optional

import cv2
import numpy as np

from .config import QualityConfig

QUALITY_VERSION = "quality/2026-09-16.1"


@dataclass
class FrameQuality:
    index: int
    t_s: float
    motion: float          # mean optical flow magnitude -> reject criterion
    tenengrad: float       # gradient energy, texture proxy -> metadata only
    laplacian_var: float   # legacy sharpness, reported for comparison
    mean_luma: float
    clipped_frac: float    # fraction at 0 or 255 -> reject criterion
    accepted: bool = False
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def tenengrad(gray: np.ndarray) -> float:
    """Sobel gradient energy. Higher = more texture for a detector to work with."""
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    return float(np.mean(gx * gx + gy * gy))


def clipped_fraction(gray: np.ndarray) -> float:
    """Fraction of pixels at the ends of the range. Catches blown-out snow and
    crushed shadow, where pixel values carry no information at all."""
    return float(((gray <= 2) | (gray >= 253)).sum()) / gray.size


def score_frame(
    frame_bgr: np.ndarray,
    index: int,
    t_s: float,
    prev_small: Optional[np.ndarray],
    cfg: QualityConfig,
) -> tuple[FrameQuality, np.ndarray]:
    """Score one frame. Returns (quality, downscaled gray for the next call).

    Optical flow runs at 240x180 rather than full resolution: we need a single
    scalar for "is the airframe moving", and Farneback at full res costs ~10x
    more for no gain in that decision.
    """
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, cfg.flow_scale)

    if prev_small is None:
        motion = 0.0
    else:
        flow = cv2.calcOpticalFlowFarneback(
            prev_small, small, None,
            pyr_scale=0.5, levels=2, winsize=15,
            iterations=2, poly_n=5, poly_sigma=1.2, flags=0,
        )
        motion = float(np.linalg.norm(flow, axis=2).mean())

    q = FrameQuality(
        index=index,
        t_s=round(t_s, 3),
        motion=round(motion, 4),
        tenengrad=round(tenengrad(gray), 1),
        laplacian_var=round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 1),
        mean_luma=round(float(gray.mean()), 1),
        clipped_frac=round(clipped_fraction(gray), 4),
    )

    # --- reject criteria ---
    if q.motion > cfg.max_motion:
        q.flags.append("MOTION")
    if q.clipped_frac > cfg.max_clipped_frac:
        q.flags.append("EXPOSURE")
    q.accepted = not q.flags

    # --- advisory only, after accept is decided ---
    if q.tenengrad < cfg.low_texture_abs:
        q.flags.append("LOW_TEXTURE_ABS")

    return q, small


def profile(path, cfg: QualityConfig, fps: float) -> list[FrameQuality]:
    """Score every frame in one decode pass."""
    from .video import iter_frames

    out: list[FrameQuality] = []
    prev = None
    for i, frame in iter_frames(path):
        q, prev = score_frame(frame, i, i / fps, prev, cfg)
        out.append(q)
    return out