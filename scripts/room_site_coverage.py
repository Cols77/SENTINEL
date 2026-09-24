"""Generate CanningSt table cells and pose visibility from the Tello camera.

Each cell centre must project into a 960x720 TELLO_MEASURED frame from at
least one declared pose and sweep yaw, within 250 cm of the lens. Occlusion
is not modelled; the resulting matrix is a geometric upper bound on coverage.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

from sentinel.camera import CameraModel, TELLO_MEASURED
from sentinel.room.semantic import resolve_pose_id
from sentinel.room.site import parse_room_site
from sentinel.room.spatial import semantic_heading_to_compass_deg


SITE_PATH = Path(__file__).resolve().parents[1] / "fixtures/room_agent/v1/canningst-site.json"
SURFACE_BOUNDS_CM = (190, 310, 190, 270)
CELL_SIZE_CM = 40
SWEEP_OFFSETS_DEG = (-30, 0, 30)
DETECTION_RANGE_CM = 250


def projects_cell_center(
    cell_xy_cm: tuple[float, float], surface_z_cm: float,
    pose: tuple[float, float, float, float], camera: CameraModel,
    max_range_cm: float,
) -> bool:
    """Invert the same pitched pinhole rays used by ``ground_point``."""
    x_cm, y_cm, z_cm, heading_deg = pose
    dx, dy = cell_xy_cm[0] - x_cm, cell_xy_cm[1] - y_cm
    down = z_cm - surface_z_cm
    if down <= 0 or math.sqrt(dx * dx + dy * dy + down * down) > max_range_cm:
        return False
    # Semantic/checkpoint yaw is math-convention (90 degrees faces +y).
    # Grounding uses compass-convention (0 degrees faces +y).
    heading = math.radians(semantic_heading_to_compass_deg(heading_deg))
    forward = dx * math.sin(heading) + dy * math.cos(heading)
    lateral = dx * math.cos(heading) - dy * math.sin(heading)
    if forward <= 0:
        return False
    pitch = math.radians(camera.pitch_down_deg)
    depth = down * math.sin(pitch) + forward * math.cos(pitch)
    vertical = down * math.cos(pitch) - forward * math.sin(pitch)
    if depth <= 0:
        return False
    u = camera.cx + camera.fx * lateral / depth
    v = camera.cy + camera.fy * vertical / depth
    return 0 <= u <= camera.width and 0 <= v <= camera.height


def generate_coverage(
    document: dict, *, surface_bounds_cm: tuple[int, int, int, int] = SURFACE_BOUNDS_CM,
    max_range_cm: float = DETECTION_RANGE_CM,
) -> tuple[dict, tuple[str, ...]]:
    """Return a new site document and the cell IDs with no declared view."""
    # Existing visibility may refer to a previous grid; parse the geometry
    # without it, then produce both dependent fields together.
    geometry_document = {**document, "pose_visibility": [], "pose_tag_visibility": []}
    site = parse_room_site(geometry_document)
    plane = next(plane for plane in site.support_planes if plane.plane_id == "demo-surface")
    pose_ids = sorted({pose_id for pose_id, _ in document["pose_visibility"]})
    if not pose_ids:
        raise ValueError("site must declare observation pose IDs")
    poses = {}
    for pose_id in pose_ids:
        pose = resolve_pose_id(site, pose_id)
        if pose is None:
            raise ValueError(f"unresolvable observation pose: {pose_id}")
        poses[pose_id] = pose

    x_min, x_max, y_min, y_max = surface_bounds_cm
    if ((x_max - x_min) % CELL_SIZE_CM or (y_max - y_min) % CELL_SIZE_CM
            or x_min >= x_max or y_min >= y_max):
        raise ValueError("surface bounds must be positive multiples of the cell size")
    cells = []
    visible: dict[str, list[str]] = {pose_id: [] for pose_id in pose_ids}
    dropped = []
    for y in range(y_min, y_max, CELL_SIZE_CM):
        for x in range(x_min, x_max, CELL_SIZE_CM):
            cell_id = f"demo-x{x}-y{y}"
            center = (x + CELL_SIZE_CM / 2, y + CELL_SIZE_CM / 2)
            viewers = []
            for pose_id, pose in poses.items():
                if any(projects_cell_center(center, plane.z_cm,
                       (pose.x_cm, pose.y_cm, pose.z_cm, pose.yaw_deg + offset),
                       TELLO_MEASURED, max_range_cm)
                       for offset in SWEEP_OFFSETS_DEG):
                    viewers.append(pose_id)
            if not viewers:
                dropped.append(cell_id)
                continue
            cells.append({
                "cell_id": cell_id,
                "polygon_xy_cm": [[x, y], [x + CELL_SIZE_CM, y],
                                  [x + CELL_SIZE_CM, y + CELL_SIZE_CM], [x, y + CELL_SIZE_CM]],
                "z_band_cm": [plane.z_cm, plane.z_cm + 30],
                "required_classes": ["computer", "phone"],
            })
            for pose_id in viewers:
                visible[pose_id].append(cell_id)
    empty = [pose_id for pose_id, cell_ids in visible.items() if not cell_ids]
    if empty:
        raise ValueError(f"observation poses have no camera-visible cells: {empty}")
    generated = {**document, "coverage_cells": cells,
                 "pose_visibility": [[pose_id, visible[pose_id]] for pose_id in pose_ids]}
    parse_room_site(generated)
    return generated, tuple(dropped)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, default=SITE_PATH)
    parser.add_argument("--check", action="store_true", help="check committed output without writing")
    args = parser.parse_args()
    document = json.loads(args.site.read_text(encoding="utf-8"))
    generated, dropped = generate_coverage(document)
    for cell_id in dropped:
        print(f"dropped uncovered cell: {cell_id}", file=sys.stderr)
    if args.check:
        if generated != document:
            print("coverage output differs from committed site", file=sys.stderr)
            return 1
    else:
        args.site.write_text(json.dumps(generated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(generated['coverage_cells'])} cells, "
          f"{len(generated['pose_visibility'])} poses, {len(dropped)} dropped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
