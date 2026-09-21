"""Recover the yaw-scan flight pattern from video alone.

The controller commands: takeoff, then alternating ROTATE / DWELL phases. That
structure is visible in the optical-flow profile -- rotation produces large
whole-frame motion, dwell produces near-zero -- so it can be recovered without
any telemetry.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import cv2
import numpy as np

from .config import ChoreographyConfig
from .quality import FrameQuality

CHOREOGRAPHY_VERSION = "choreo/2026-09-16.1"


@dataclass
class Phase:
    kind: str          # "DWELL" | "ROTATE"
    index: int         # ordinal within its kind, for DWELL this is the sector
    first: int
    last: int
    t0_s: float
    t1_s: float
    median_motion: float
    n_frames: int

    def to_dict(self) -> dict:
        return asdict(self)


def segment(qualities: list[FrameQuality], cfg: ChoreographyConfig,
            fps: float = 30.0) -> list[Phase]:
    """Split the run into ROTATE and DWELL phases.
    """
    if not qualities:
        return []

    motion = np.array([q.motion for q in qualities], dtype=np.float32)

    # Boxcar smooth. A single frame of flow is noisy; we want the phase, not
    # the instantaneous value. 'same' keeps indices aligned with frames.
    kern = np.ones(cfg.smooth_window, dtype=np.float32) / cfg.smooth_window
    smoothed = np.convolve(motion, kern, mode="same")

    lo, hi = float(smoothed.min()), float(smoothed.max())
    threshold = lo + cfg.motion_split * (hi - lo)
    moving = smoothed > threshold

    phases: list[Phase] = []
    counts = {"DWELL": 0, "ROTATE": 0}
    start = 0
    for i in range(1, len(moving) + 1):
        boundary = i == len(moving) or moving[i] != moving[i - 1]
        if not boundary:
            continue
        # Runs shorter than min_phase_frames are flow noise, not a real phase.
        if i - start >= cfg.min_phase_frames:
            kind = "ROTATE" if moving[start] else "DWELL"
            phases.append(Phase(
                kind=kind,
                index=counts[kind],
                first=start,
                last=i - 1,
                t0_s=round(start / fps, 3),
                t1_s=round((i - 1) / fps, 3),
                median_motion=round(float(np.median(motion[start:i])), 3),
                n_frames=i - start,
            ))
            counts[kind] += 1
        if i < len(moving):
            start = i
    return phases


def check_choreography(phases: list[Phase], cfg: ChoreographyConfig) -> dict:
    """Compare the observed pattern against what was commanded."""
    dwells = [p for p in phases if p.kind == "DWELL"]
    rotations = [p for p in phases if p.kind == "ROTATE"]
    ok = len(rotations) == cfg.expected_turns
    return {
        "dwells": len(dwells),
        "rotations": len(rotations),
        "expected_turns": cfg.expected_turns,
        "match": ok,
        "verdict": "MATCH" if ok else
                   f"MISMATCH: {len(rotations)} rotations observed, "
                   f"{cfg.expected_turns} commanded",
        # n turns of 90 deg means dwell n re-observes dwell 0
        "closes_loop": (len(rotations) * cfg.turn_deg) % 360 == 0,
    }


def loop_closure(first_bgr: np.ndarray, last_bgr: np.ndarray) -> float:
    """Normalised correlation between the first and last dwell views, 0..1.

    Four commanded 90-degree turns should return the airframe to its starting
    heading, so the final dwell should see roughly what the first one saw.

    Downscale and blur first so the comparison is of scene layout rather than
    pixel-exact detail. The airframe will have drifted somewhat in x/y even
    with perfect yaw.
    """
    def prep(img):
        g = cv2.cvtColor(cv2.resize(img, (160, 120)), cv2.COLOR_BGR2GRAY)
        g = cv2.GaussianBlur(g, (5, 5), 0).astype(np.float32)
        return g - g.mean()

    a, b = prep(first_bgr), prep(last_bgr)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return 0.0 if denom == 0 else float(np.clip((a * b).sum() / denom, 0.0, 1.0))