# src/sentinel/survey/merge.py
"""Sightings to distinct objects.

Code counts; the model never does. Vision-language models are documented to
count poorly (hallucination, poor individuation, double counting, and a
consistent undercount bias), so the provider is asked only *where* each
object is and the arithmetic happens here.

rev 3 R1 replaces this module's original triangulating ``_cluster`` (rev
1-2) entirely -- deleted, not fixed. An adversarial review proved it: stop
positions are *commanded*, not measured, with ~15 cm error at each end, so
a believed 75 cm baseline is really 45-105 cm and a single laptop at 3 m
triangulates to depths of 214-500 cm, clustering into two CONFIRMED objects
plus one UNCERTAIN. Evenly spaced real objects additionally generate an
organised phantom lattice that itself passes the CONFIRMED stop-count gate.

Position now comes from ``geometry.position_of`` -- a single frame's
ground-plane depth combined with its horizontal bearing (design spec 5.2).
Cross-stop ray crossings (``geometry.ray_intersection``) are demoted to a
cross-check and never derive a position here.

Association is mutual-nearest-neighbour (design spec 5.4): sightings are
merged single-linkage, always taking the globally closest still-unclaimed
pair first, so a sighting is claimed by at most one growing object and
never accumulated into every cluster it happens to be near. That is what
let rev 1-2's phantoms reach ``distinct_stops >= 3``.
"""
from __future__ import annotations

import math
from statistics import median

from .geometry import ground_depth, position_of
from .observability import SYNTHETIC_LEVEL, CameraModel

MERGE_MIN_RANGE_CM = 50.0
MERGE_MAX_RANGE_CM = 1200.0
CONFIRM_MIN_STOPS = 3

#: rev 5: the spread is the widest gap between the positions the member
#: sightings recover, as a fraction of the median viewing depth. Rev 3-4
#: compared the viewing depths themselves, which differ legitimately as soon
#: as one object is seen from two distances -- so a closer follow-up look
#: marked every object it re-saw as degraded, the opposite of its purpose.
#: A per-object depth spread above this fraction of the median depth means
#: the geometry disagrees with itself for that object (design spec 5.4):
#: it stays UNCERTAIN and counts toward the sweep's geometry_degraded tally.
DEGRADED_SPREAD_PCT = 30.0

#: When a sighting's second-closest candidate object is within this factor
#: of its closest, the match is "nearly equidistant" (design spec 5.4) and
#: not safe to call CONFIRMED -- both groups it could join are marked
#: ambiguous even though exactly one of them claims the sighting.
AMBIGUITY_FACTOR = 1.15

#: Classes that move between stops, so their positions cannot be merged
#: (design spec 4.3): reported as a single-view lower bound instead.
MOBILE_CLASSES = frozenset({"person"})


def _tolerance_cm(range_cm: float) -> float:
    """ALONG the line of sight. Grows with distance: depth comes from a
    shallow ray meeting a plane, so one degree of camera pitch is 9% of
    range, and a fixed tolerance would be wrong at both ends."""
    return max(30.0, 0.25 * range_cm)


#: ACROSS the line of sight the error is where the camera really was, so it
#: depends on whether the frame's position was measured from the tags or
#: dead-reckoned. Set from the WP5 session's Monte Carlo of 2026-09-22 (300
#: trials per cell; 12 cm per-axis drift for a commanded stop, 6 cm for a
#: measured one; boxes perfect; one look per stop): with COMMANDED frames a
#: lower floor splits single laptops faster than it separates pairs (at a
#: 20 cm floor 27% of single laptops split while 35 cm pairs still fuse
#: 76%), so nothing closer than ~60 cm can be told apart and the floor stays
#: wide. With MEASURED frames 22 cm / 9% separates 35-40 cm pairs at no
#: split cost in that model. Real drift is correlated along a leg, which the
#: model ignores; the first real flight is what checks these.
LATERAL_FLOOR_CM = {"MEASURED": 22.0, "COMMANDED": 45.0}
LATERAL_FRACTION = {"MEASURED": 0.09, "COMMANDED": 0.18}


