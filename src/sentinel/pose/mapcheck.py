"""Truth-free tag-map QA on recorded frames: single-tag vs joint PnP spread, and
the joint reprojection residual per zone (a mis-measured stand shows up as a
persistently high residual)."""
import math
import statistics
from typing import Iterable

import numpy as np

from sentinel.pose.detector import estimate_pose
from sentinel.pose.tagmap import TagMap


def _summary(points: list[tuple[float, float]], cm_per_unit: float) -> dict:
    if not points:
        return {"n": 0, "spread_p95_cm": None, "median_x_units": None, "median_y_units": None}
    mx = statistics.median(p[0] for p in points)
    my = statistics.median(p[1] for p in points)
    dist = sorted(math.hypot(x - mx, y - my) for x, y in points)
    return {"n": len(points), "spread_p95_cm": dist[int(0.95 * (len(dist) - 1))] * cm_per_unit,
            "median_x_units": mx, "median_y_units": my}


def compare_frames(frames: Iterable[np.ndarray], tag_map: TagMap) -> dict:
    cm_per_unit = tag_map.room_scale_m_per_unit * 100.0
    total = 0
    single, joint = [], []
    residuals: dict[str, list[float]] = {}
    for frame in frames:
        total += 1
        try:
            j = estimate_pose(frame, tag_map, run_id="mapcheck", report_id="j")
        except ValueError:
            continue
        if len(j.tag_observations) < 2:
            continue  # only frames where the joint path actually ran are comparable
        s = estimate_pose(frame, tag_map, run_id="mapcheck", report_id="s", joint=False)
        joint.append(j.point)
        single.append(s.point)
        for zone in {o.zone_id for o in j.tag_observations}:
            residuals.setdefault(zone, []).append(j.reprojection_error_px)
    return {
        "frames": total,
        "multi_tag_frames": len(joint),
        "single": _summary(single, cm_per_unit),
        "joint": _summary(joint, cm_per_unit),
        "zones": {zone: {"n": len(v), "median_joint_reprojection_px": statistics.median(v)}
                  for zone, v in sorted(residuals.items())},
    }
