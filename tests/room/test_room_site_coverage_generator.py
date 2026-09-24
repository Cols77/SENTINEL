"""The committed CanningSt visibility matrix comes from the measured camera."""
from __future__ import annotations

import json
from pathlib import Path

from sentinel.camera import TELLO_MEASURED
from sentinel.room.semantic import resolve_pose_id
from sentinel.room.site import parse_room_site

from scripts.room_site_coverage import generate_coverage, projects_cell_center


SITE_PATH = Path(__file__).resolve().parents[2] / "fixtures/room_agent/v1/canningst-site.json"


def test_projection_uses_room_heading_and_rejects_out_of_frame_or_range():
    # Heading 90 is +y in the room geometry. A close point on the support
    # surface projects below the image; one 250 cm away exceeds detection range.
    pose = (250, 153, 70, 90)
    assert projects_cell_center((250, 230), 50, pose, TELLO_MEASURED, 250)
    assert not projects_cell_center((250, 170), 50, pose, TELLO_MEASURED, 250)
    assert not projects_cell_center((250, 404), 50, pose, TELLO_MEASURED, 250)
    assert not projects_cell_center((100, 230), 50, pose, TELLO_MEASURED, 250)


def test_generator_is_deterministic_and_every_committed_cell_has_a_view():
    document = json.loads(SITE_PATH.read_text(encoding="utf-8"))
    generated, dropped = generate_coverage(document)
    assert generate_coverage(document) == (generated, dropped)
    assert generated["coverage_cells"] == document["coverage_cells"]
    assert generated["pose_visibility"] == document["pose_visibility"]
    assert not dropped
    site = parse_room_site(generated)
    assert len(site.coverage_cells) == 6  # 120 × 80 cm table in 40 cm squares.
    assert all(cell.z_band_cm == (50, 80) and
               max(x for x, _ in cell.polygon_xy_cm) - min(x for x, _ in cell.polygon_xy_cm) == 40 and
               max(y for _, y in cell.polygon_xy_cm) - min(y for _, y in cell.polygon_xy_cm) == 40
               for cell in site.coverage_cells)
    views = {cell_id for _, cell_ids in site.pose_visibility for cell_id in cell_ids}
    assert views == {cell.cell_id for cell in site.coverage_cells}
    flight = next(v for v in site.free_space_volumes if v.volume_id == "flight")
    x_min = min(x for x, _ in flight.polygon_xy_cm)
    x_max = max(x for x, _ in flight.polygon_xy_cm)
    y_min = min(y for _, y in flight.polygon_xy_cm)
    y_max = max(y for _, y in flight.polygon_xy_cm)
    for pose_id, _ in site.pose_visibility:
        pose = resolve_pose_id(site, pose_id)
        assert pose is not None
        assert x_min <= pose.x_cm <= x_max
        assert y_min <= pose.y_cm <= y_max
        assert flight.z_interval_cm[0] <= pose.z_cm <= flight.z_interval_cm[1]


def test_generator_logs_cells_with_no_camera_view():
    document = json.loads(SITE_PATH.read_text(encoding="utf-8"))
    generated, dropped = generate_coverage(document, surface_bounds_cm=(190, 390, 190, 270),
                                           max_range_cm=80)
    assert dropped
    assert set(dropped).isdisjoint(cell["cell_id"] for cell in generated["coverage_cells"])
