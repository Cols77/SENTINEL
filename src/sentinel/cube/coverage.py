"""Can the camera localise from a stop? A pre-flight guard derived from the site file.

The tags' positions and heights come from the cube-site-v1 file and the camera from its chessboard
calibration, so a different tag layout needs a different site file and nothing else. Each stop is
projected through the camera (``pose.layout``: 15 px frame border, 65 degree obliquity limit) not
only where it is commanded but where the drone may really hover: anywhere on the tolerance circle,
with the heading off by the measured bias and more, the airframe tilted, and the altitude off.

- BLIND: some of those poses see no tag at all. The runner refuses the stop.
- MARGINAL: every pose sees a tag, but not always two, or the predicted fix error exceeds the
  tolerance. Flown, with an alert.
- OK: two tags or more from every pose, and a predicted fix inside the tolerance.

Calibrated against the 2026-09-21/22 flights (tests/cube/test_coverage.py). Once the site's
drone's height was corrected (``Site.altitude_bias_cm``: it hovers ~18 cm above its `height?`),
the stop that saw no tag in 287 frames is BLIND, the stop that needed three trims is refused too,
and every stop that localised at the first fix is OK or MARGINAL.

Each error is applied alone, at its bound, with the others nominal: all of them at their bounds at
once made the stops that localised every time BLIND, which the flights contradict.
"""
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from sentinel.pose.layout import _camera, _visible, predict_cell
from sentinel.pose.tagmap import TagMap

OK, MARGINAL, BLIND = "OK", "MARGINAL", "BLIND"
#: Where the drone may hover when it "arrives": the tolerance circle (mission.TOLERANCE_CM).
ARRIVAL_OFFSETS_CM = ((0, 0), (15, 0), (-15, 0), (0, 15), (0, -15))
#: Heading error: the trials measured a constant +5 degrees; the yaw trim only acts beyond 10.
YAW_ERRORS_DEG = (-6.0, 0.0, 6.0)
#: Airframe tilt while holding position.
PITCH_ERRORS_DEG = (-3.0, 0.0, 3.0)
#: The altitude hold's error around the commanded height (mission.DEAD_BAND_CM).
ALTITUDE_ERRORS_CM = (-10, 0, 10)
#: A predicted fix error above the tolerance circle makes a stop MARGINAL.
MAX_P95_CM = 15.0


def _one_error_at_a_time():
    yield 0, 0, 0, 0.0, 0.0
    for dx, dy in ARRIVAL_OFFSETS_CM:
        yield dx, dy, 0, 0.0, 0.0
    for dz in ALTITUDE_ERRORS_CM:
        yield 0, 0, dz, 0.0, 0.0
    for yaw in YAW_ERRORS_DEG:
        yield 0, 0, 0, yaw, 0.0
    for pitch in PITCH_ERRORS_DEG:
        yield 0, 0, 0, 0.0, pitch


@dataclass(frozen=True)
class Coverage:
    level: str
    nominal_tags: int
    min_tags: int
    p95_cm: float | None

    def describe(self) -> str:
        p95 = "-" if self.p95_cm is None else f"{self.p95_cm:.0f} cm"
        return (f"{self.level}: {self.nominal_tags} tag(s) in frame as commanded, "
                f"{self.min_tags} in the worst case, predicted fix error p95 {p95}")


class CoverageModel:
    def __init__(self, site, calibration: dict) -> None:
        self.site = site
        self.tag_map = TagMap(site.tag_map_doc(calibration))

    @classmethod
    def from_files(cls, site, calibration_path) -> "CoverageModel":
        """``calibration_path``: any JSON file with a ``camera_calibration`` block (a tag map)."""
        doc = json.loads(Path(calibration_path).read_text(encoding="utf-8"))
        return cls(site, doc["camera_calibration"])

    def _room_point(self, x_cm: float, y_cm: float, z_cm: float) -> np.ndarray:
        return np.array([x_cm, y_cm, z_cm + self.site.height_offset_cm], dtype=float) / 100.0

    def tags_in_view(self, x_cm: float, y_cm: float, z_cm: float, *,
                     yaw_err_deg: float = 0.0, pitch_err_deg: float = 0.0) -> int:
        """Tags fully in frame from a commanded altitude ``z_cm`` (the altimeter's reading)."""
        pos = self._room_point(x_cm, y_cm, z_cm)
        R, t = _camera(pos, yaw_err_deg, pitch_err_deg)
        return len(_visible(self.tag_map, pos, R, t))

    def verdict(self, x_cm: float, y_cm: float, z_cm: float) -> Coverage:
        nominal = self.tags_in_view(x_cm, y_cm, z_cm)
        worst = min(self.tags_in_view(x_cm + dx, y_cm + dy, z_cm + dz,
                                      yaw_err_deg=yaw, pitch_err_deg=pitch)
                    for dx, dy, dz, yaw, pitch in _one_error_at_a_time())
        if worst == 0:
            return Coverage(BLIND, nominal, 0, None)
        pos = self._room_point(x_cm, y_cm, z_cm)
        p95 = predict_cell(self.tag_map, *pos, rng=np.random.default_rng(0), trials=40)["p95_cm"]
        good = worst >= 2 and p95 is not None and p95 <= MAX_P95_CM
        return Coverage(OK if good else MARGINAL, nominal, worst, p95)

    def grid(self, z_cm: int, step_cm: int = 25) -> list[tuple[int, list[tuple[int, Coverage]]]]:
        """Verdicts over the commanded area, one row per y (farthest from the tags first)."""
        env = self.site.envelope()
        xs = list(range(env.min_x_cm, env.max_x_cm + 1, step_cm))
        ys = list(range(env.min_y_cm, env.max_y_cm + 1, step_cm))
        return [(y, [(x, self.verdict(x, y, z_cm)) for x in xs]) for y in ys]


def check_tag_map_matches_site(tag_map, site, tolerance_cm: float = 1.0) -> None:
    """The guard predicts from the site file, localisation solves against the tag map: refuse a
    flight where the two describe different tags."""
    expected = {t["tag_id"]: t for t in site.tag_map_doc({})["tags"]}
    actual = {t["tag_id"]: t for t in tag_map.doc["tags"]}
    if set(expected) != set(actual):
        raise ValueError(f"tag map ids {sorted(actual)} differ from the site file's {sorted(expected)}")
    scale_cm = 100.0 * site_scale(site)
    for tag_id, want in expected.items():
        got = actual[tag_id]
        deltas = (abs(want["x_units"] - got["x_units"]) * scale_cm,
                  abs(want["y_units"] - got["y_units"]) * scale_cm,
                  abs(want["z_units"] - got["z_units"]) * 100.0)
        if max(deltas) > tolerance_cm:
            raise ValueError(f"tag {tag_id} sits elsewhere in the tag map than in the site file "
                             f"(dx, dy, dz = {deltas[0]:.0f}, {deltas[1]:.0f}, {deltas[2]:.0f} cm)")


def site_scale(site) -> float:
    return site.tag_map_doc({})["room_scale_m_per_unit"]
