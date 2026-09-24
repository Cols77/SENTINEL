"""Generate the CanningSt *wide* scenario from the compact CanningSt site.

Same room, tag map and measured drift rates. The observation envelope is
spread over most of the flight volume as a 3x3 grid of tag-facing viewpoints,
home is declared where the operator places the drone (about 180 cm from the
tag wall), the demo surface is gridded over the room width, and the drift
profile allows the aircraft further from a tag fix. See
``fixtures/room_agent/v1/canningst-wide-site.PROVENANCE.md``.

    python scripts/room_site_wide.py          # write both files
    python scripts/room_site_wide.py --check  # verify the committed files
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

from sentinel.camera import TELLO_MEASURED
from sentinel.room.semantic import resolve_pose_id
from sentinel.room.site import parse_room_site
from scripts.room_site_coverage import generate_coverage

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "room_agent" / "v1"
BASE_SITE = FIXTURES / "canningst-site.json"
WIDE_SITE = FIXTURES / "canningst-wide-site.json"
BASE_DRIFT = FIXTURES / "profiles" / "canningst-drift.json"
WIDE_DRIFT = FIXTURES / "profiles" / "canningst-wide-drift.json"

OBSERVATION_XY_CM = [[140, 170], [360, 170], [360, 330], [140, 330]]
HOME_XY_CM = (250, 220)
SURFACE_BOUNDS_CM = (130, 370, 240, 360)
DETECTION_RANGE_CM = 120  # a phone is ~100 px wide at this range in the 960x720 stream
MAX_XY_UNCERTAINTY_CM = 90
MAX_HEADING_UNCERTAINTY_DEG = 30

TAGS_XY_CM = {2: (215.6, 400.0), 0: (284.4, 400.0)}
TAG_HALF_WIDTH_CM = 8.0
MAX_TAG_RANGE_CM = 260.0
EDGE_MARGIN_DEG = 3.0


def tags_in_view(pose) -> list[int]:
    """Mapped wall tags wholly inside the frame at the pose's own heading."""
    half_fov = math.degrees(math.atan((TELLO_MEASURED.width / 2) / TELLO_MEASURED.fx)) - EDGE_MARGIN_DEG
    yaw = math.radians(pose.yaw_deg)
    seen = []
    for tag_id, (tx, ty) in sorted(TAGS_XY_CM.items()):
        dx, dy = tx - pose.x_cm, ty - pose.y_cm
        forward = dx * math.cos(yaw) + dy * math.sin(yaw)
        lateral = dx * math.sin(yaw) - dy * math.cos(yaw)
        if forward > 0 and math.hypot(dx, dy) <= MAX_TAG_RANGE_CM and \
                math.degrees(math.atan((abs(lateral) + TAG_HALF_WIDTH_CM) / forward)) <= half_fov:
            seen.append(tag_id)
    return seen


def build_site() -> dict:
    doc = json.loads(BASE_SITE.read_text(encoding="utf-8"))
    doc["site_id"] = "canningst-wide"
    doc["clearance_provenance"] = ("CanningSt wide scenario 2026-09-24: canningst-site.json free space, with the "
                                   "observation envelope widened to most of the flight volume; see "
                                   "canningst-wide-site.PROVENANCE.md")
    for volume in doc["free_space_volumes"]:
        if volume["volume_id"] == "observation":
            volume["polygon_xy_cm"] = OBSERVATION_XY_CM
            volume["z_interval_cm"] = [0, 140]
    x, y = HOME_XY_CM
    doc["home"]["nominal_pose"] = {"x_cm": x, "y_cm": y, "z_cm": 0}
    for route in doc["routes"]:
        if route["route_id"] == doc["home"]["route_id"]:
            route["checkpoints"] = [{"x_cm": x, "y_cm": y, "z_cm": 80}]
    poses = sorted(f"{h}-{d}-middle-toward_targets" for h in ("left", "center", "right")
                   for d in ("rear", "middle", "front"))
    doc["pose_visibility"] = [[pose_id, []] for pose_id in poses]
    doc["coverage_cells"] = []
    site = parse_room_site({**doc, "pose_visibility": [], "pose_tag_visibility": []})
    # Declare tags only where both are in view head-on: the minimum for an
    # expected re-anchor credit. Side columns see one tag, or none.
    doc["pose_tag_visibility"] = [[pose_id, seen] for pose_id in poses
                                  if len(seen := tags_in_view(resolve_pose_id(site, pose_id))) == 2]
    generated, dropped = generate_coverage(doc, surface_bounds_cm=SURFACE_BOUNDS_CM,
                                           max_range_cm=DETECTION_RANGE_CM)
    if dropped:
        raise ValueError(f"surface cells with no view: {dropped}")
    return generated


def build_drift() -> dict:
    profile = json.loads(BASE_DRIFT.read_text(encoding="utf-8"))
    profile.update(
        profile_id="canningst-wide-20260924-drift",
        max_xy_uncertainty_cm=MAX_XY_UNCERTAINTY_CM,
        max_heading_uncertainty_deg=MAX_HEADING_UNCERTAINTY_DEG,
        evidence_refs=[*profile["evidence_refs"], "operator-declared:wide-scenario-uncertainty-limits-20260924"],
    )
    return profile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="verify the committed files without writing")
    args = parser.parse_args()
    outputs = {WIDE_SITE: json.dumps(build_site(), indent=2, ensure_ascii=False) + "\n",
               WIDE_DRIFT: json.dumps(build_drift(), indent=2, sort_keys=True) + "\n"}
    stale = [path for path, text in outputs.items()
             if not path.exists() or path.read_text(encoding="utf-8") != text]
    if args.check:
        for path in stale:
            print(f"out of date: {path.relative_to(ROOT).as_posix()}", file=sys.stderr)
        return 1 if stale else 0
    for path, text in outputs.items():
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
