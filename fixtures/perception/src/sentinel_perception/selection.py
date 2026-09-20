from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Optional

import numpy as np

from .choreography import Phase
from .config import ChoreographyConfig, QualityConfig
from .quality import FrameQuality

SELECTION_VERSION = "selection/2026-09-16.2"


@dataclass
class SelectedFrame:
    sector: int                      # dwell ordinal -- the localisation claim
    frame_index: int                 # -1 = nothing usable
    t_s: float
    bearing_centre_deg: float
    quality: FrameQuality
    role: str = "keyframe"           # "keyframe" | "alternate"
    rank: int = 0                    # 0 = best in sector
    trim_adjusted: bool = False
    image_path: Optional[str] = None
    flags: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.frame_index >= 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["quality"] = self.quality.to_dict()
        d["usable"] = self.usable
        return d


def _blind(sector: int, phase: Phase, turn_deg: float) -> SelectedFrame:
    return SelectedFrame(
        sector=sector, frame_index=-1, t_s=phase.t0_s,
        bearing_centre_deg=(sector * turn_deg) % 360,
        quality=FrameQuality(index=-1, t_s=phase.t0_s, motion=float("nan"),
                             tenengrad=0.0, laplacian_var=0.0, mean_luma=0.0,
                             clipped_frac=0.0, accepted=False),
        flags=["NO_USABLE_FRAME", "BLIND_SECTOR"],
    )


def select(
    qualities: list[FrameQuality],
    phases: list[Phase],
    cfg: ChoreographyConfig,
    alternates: int = 2,
    min_separation: int = 10,
) -> list[SelectedFrame]:
    """Pick a keyframe plus alternates from each dwell.
    """
    out: list[SelectedFrame] = []
    dwells = [p for p in phases if p.kind == "DWELL"]

    for sector, phase in enumerate(dwells):
        lo = min(phase.first + cfg.settle_head, phase.last)
        hi = max(phase.last - cfg.settle_tail, lo)
        window = [q for q in qualities[lo:hi + 1] if q.accepted]

        adjusted = False
        if not window:
            window = [q for q in qualities[phase.first:phase.last + 1] if q.accepted]
            adjusted = bool(window)

        if not window:
            out.append(_blind(sector, phase, cfg.turn_deg))
            continue

        window.sort(key=lambda q: q.tenengrad, reverse=True)

        chosen: list[FrameQuality] = []
        for q in window:
            if len(chosen) > alternates:
                break
            if all(abs(q.index - c.index) >= min_separation for c in chosen):
                chosen.append(q)

        for rank, q in enumerate(chosen):
            out.append(SelectedFrame(
                sector=sector,
                frame_index=q.index,
                t_s=q.t_s,
                bearing_centre_deg=(sector * cfg.turn_deg) % 360,
                quality=q,
                role="keyframe" if rank == 0 else "alternate",
                rank=rank,
                trim_adjusted=adjusted,
                flags=[f for f in q.flags if f != "MOTION"],
            ))
    return out


def keyframes(selected: list[SelectedFrame]) -> list[SelectedFrame]:
    return [s for s in selected if s.role == "keyframe"]


def flag_relative_texture(selected: list[SelectedFrame], cfg: QualityConfig) -> dict:
    """Flag sectors anomalously flat *for this run*.

    Two failure modes, two flags, because the operator response differs:

      LOW_TEXTURE_ABS -- the scene itself has almost no gradient content. A
                         uniform snowfield trips this on every sector, correctly:
                         the whole run is low-confidence.

      LOW_TEXTURE_REL -- this sector is much flatter than its siblings. Trips on
                         one blank wall in an otherwise textured scan, and is
                         invisible to an absolute threshold tuned for snow.

    All-ABS-no-REL means uniformly poor conditions. One REL means a local blind
    spot. Same metric, opposite conclusions.
    """
    keys = [s for s in keyframes(selected) if s.usable]
    if len(keys) < 3:
        return {"applied": False, "reason": "fewer than three usable sectors"}

    median = float(np.median([s.quality.tenengrad for s in keys]))
    floor = median * cfg.low_texture_rel

    hits = []
    for s in selected:
        if s.usable and s.quality.tenengrad < floor and "LOW_TEXTURE_REL" not in s.flags:
            s.flags.append("LOW_TEXTURE_REL")
            if s.role == "keyframe":
                hits.append(s.sector)

    return {
        "applied": True,
        "run_median_tenengrad": round(median, 1),
        "relative_floor": round(floor, 1),
        "absolute_floor": cfg.low_texture_abs,
        "sectors_below_relative": hits,
        "sectors_below_absolute": [s.sector for s in keys
                                   if "LOW_TEXTURE_ABS" in s.flags],
    }