def _lateral_tolerance_cm(range_cm: float, commanded: bool) -> float:
    """ACROSS the line of sight, which is far better measured than depth:
    a bearing error of a degree is under 2% of range, so what is left is
    where the camera really was. rev 6: one round tolerance of 25% of range
    fused any two laptops closer than ~75 cm at 3 m -- a whole desk."""
    kind = "COMMANDED" if commanded else "MEASURED"
    return max(LATERAL_FLOOR_CM[kind], LATERAL_FRACTION[kind] * range_cm)


def _frame_key(sighting: dict):
    frame_id = sighting.get("frame_id")
    if frame_id is not None:
        return frame_id
    return (sighting.get("sweep_id"), sighting.get("stop_index"), sighting.get("look_deg", 0.0))


def _iou(a: dict, b: dict) -> float:
    left, top = max(a["x"], b["x"]), max(a["y"], b["y"])
    right = min(a["x"] + a["width"], b["x"] + b["width"])
    bottom = min(a["y"] + a["height"], b["y"] + b["height"])
    overlap = max(0.0, right - left) * max(0.0, bottom - top)
    union = a["width"] * a["height"] + b["width"] * b["height"] - overlap
    return overlap / union if union > 0 else 0.0


_BAND_RANK = {"high": 2, "medium": 1, "low": 0}
IN_FRAME_DUPLICATE_IOU = 0.5


def _without_in_frame_duplicates(sightings: list[dict]) -> tuple[list[dict], dict[str, list[str]]]:
    """Two sightings in one frame are two objects -- unless they are the same
    box twice, which a provider does produce. Heavily overlapping boxes in one
    frame collapse onto the better-banded one; its duplicates stay attached to it."""
    kept: list[dict] = []
    absorbed: dict[str, list[str]] = {}
    ranked = sorted(sightings, key=lambda s: -_BAND_RANK.get(s.get("band"), 0))
    for sighting in ranked:
        bbox = sighting.get("bbox")
        twin = next((k for k in kept if bbox and k.get("bbox")
                     and _frame_key(k) == _frame_key(sighting)
                     and _iou(k["bbox"], bbox) >= IN_FRAME_DUPLICATE_IOU), None)
        if twin is None:
            kept.append(sighting)
        else:
            absorbed.setdefault(twin["sighting_id"], []).append(sighting["sighting_id"])
    return kept, absorbed


def _locate(sighting: dict, default_delta_z_cm: float,
            camera: CameraModel = SYNTHETIC_LEVEL):
    """Return (x_cm, y_cm, depth_cm) for a sighting that can be placed on
    the ground plane, or None when it cannot: no box, or the box is not on
    the measured plane (``ground_depth`` raises for those)."""
    bbox = sighting.get("bbox")
    if bbox is None:
        return None
    delta_z_cm = sighting.get("delta_z_cm", default_delta_z_cm)
    try:
        depth_cm = ground_depth(bbox, delta_z_cm, camera)
        x_cm, y_cm = position_of(sighting, delta_z_cm, camera)
    except (KeyError, ValueError, ZeroDivisionError, TypeError):
        return None
    return (x_cm, y_cm, depth_cm)


def _unmerged(sighting: dict, x_cm=None, y_cm=None) -> dict:
    return {"class": sighting["class"], "x_cm": x_cm, "y_cm": y_cm,
            "distinct_stops": 1, "band": "UNCERTAIN",
            "appearance": sighting.get("description"),
            "sighting_ids": [sighting["sighting_id"]],
            "depth_spread_pct": None}


