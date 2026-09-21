"""Aggregate tracks into a description of the area."""

from __future__ import annotations

import math
from collections import Counter, defaultdict

SCENE_VERSION = "scene/2026-09-16.3"


def bearing_deg(cx_norm: float, sector: int, turn_deg: float, hfov: float) -> float:
    """Heading clockwise from takeoff, degrees.
    """
    return (sector * turn_deg + (cx_norm - 0.5) * hfov) % 360.0


def elevation_deg(cy_norm: float, vfov: float) -> float:
    """Angle above or below the optical axis. Negative is toward the ground."""
    return -(cy_norm - 0.5) * vfov


def describe(track, width: int, height: int, turn_deg: float,
             hfov: float, vfov: float, fps: float, yaw_verified: bool) -> dict:
    frame, box, score = track.best
    x1, y1, x2, y2 = box
    cx, cy = (x1 + x2) / 2 / width, (y1 + y2) / 2 / height
    area = ((x2 - x1) * (y2 - y1)) / (width * height)
    return {
        "track_id": track.track_id,
        "label": track.query,
        "sector": track.sector,
        "peak_score": round(track.peak_score, 3),
        "mean_score": round(track.mean_score, 3),
        "observations": track.n_obs,
        "persistence_s": round(track.n_obs / fps, 2),
        "best_frame": frame,
        "box_xyxy": [round(v, 1) for v in box],
        "image_position": {
            "cx_norm": round(cx, 3),
            "cy_norm": round(cy, 3),
            "area_frac": round(area, 5),
            "horizontal": "left" if cx < 0.37 else "right" if cx > 0.63 else "centre",
            "vertical": "upper" if cy < 0.37 else "lower" if cy > 0.63 else "middle",
            "apparent_size": ("large" if area > 0.10 else
                              "medium" if area > 0.02 else "small"),
        },
        "spatial": {
            "bearing_deg": round(bearing_deg(cx, track.sector, turn_deg, hfov), 1),
            "elevation_deg": round(elevation_deg(cy, vfov), 1),
            "confidence": "MODERATE" if yaw_verified else "LOW_YAW_UNVERIFIED",
            "range_m": None,
            "range_note": "metric range deferred; see roadmap",
            "provenance": "INFERRED",
        },
    }


def dedupe_loop_closure(objects: list[dict], n_sectors: int, turn_deg: float,
                        yaw_verified: bool, tol_deg: float = 20.0) -> dict:
    """Merge the final sector into the first when the scan closes a loop.
    """
    if n_sectors < 2:
        return {"applied": False, "reason": "fewer than two sectors"}
    if not yaw_verified:
        return {"applied": False,
                "reason": "loop closure unverified; sectors not assumed coincident"}
    if round((n_sectors - 1) * turn_deg) % 360 != 0:
        return {"applied": False, "reason": "scan does not close a circle"}

    last = n_sectors - 1
    firsts = [o for o in objects if o["sector"] == 0]
    merged = 0
    for o in [x for x in objects if x["sector"] == last]:
        ob = o["spatial"]["bearing_deg"] % 360
        best, best_d = None, tol_deg
        for f in firsts:
            if f["label"] != o["label"] or f.get("corroborated_by"):
                continue
            fb = f["spatial"]["bearing_deg"] % 360
            d = min(abs(ob - fb), 360 - abs(ob - fb))
            if d < best_d:
                best, best_d = f, d
        if best:
            o["duplicate_of"] = best["track_id"]
            o["duplicate_bearing_delta_deg"] = round(best_d, 1)
            best.setdefault("corroborated_by", []).append(o["track_id"])
            merged += 1

    return {"applied": True, "merged": merged, "merged_sector": last,
            "reason": f"sector {last} re-observes sector 0"}


