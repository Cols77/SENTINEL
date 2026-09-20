"""tag-map-v1 loader: indexed AprilTag room map with camera calibration."""

import json
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Tag:
    tag_id: int
    zone_id: str
    x_units: float
    y_units: float
    z_units: float
    yaw_deg: float
    size_m: float
    mounting: str


class TagMap:
    def __init__(self, doc: dict):
        if doc.get("schema_version") != "tag-map-v1":
            raise ValueError(f"unsupported tag map: {doc.get('schema_version')!r}")
        self.doc = doc
        self._by_id = {t["tag_id"]: Tag(
            tag_id=t["tag_id"],
            zone_id=t["zone_id"],
            x_units=t["x_units"],
            y_units=t["y_units"],
            z_units=t["z_units"],
            yaw_deg=t["yaw_deg"],
            size_m=t["size_m"],
            mounting=t["mounting"],
        ) for t in doc["tags"]}
        c = doc["camera_calibration"]
        self.room_scale_m_per_unit = float(doc.get("room_scale_m_per_unit", 0.1))
        self.K = np.array([[c["fx"], 0, c["cx"]], [0, c["fy"], c["cy"]], [0, 0, 1]], dtype=float)
        self.dist = np.array(c["dist_coeffs"], dtype=np.float64)
        # Only "nominal" (or missing) is treated as uncalibrated; "chessboard" means a real calibration (fail-closed default).
        self.calibration_source: str = c.get("calibration_source") or "nominal"

    def tag(self, tag_id: int) -> Tag:
        return self._by_id[tag_id]  # KeyError = unknown tag, fail closed upstream

    def has_tag(self, tag_id: int) -> bool:
        return tag_id in self._by_id


def load_tag_map(path: str) -> TagMap:
    with open(path, "r", encoding="utf-8") as f:
        return TagMap(json.load(f))
