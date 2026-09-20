"""Metric depth, and the conversion from pixels to a position in the world.

This turns a detection into a location. Bearing alone says
"something at 200 degrees". Bearing plus range says "something at 200 degrees,
4.2 metres out", which is a point on a map.

Three things that make this harder than calling the model:

  Sampling. A box contains the object AND background. The naive mean of depth
  over a box is a blend of the two and can land behind the object entirely. We
  sample the central region and take a median, which is robust to the
  background pixels that survive at the box edges.

  Calibration domain. A metric checkpoint is fine-tuned on a particular depth
  range. The Outdoor variant is trained on driving-scale scenes out to ~80m;
  run on a 4m courtyard it will be systematically wrong. Absolute values are
  therefore reported WITH the checkpoint that produced them, and validated
  against tape-measured ground truth before being trusted.

  Scale is a claim, not a measurement. The model infers metric scale from
  learned priors about object sizes. On terrain with no familiar objects --
  a snowfield, a scree slope -- those priors are weak. Range confidence is
  downgraded accordingly rather than reported flat.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import numpy as np
import torch
from PIL import Image
from transformers import AutoImageProcessor, AutoModelForDepthEstimation

from .models import pick_device, pick_dtype

DEPTH_VERSION = "depth/2026-09-16.1"


@dataclass
class Intrinsics:
    """Pinhole camera parameters derived from field of view.

    We have no calibration for this airframe, so fx/fy come from the quoted
    FOV. That is an approximation -- a real calibration with a checkerboard
    would also give distortion coefficients, which the Tello's wide lens
    certainly has. Errors here scale linearly into lateral position, so this
    is worth doing properly before any measurement claim is made in a pitch.
    """
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float
    source: str = "derived_from_fov"

    @classmethod
    def from_fov(cls, width: int, height: int,
                 hfov_deg: float, vfov_deg: float) -> "Intrinsics":
        fx = (width / 2) / math.tan(math.radians(hfov_deg / 2))
        fy = (height / 2) / math.tan(math.radians(vfov_deg / 2))
        return cls(width, height, round(fx, 2), round(fy, 2),
                   width / 2, height / 2)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RangeEstimate:
    range_m: float              # median depth over the sampled region
    p25_m: float                # nearer surface within the box
    p75_m: float                # farther surface
    spread_m: float             # p75 - p25, a proxy for sampling reliability
    n_samples: int
    confidence: str             # HIGH | MODERATE | LOW
    notes: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class WorldPosition:
    """Position in a local frame anchored at the airframe's takeoff point.

    Axes: x east, y north, z up, with north defined as the takeoff heading.
    Not georeferenced -- a standard Tello has no compass reference that would
    make this a real bearing. On a GPS platform this frame becomes ENU anchored
    at a survey point and the same maths applies unchanged.
    """
    x_m: float
    y_m: float
    z_m: float
    bearing_deg: float
    elevation_deg: float
    range_m: float
    frame: str = "local_takeoff_enu"
    provenance: str = "INFERRED"

    def to_dict(self) -> dict:
        return asdict(self)


class DepthEstimator:
    def __init__(self, model_id: str, model_sha: str = "", max_valid_m: float = 120.0):
        self.model_id = model_id
        self.model_sha = model_sha
        self.max_valid_m = max_valid_m
        self.device = pick_device()
        self.dtype = pick_dtype(self.device)
        self.processor = AutoImageProcessor.from_pretrained(model_id)
        self.model = AutoModelForDepthEstimation.from_pretrained(
            model_id, dtype=self.dtype
        ).to(self.device).eval()

    @torch.inference_mode()
    def depth_map(self, image_path: str | Path) -> np.ndarray:
        """Depth in metres, same HxW as the source image."""
        image = Image.open(image_path).convert("RGB")
        inputs = self.processor(images=image, return_tensors="pt")
        inputs = {k: v.to(self.device, self.dtype) for k, v in inputs.items()}
        out = self.model(**inputs)

        # Model output is at its own working resolution; resize to source so
        # box coordinates index it directly without a scale factor.
        depth = torch.nn.functional.interpolate(
            out.predicted_depth.unsqueeze(1).float(),
            size=image.size[::-1], mode="bicubic", align_corners=False,
        ).squeeze()
        return depth.cpu().numpy()


def sample_box(depth: np.ndarray, box, inset: float = 0.25,
               max_valid_m: float = 120.0) -> RangeEstimate:
    """Robust depth for one detection box.

    Insetting by 25% each side discards the border where background leaks in.
    The median resists whatever background survives. A wide p25-p75 spread
    means the box straddles surfaces at different depths, so the single range
    value is less meaningful.
    """
    x1, y1, x2, y2 = (float(v) for v in box)
    h, w = depth.shape
    dx, dy = (x2 - x1) * inset, (y2 - y1) * inset
    ix1 = max(0, int(x1 + dx)); ix2 = min(w, int(x2 - dx))
    iy1 = max(0, int(y1 + dy)); iy2 = min(h, int(y2 - dy))

    notes: list[str] = []
    if ix2 <= ix1 or iy2 <= iy1:                      # inset collapsed a small box
        ix1, iy1 = max(0, int(x1)), max(0, int(y1))
        ix2, iy2 = min(w, int(x2)), min(h, int(y2))
        notes.append("INSET_COLLAPSED_USED_FULL_BOX")

    patch = depth[iy1:iy2, ix1:ix2].ravel()
    valid = patch[np.isfinite(patch) & (patch > 0) & (patch < max_valid_m)]
    if valid.size == 0:
        return RangeEstimate(float("nan"), float("nan"), float("nan"),
                             float("nan"), 0, "LOW", notes + ["NO_VALID_DEPTH"])

    med = float(np.median(valid))
    p25, p75 = (float(np.percentile(valid, q)) for q in (25, 75))
    spread = p75 - p25

    # Spread relative to range: a 1 m spread at 3 m is a problem, at 50 m is not.
    rel = spread / med if med > 0 else float("inf")
    if rel < 0.15:
        conf = "HIGH"
    elif rel < 0.40:
        conf = "MODERATE"
    else:
        conf = "LOW"
        notes.append("BOX_STRADDLES_DEPTHS")

    if valid.size < 50:
        conf = "LOW"
        notes.append("FEW_SAMPLES")

    return RangeEstimate(round(med, 3), round(p25, 3), round(p75, 3),
                         round(spread, 3), int(valid.size), conf, notes)


def to_world(box, range_m: float, sector: int, intr: Intrinsics,
             turn_deg: float = 90.0) -> WorldPosition:
    """Project a box centre plus a range into the local world frame.

    Two steps. First the pinhole model puts the point in camera coordinates
    using the intrinsics. Then the point is rotated by the sector's heading,
    since each dwell looks in a different direction.

    The sector rotation is where the commanded-yaw assumption enters. Loop
    closure is what justifies it, without that check these coordinates would be
    a guess dressed as a measurement.
    """
    x1, y1, x2, y2 = (float(v) for v in box)
    u, v = (x1 + x2) / 2, (y1 + y2) / 2

    # Camera frame: X right, Y down, Z forward along the optical axis.
    Xc = (u - intr.cx) * range_m / intr.fx
    Yc = (v - intr.cy) * range_m / intr.fy
    Zc = range_m

    yaw = math.radians((sector * turn_deg) % 360)
    east = Zc * math.sin(yaw) + Xc * math.cos(yaw)
    north = Zc * math.cos(yaw) - Xc * math.sin(yaw)
    up = -Yc                                          # image Y is down

    return WorldPosition(
        x_m=round(east, 3), y_m=round(north, 3), z_m=round(up, 3),
        bearing_deg=round(math.degrees(math.atan2(east, north)) % 360, 1),
        elevation_deg=round(math.degrees(math.atan2(up, math.hypot(east, north))), 1),
        range_m=round(range_m, 3),
    )


def validate_against_truth(estimates: list[tuple[float, float]]) -> dict:
    """Compare predicted ranges with tape-measured ground truth. (for later)
    """
    if not estimates:
        return {"n": 0}
    pred = np.array([p for p, _ in estimates], dtype=float)
    true = np.array([t for _, t in estimates], dtype=float)
    err = pred - true
    scale = float(np.median(true / pred)) if np.all(pred > 0) else float("nan")
    return {
        "n": len(estimates),
        "mae_m": round(float(np.mean(np.abs(err))), 3),
        "rmse_m": round(float(np.sqrt(np.mean(err ** 2))), 3),
        "mean_rel_error": round(float(np.mean(np.abs(err) / true)), 4),
        "bias_m": round(float(np.mean(err)), 3),
        "suggested_scale_correction": round(scale, 4),
        "note": ("apply suggested_scale_correction only if bias is consistent "
                 "across the measured range; a single-point correction on a "
                 "domain-mismatched checkpoint will not generalise"),
    }