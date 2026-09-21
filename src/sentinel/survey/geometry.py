# src/sentinel/survey/geometry.py
"""Image-space to room-space arithmetic. Pure functions, no I/O.

rev 3 R1 replaces cross-stop triangulation as the *primary* measurement
with ground-plane depth. An adversarial review proved triangulation from
*commanded* (not measured) stop positions turns ~15 cm of per-stop position
error into a depth estimate that is wrong by roughly a factor of two, and
additionally manufactures an organised phantom lattice from evenly spaced
real objects (design spec 5.2.1).

rev 5 states that measurement as what it is -- the intersection of the
pixel's ray with a horizontal plane ``delta_z`` below the lens -- instead of
the shortcut ``D = delta_z * f / v``. The shortcut is the special case of a
level optical axis, and the Tello's axis is not level: it looks 8.6 degrees
down, so the horizon is near image row 219 and an object 3 m away on a
plane 55 cm below the lens sits 28 px below the principal row, not 125 px.
The shortcut would have placed it beyond 17 m. The same revision fixes a
second error: the shortcut's ``D`` is the *forward* distance, and rev 3-4
then used it as a slant range along the bearing, shortening every off-axis
position by cos(bearing).

``ray_intersection`` and ``bearing_of`` survive: crossing rays still serve
as a cross-check in ``merge.py``, never as the sole source of a position.
"""
from __future__ import annotations

import math

from .observability import SYNTHETIC_LEVEL, CameraModel


def _centre(bbox: dict) -> tuple[float, float]:
    return (bbox["x"] + bbox["width"] / 2.0, bbox["y"] + bbox["height"] / 2.0)


def bearing_of(bbox: dict, camera: CameraModel = SYNTHETIC_LEVEL) -> float:
    """Horizontal angle from the optical axis to the box's centre, in
    radians, positive to the right. Only the horizontal centre is used: the
    merge needs a bearing, not a tight box, which is the least demanding
    thing a grounding model can produce."""
    centre_u, _ = _centre(bbox)
    return math.atan((centre_u - camera.cx) / camera.fx)


def ground_point(bbox: dict, delta_z_cm: float,
                 camera: CameraModel = SYNTHETIC_LEVEL) -> tuple[float, float]:
    """Where the ray through the box centre meets the horizontal plane
    ``delta_z_cm`` below the lens, as ``(lateral_cm, forward_cm)`` in the
    camera's own heading frame (lateral positive to the right).

    In camera axes the ray is ``(x_n, y_n, 1)`` with ``y_n`` positive down.
    Pitching the axis down by ``p`` gives it a downward component
    ``sin p + y_n cos p`` and a forward component ``cos p - y_n sin p``.

    A box centred at or above the horizon row is not resting on a plane
    below the camera -- there is nothing to solve for, and guessing would
    manufacture a position from nothing (the same rule ``bbox`` itself
    already follows: never manufactured from prose)."""
    centre_u, centre_v = _centre(bbox)
    x_n = (centre_u - camera.cx) / camera.fx
    y_n = (centre_v - camera.cy) / camera.fy
    pitch = math.radians(camera.pitch_down_deg)
    down = math.sin(pitch) + y_n * math.cos(pitch)
    if down <= 0:
        raise ValueError(
            "box centre is at or above the horizon; no ground-plane depth")
    scale = delta_z_cm / down
    return (scale * x_n, scale * (math.cos(pitch) - y_n * math.sin(pitch)))


def ground_depth(bbox: dict, delta_z_cm: float,
                 camera: CameraModel = SYNTHETIC_LEVEL) -> float:
    """The primary depth measurement (design spec 5.2): the *forward*
    distance to the box centre's ground point. For a level camera this is
    exactly ``delta_z * f / v``. Sanity-pinned in
    ``tests/survey/test_geometry.py`` against the spec's worked examples
    for both camera models."""
    return ground_point(bbox, delta_z_cm, camera)[1]


def ray_direction(heading_deg: float, bearing_rad: float) -> tuple[float, float]:
    """Room-frame unit direction. Heading 0 looks along +y; +x is right."""
    angle = math.radians(heading_deg) + bearing_rad
    return (math.sin(angle), math.cos(angle))


def position_of(sighting: dict, delta_z_cm: float,
                camera: CameraModel = SYNTHETIC_LEVEL) -> tuple[float, float]:
    """One sighting's absolute room position from a single frame (design
    spec 5.2: "one sighting gives a full (x, y) from one frame"): the
    ground point, rotated from the camera's heading frame into the room's.
    Needs the camera's own commanded position at that stop
    (``frame_x_cm``/``frame_y_cm``, falling back to ``x_cm``/``y_cm`` for
    callers that have not yet disambiguated the two -- see
    ``store.sightings()``, which aliases the frame's position to avoid
    colliding with the sighting's own computed ``x_cm``/``y_cm`` columns)."""
    lateral_cm, forward_cm = ground_point(sighting["bbox"], delta_z_cm, camera)
    angle = math.radians(sighting["heading_deg"] + sighting.get("look_deg", 0.0))
    origin_x = sighting.get("frame_x_cm", sighting.get("x_cm"))
    origin_y = sighting.get("frame_y_cm", sighting.get("y_cm"))
    return (origin_x + forward_cm * math.sin(angle) + lateral_cm * math.cos(angle),
            origin_y + forward_cm * math.cos(angle) - lateral_cm * math.sin(angle))


def ray_intersection(p1, d1, p2, d2) -> tuple[float, float] | None:
    """Where two rays cross, or None if they are parallel or cross behind
    either origin. Demoted by rev 3 R1 to a cross-check: it must not derive
    a position on its own."""
    denominator = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(denominator) < 1e-9:
        return None
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    t1 = (dx * d2[1] - dy * d2[0]) / denominator
    t2 = (dx * d1[1] - dy * d1[0]) / denominator
    if t1 <= 0 or t2 <= 0:
        return None
    return (p1[0] + t1 * d1[0], p1[1] + t1 * d1[1])
