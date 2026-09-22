"""
Every tunable in one place, versioned.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field

CONFIG_VERSION = "cfg/2026-09-16.1"

STUFF_QUERIES = {
    "scree", "loose scree", "gravel", "snow", "vegetation", "grass",
    "brick paving", "paving", "soil", "sand", "rubble", "debris field",
}


@dataclass(frozen=True)
class QualityConfig:
    """Frame-level gating.
    """
    max_motion: float = 1.0          # mean optical flow px/frame @ 240x180
    max_clipped_frac: float = 0.12   # fraction of pixels at 0 or 255
    low_texture_abs: float = 3000.0  # Tenengrad floor -> flag, never reject
    low_texture_rel: float = 0.40    # fraction of run median -> flag
    flow_scale: tuple[int, int] = (240, 180)


@dataclass(frozen=True)
class ChoreographyConfig:
    """Recovering the flight pattern from the motion profile."""
    smooth_window: int = 5           # frames, boxcar over the flow signal
    motion_split: float = 0.25       # threshold as fraction of run flow range
    min_phase_frames: int = 8        # ignore shorter runs as noise
    settle_head: int = 9             # discard after a turn (0.30 s @ 30fps)
    settle_tail: int = 4             # discard before the next turn
    expected_turns: int = 4
    turn_deg: float = 90.0
    loop_closure_min: float = 0.45


@dataclass(frozen=True)
class CameraConfig:
    """Standard Tello. The quoted 82.6 deg is DIAGONAL, these are derived for
    4:3 and are what the bearing maths actually needs. Getting this wrong
    silently skews every bearing, so it is stated explicitly rather than
    hardcoded at a call site."""
    hfov_deg: float = 70.2
    vfov_deg: float = 55.6
    width: int = 960
    height: int = 720


@dataclass(frozen=True)
class Config:
    quality: QualityConfig = field(default_factory=QualityConfig)
    choreography: ChoreographyConfig = field(default_factory=ChoreographyConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    version: str = CONFIG_VERSION

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT = Config()