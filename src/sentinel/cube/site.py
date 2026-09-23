"""cube-site-v1: one description of the physical setup.

A straight line of tag stands at y = line_y_m, tags facing -y, and the operating
cube in front of it. The tag map, the flight envelope and the containment
footprint are all derived from this one file so they cannot drift apart.
"""
import json
from dataclasses import dataclass
from pathlib import Path

from sentinel.envelope import FlightEnvelope
from sentinel.pose.containment import CubeFootprint

ROOM_SCALE_M_PER_UNIT = 0.1
MIN_LINE_STANDOFF_M = 0.5
MAX_LEG_CM = 300


@dataclass(frozen=True)
class Stand:
    x_m: float
    first_tag_id: int
    heights_m: tuple[float, ...]
    size_m: float


@dataclass(frozen=True)
class Site:
    line_y_m: float
    stands: tuple[Stand, ...]
    cube_min_x_m: float
    cube_max_x_m: float
    cube_min_y_m: float
    cube_max_y_m: float
    cube_max_z_m: float
    inset_m: float
    min_z_cm: int
    max_z_cm: int
    #: Height of the takeoff surface in site-frame cm (the survey's CubeFlight reads the same
    #: field). The Tello's `height?` is relative to the takeoff point, and commanded altitudes are
    #: `height?` values, so the drone's height in the room is commanded + takeoff_z_cm (+ bias).
    takeoff_z_cm: float = 0.0
    #: How much higher the drone hovers than `height?` + takeoff_z_cm (positive: higher). The
    #: 2026-09-21/22 ground takeoffs need ~18 cm for the model to reproduce which stops lost the
    #: tags (they sat on the bottom edge of the frame), and the tag centres were tape-measured at
    #: 53 cm, so it is the altimeter that reads low. The survey's CubeFlight adds it too.
    altitude_bias_cm: float = 0.0

    @property
    def height_offset_cm(self) -> float:
        """Room height of the drone minus its commanded altitude."""
        return self.takeoff_z_cm + self.altitude_bias_cm

    def tag_map_doc(self, camera_calibration: dict) -> dict:
        tags = []
        for index, stand in enumerate(self.stands):
            for k, height in enumerate(stand.heights_m):
                tags.append({
                    "tag_id": stand.first_tag_id + k, "zone_id": f"STAND_{index}",
                    "x_units": round(stand.x_m / ROOM_SCALE_M_PER_UNIT, 4),
                    "y_units": round(self.line_y_m / ROOM_SCALE_M_PER_UNIT, 4),
                    "z_units": height, "yaw_deg": -90.0, "size_m": stand.size_m,
                    "mounting": "WALL"})
        return {"schema_version": "tag-map-v1", "tag_family": "36h11",
                "room": {"width_units": 100, "height_units": 100,
                         "origin": "LEFT_ALLEY_FRONT_CORNER"},
                "room_scale_m_per_unit": ROOM_SCALE_M_PER_UNIT, "tags": tags,
                "camera_calibration": camera_calibration}

    def envelope(self) -> FlightEnvelope:
        """Commanded bounds: the cube inset by the open-loop drift margin."""
        return FlightEnvelope(
            min_x_cm=round((self.cube_min_x_m + self.inset_m) * 100),
            max_x_cm=round((self.cube_max_x_m - self.inset_m) * 100),
            min_y_cm=round((self.cube_min_y_m + self.inset_m) * 100),
            max_y_cm=round((self.cube_max_y_m - self.inset_m) * 100),
            min_z_cm=self.min_z_cm, max_z_cm=self.max_z_cm, max_move_cm=MAX_LEG_CM)

    def footprint(self) -> CubeFootprint:
        # round: 0.3 * 100 is 30.000000000000004 in binary floating point
        return CubeFootprint(round(self.cube_min_x_m * 100, 1), round(self.cube_max_x_m * 100, 1),
                             round(self.cube_min_y_m * 100, 1), round(self.cube_max_y_m * 100, 1))


def parse_site(doc: dict) -> Site:
    if doc.get("schema_version") != "cube-site-v1":
        raise ValueError(f"unsupported site file: {doc.get('schema_version')!r}")
    stands = tuple(Stand(float(s["x_m"]), int(s["first_tag_id"]),
                         tuple(float(h) for h in s["heights_m"]), float(s["size_m"]))
                   for s in doc["stands"])
    if not stands or any(not s.heights_m for s in stands):
        raise ValueError("a site needs at least one stand, each with at least one tag height")
    ids = [s.first_tag_id + k for s in stands for k in range(len(s.heights_m))]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate tag ids across stands: {sorted(ids)}")
    cube, commanded = doc["cube"], doc["commanded"]
    site = Site(float(doc["line_y_m"]), stands, float(cube["min_x_m"]), float(cube["max_x_m"]),
                float(cube["min_y_m"]), float(cube["max_y_m"]), float(cube["max_z_m"]),
                float(commanded["inset_m"]), int(commanded["min_z_cm"]), int(commanded["max_z_cm"]),
                float(doc.get("takeoff_z_cm", 0.0)), float(doc.get("altitude_bias_cm", 0.0)))
    if site.cube_max_y_m > site.line_y_m - MIN_LINE_STANDOFF_M:
        raise ValueError(f"the cube must end at least {MIN_LINE_STANDOFF_M} m before the tag line")
    if site.max_z_cm > site.cube_max_z_m * 100 or site.min_z_cm >= site.max_z_cm:
        raise ValueError("the commanded altitude band must lie inside the cube")
    env = site.envelope()
    if env.min_x_cm >= env.max_x_cm or env.min_y_cm >= env.max_y_cm or env.min_x_cm < 0:
        raise ValueError("the inset leaves no commanded area inside the cube")
    return site


def load_site(path) -> Site:
    return parse_site(json.loads(Path(path).read_text(encoding="utf-8")))