def _object_from_group(group: dict, sighting_by_id: dict) -> dict:
    depths = group["depths"]
    stops = group["stops"]
    points = group["points"]
    widest = max((math.hypot(a[0] - b[0], a[1] - b[1])
                  for i, a in enumerate(points) for b in points[i + 1:]), default=0.0)
    spread_pct = widest / median(depths) * 100.0 if len(points) > 1 else None
    degraded = spread_pct is not None and spread_pct > DEGRADED_SPREAD_PCT
    confident = all(band != "low" for band in group["bands"])
    confirmed = (len(stops) >= CONFIRM_MIN_STOPS and confident
                 and not degraded and not group["ambiguous"])
    appearance = next((sighting_by_id[sid].get("description") for sid in group["ids"]
                       if sighting_by_id[sid].get("description")), None)
    class_name = sighting_by_id[group["ids"][0]]["class"]
    return {
        "class": class_name,
        "x_cm": group["x_cm"], "y_cm": group["y_cm"],
        "distinct_stops": len(stops),
        "band": "CONFIRMED" if confirmed else "UNCERTAIN",
        "appearance": appearance,
        "sighting_ids": sorted(group["ids"]),
        "depth_spread_pct": spread_pct,
    }


def _separation(a: dict, b: dict, mean_depth: float) -> float:
    """How far apart two groups are, in units of the tolerance: 1.0 is the
    edge of being the same object. The gap is split along and across the
    mean line of sight, because the two are measured very differently well.
    With no camera position on record it falls back to the round tolerance."""
    dx, dy = b["x_cm"] - a["x_cm"], b["y_cm"] - a["y_cm"]
    cams = a["cams"] + b["cams"]
    mid_x, mid_y = (a["x_cm"] + b["x_cm"]) / 2.0, (a["y_cm"] + b["y_cm"]) / 2.0
    sight_x = sum(mid_x - cx for cx, _ in cams)
    sight_y = sum(mid_y - cy for _, cy in cams)
    norm = math.hypot(sight_x, sight_y)
    if not cams or norm < 1e-6:
        return math.hypot(dx, dy) / _tolerance_cm(mean_depth)
    ux, uy = sight_x / norm, sight_y / norm
    along, across = abs(dx * ux + dy * uy), abs(dx * uy - dy * ux)
    lateral = _lateral_tolerance_cm(mean_depth, a["commanded"] or b["commanded"])
    return math.hypot(along / _tolerance_cm(mean_depth), across / lateral)


