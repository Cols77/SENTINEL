# src/sentinel/survey/geometry.py
"""Image-space to room-space arithmetic. Pure functions, no I/O.

rev 3 R1 replaces cross-stop triangulation as the *primary* measurement
with ground-plane depth: ``D = delta_z * f / v``, where ``delta_z`` is the
camera's altitude above the surface the objects rest on and ``v`` is the
box centre's pixel offset below the image centre. An adversarial review
proved triangulation from *commanded* (not measured) stop positions turns
~15 cm of per-stop position error into a depth estimate that is wrong by
roughly a factor of two, and additionally manufactures an organised phantom
lattice from evenly spaced real objects (design spec 5.2.1).

``ray_intersection`` and ``bearing_of`` survive unchanged: a bearing still
gives the horizontal half of a position, and crossing rays still serve as a
cross-check in ``merge.py``, just never as the sole source of a position.
"""
from __future__ import annotations

import math

from .observability import FOCAL_PX, PRINCIPAL_X, PRINCIPAL_Y


def bearing_of(bbox: dict) -> float:
    """Horizontal angle from the optical axis to the box's centre, in
    radians, positive to the right. Only the horizontal centre is used: the
    merge needs a bearing, not a tight box, which is the least demanding
    thing a grounding model can produce."""
    centre_u = bbox["x"] + bbox["width"] / 2.0
    return math.atan((centre_u - PRINCIPAL_X) / FOCAL_PX)


def ground_depth(bbox: dict, delta_z_cm: float) -> float:
    """The primary depth measurement (design spec 5.2): ``D = delta_z *
    f / v``, ``v`` being the box centre's pixel offset *below* the image
    centre (image centre row is ``PRINCIPAL_Y``). Sanity-pinned in
    ``tests/survey/test_geometry.py`` against the spec's own worked
    example: at ``delta_z`` 55 cm and ``D`` 300 cm, ``v`` is ~125 px and
    ``dD/dv`` is ~2.40 cm/px.

    A box centred at or above the image centre is not resting on the
    measured plane below the camera -- there is nothing to solve for, and
    guessing would manufacture a position from nothing (the same rule
    ``bbox`` itself already follows: never manufactured from prose)."""
    centre_v = bbox["y"] + bbox["height"] / 2.0
    v_px = centre_v - PRINCIPAL_Y
    if v_px <= 0:
        raise ValueError(
            "box centre is at or above the image centre; no ground-plane depth")
    return delta_z_cm * FOCAL_PX / v_px


def ray_direction(heading_deg: float, bearing_rad: float) -> tuple[float, float]:
    """Room-frame unit direction. Heading 0 looks along +y; +x is right."""
    angle = math.radians(heading_deg) + bearing_rad
    return (math.sin(angle), math.cos(angle))


def position_of(sighting: dict, delta_z_cm: float) -> tuple[float, float]:
    """Combine ``ground_depth`` (range) with the horizontal bearing to
    recover one sighting's absolute room position from a single frame
    (design spec 5.2: "one sighting gives a full (x, y) from one frame").
    Needs the camera's own commanded position at that stop
    (``frame_x_cm``/``frame_y_cm``, falling back to ``x_cm``/``y_cm`` for
    callers that have not yet disambiguated the two -- see
    ``store.sightings()``, which aliases the frame's position to avoid
    colliding with the sighting's own computed ``x_cm``/``y_cm`` columns)."""
    depth_cm = ground_depth(sighting["bbox"], delta_z_cm)
    bearing = bearing_of(sighting["bbox"]) + math.radians(sighting.get("look_deg", 0.0))
    dx, dy = ray_direction(sighting["heading_deg"], bearing)
    origin_x = sighting.get("frame_x_cm", sighting.get("x_cm"))
    origin_y = sighting.get("frame_y_cm", sighting.get("y_cm"))
    return (origin_x + depth_cm * dx, origin_y + depth_cm * dy)


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