def aggregate(objects: list[dict], n_sectors: int, threshold: float,
              turn_deg: float, merged_sector: int | None) -> dict:
    """Counts are over unique tracks, never detections.

    Three filters in sequence, and the order is load-bearing:

      1. duplicates  -- loop-closure re-sightings of an object already counted
                        in sector 0
      2. stuff       -- material regions (paving, scree, snow). These have no
                        object boundary, so counting them is meaningless: a
                        single "brick paving" box covered half a frame. They
                        are reported as terrain context instead.
      3. threshold   -- confirmed vs provisional

    Stuff is split from the ORIGINAL unique list, not from the already-filtered
    one, or it would always come back empty.
    """
    import numpy as np

    from .config import STUFF_QUERIES

    unique = [o for o in objects if o.get("duplicate_of") is None]
    stuff = [o for o in unique if o["label"].strip().lower() in STUFF_QUERIES]
    things = [o for o in unique if o["label"].strip().lower() not in STUFF_QUERIES]

    confirmed = [o for o in things if o["peak_score"] >= threshold]
    provisional = [o for o in things if o["peak_score"] < threshold]

    by_sector = defaultdict(list)
    for o in confirmed:
        by_sector[o["sector"]].append(o["label"])

    # Terrain context: which material appears where, with the best score seen.
    # Presence, not a count -- "2 brick pavings" is not a meaningful statement.
    terrain = defaultdict(dict)
    for o in stuff:
        prev = terrain[o["label"]].get("peak_score", 0.0)
        terrain[o["label"]] = {
            "sectors": sorted(set(terrain[o["label"]].get("sectors", []))
                              | {o["sector"]}),
            "peak_score": max(prev, o["peak_score"]),
        }

    # A sector with sub-threshold detections is NOT empty. On a mountain that
    # is the difference between a clear slope and a slope full of rocks that
    # all scored just under the line.
    confirmed_sectors = {o["sector"] for o in confirmed}
    below = sorted({o["sector"] for o in provisional} - confirmed_sectors)

    scores = [o["peak_score"] for o in objects] or [0.0]
    return {
        "counts": {
            "unique_confirmed": len(confirmed),
            "unique_provisional": len(provisional),
            "by_class_confirmed": dict(Counter(o["label"] for o in confirmed).most_common()),
            "by_class_provisional": dict(Counter(o["label"] for o in provisional).most_common()),
            "duplicates_excluded": len(objects) - len(unique),
            "stuff_excluded": len(stuff),
            "basis": ("unique object tracks after loop-closure dedup; "
                      "material regions excluded, see terrain"),
        },
        "terrain": dict(terrain),
        "by_sector": {
            f"sector_{s}": {
                "bearing_centre_deg": round((s * turn_deg) % 360, 1),
                "n_objects": len(by_sector.get(s, [])),
                "classes": dict(Counter(by_sector.get(s, [])).most_common()),
            } for s in range(n_sectors)
        },
        # A sector merged by loop closure is not a coverage gap: it was
        # observed twice. Reporting it as empty would read as a blind bearing.
        "empty_sectors": [s for s in range(n_sectors)
                          if not by_sector.get(s) and s != merged_sector
                          and s not in below],
        "sectors_below_threshold": below,
        "score_distribution": {
            "n": len(scores),
            "min": round(float(np.min(scores)), 3),
            "p25": round(float(np.percentile(scores, 25)), 3),
            "median": round(float(np.median(scores)), 3),
            "p75": round(float(np.percentile(scores, 75)), 3),
            "max": round(float(np.max(scores)), 3),
            "note": "raw Grounding DINO similarity; not calibrated probabilities",
        },
    }


def narrate(scene: dict) -> str:
    c = scene["counts"]
    if not c["unique_confirmed"]:
        text = (f"No objects confirmed at threshold. "
                f"{c['unique_provisional']} provisional detection(s) retained below it.")
    else:
        parts = [f"{n} {lab}{'s' if n > 1 else ''}"
                 for lab, n in c["by_class_confirmed"].items()]
        text = "The scan observed " + (
            ", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else parts[0]
        ) + "."
        headings = [f"{', '.join(f'{v} {k}' for k, v in d['classes'].items())} "
                    f"near {d['bearing_centre_deg']:.0f} degrees"
                    for d in scene["by_sector"].values() if d["n_objects"]]
        if headings:
            text += " By heading: " + "; ".join(headings) + "."

    if scene.get("terrain"):
        text += (" Surface: " + "; ".join(
            f"{lab} in sector(s) {d['sectors']}"
            for lab, d in scene["terrain"].items()) + ".")

    # "Detected but unconfirmed" and "nothing there" are different findings and
    # must not be collapsed into one sentence.
    if scene.get("sectors_below_threshold"):
        text += (f" Sector(s) {scene['sectors_below_threshold']} produced detections "
                 f"only below the confirmation threshold -- unconfirmed, not empty.")
    if scene["empty_sectors"]:
        text += f" No detections at all in sector(s) {scene['empty_sectors']}."
    return text