def associate(sightings: list[dict], delta_z_cm: float = 55.0,
              camera: CameraModel = SYNTHETIC_LEVEL) -> list[dict]:
    """Merge one class's sightings into distinct objects via
    mutual-nearest-neighbour single-linkage: each sighting is claimed by
    exactly one object. Sightings that cannot be placed on the ground plane
    (no box, or the box is not on the measured surface) are returned as
    their own UNCERTAIN, unpositioned object -- never dropped."""
    if not sightings:
        return []

    sighting_by_id = {s["sighting_id"]: s for s in sightings}
    sightings, absorbed = _without_in_frame_duplicates(sightings)
    groups: dict[str, dict] = {}
    unpositioned: list[dict] = []
    for sighting in sightings:
        located = _locate(sighting, delta_z_cm, camera)
        if located is None:
            unpositioned.append(sighting)
            continue
        x_cm, y_cm, depth_cm = located
        sid = sighting["sighting_id"]
        cam = (sighting.get("frame_x_cm", sighting.get("x_cm")),
               sighting.get("frame_y_cm", sighting.get("y_cm")))
        groups[sid] = {"ids": [sid, *absorbed.get(sid, [])], "x_cm": x_cm, "y_cm": y_cm,
                       "points": [(x_cm, y_cm)], "frames": {_frame_key(sighting)},
                       "cams": [cam] if None not in cam else [],
                       "commanded": sighting.get("position_kind") == "COMMANDED",
                       # rev 6: a stop is a viewpoint of ONE sweep. Keyed on stop_index
                       # alone, a follow-up's stop 0 was the rail's stop 0 again, so a
                       # second look could never add the viewpoint that confirms.
                       "depths": [depth_cm],
                       "stops": {(sighting.get("sweep_id"), sighting.get("stop_index"))},
                       "bands": [sighting.get("band")], "ambiguous": False}

    while len(groups) > 1:
        ids = list(groups)
        candidates = []
        compatible = set()
        for i, id_a in enumerate(ids):
            for id_b in ids[i + 1:]:
                a, b = groups[id_a], groups[id_b]
                # rev 6: two sightings of one class in ONE frame are two objects by
                # definition, whatever the geometry says about how close they are.
                if not a["frames"].isdisjoint(b["frames"]):
                    continue
                mean_depth = ((sum(a["depths"]) / len(a["depths"]))
                              + (sum(b["depths"]) / len(b["depths"]))) / 2.0
                if not MERGE_MIN_RANGE_CM <= mean_depth <= MERGE_MAX_RANGE_CM:
                    continue
                distance = _separation(a, b, mean_depth)
                if distance <= 1.0:
                    candidates.append((distance, id_a, id_b))
                    compatible.add(frozenset((id_a, id_b)))
        if not candidates:
            break
        candidates.sort(key=lambda c: c[0])
        best_distance, id_a, id_b = candidates[0]

        runner_up = next((c for c in candidates[1:]
                          if id_a in (c[1], c[2]) or id_b in (c[1], c[2])), None)
        ambiguous = False
        if runner_up and runner_up[0] <= best_distance * AMBIGUITY_FACTOR:
            # rev 5: a near-tie is only ambiguous when it is a real choice.
            # If the runner-up's third group is also compatible with the
            # other half of the best pair, all three are one object and the
            # tie is what agreement looks like. Accurate geometry makes such
            # ties the normal case (every distance near zero); rev 3-4 only
            # escaped it because their position error spread the points out.
            shared = id_a if id_a in runner_up[1:] else id_b
            other = id_b if shared == id_a else id_a
            third = runner_up[2] if runner_up[1] == shared else runner_up[1]
            ambiguous = frozenset((other, third)) not in compatible

        a, b = groups.pop(id_a), groups.pop(id_b)
        n_a, n_b = len(a["ids"]), len(b["ids"])
        total = n_a + n_b
        groups[id_a] = {
            "ids": a["ids"] + b["ids"],
            "x_cm": (a["x_cm"] * n_a + b["x_cm"] * n_b) / total,
            "y_cm": (a["y_cm"] * n_a + b["y_cm"] * n_b) / total,
            "points": a["points"] + b["points"],
            "frames": a["frames"] | b["frames"],
            "cams": a["cams"] + b["cams"],
            "commanded": a["commanded"] or b["commanded"],
            "depths": a["depths"] + b["depths"],
            "stops": a["stops"] | b["stops"],
            "bands": a["bands"] + b["bands"],
            "ambiguous": a["ambiguous"] or b["ambiguous"] or ambiguous,
        }

    objects = [_object_from_group(group, sighting_by_id) for group in groups.values()]
    objects.extend(_unmerged(sighting) for sighting in unpositioned)
    return objects


def merge_sightings(sightings: list[dict], delta_z_cm: float = 55.0,
                    camera: CameraModel = SYNTHETIC_LEVEL) -> list[dict]:
    """Group by class, route mobile classes straight to single-sighting
    objects (design spec 4.3: people are never position-merged, since they
    move between stops), and run ``associate`` per remaining class."""
    objects: list[dict] = []
    for class_name in sorted({s["class"] for s in sightings}):
        rows = [s for s in sightings if s["class"] == class_name]
        if class_name in MOBILE_CLASSES:
            objects.extend(_unmerged(s) for s in rows)
            continue
        objects.extend(associate(rows, delta_z_cm, camera))
    return objects


def majority_degraded(objects: list[dict]) -> bool:
    """True when more than half the merged objects show a depth
    disagreement severe enough to be marked degraded (design spec 5.4).
    Callers (the sweep-level report) fall back to SINGLE_VIEW_LOWER_BOUND
    for every class when this is true -- the geometry itself is no longer
    trusted, not just individual objects."""
    if not objects:
        return False
    degraded = sum(1 for obj in objects
                   if obj.get("depth_spread_pct") is not None
                   and obj["depth_spread_pct"] > DEGRADED_SPREAD_PCT)
    return degraded > len(objects) / 2.0
