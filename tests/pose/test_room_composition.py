"""KB5-C4: tag-to-room composition validated against synthetic ground truth.

A tag is placed at a known room pose (from the tag map) and a synthetic
camera is placed at a known offset along the tag's outward normal at tag
height. The tag is rendered into a frame via cv2.projectPoints +
warpPerspective exactly as the detector's PnP model expects, then
estimate_pose must recover the camera's (== drone's) room position and
heading. ALL ground truth is computed inside this test from the tag map
geometry; nothing is hardcoded.
"""
import cv2
import numpy as np
import pytest

from sentinel.pose.detector import estimate_pose
from sentinel.pose.tagmap import TagMap

K = np.array([[547.0, 0, 480.0], [0, 547.0, 360.0], [0, 0, 1]])
DIST = np.zeros((0,), dtype=np.float64)
SIZE_M = 0.16
ROOM_SCALE_M_PER_UNIT = 0.1

TAG_X_UNITS, TAG_Y_UNITS, TAG_Z_UNITS = 30.0, 40.0, 1.2

TAG_MAP_DOC = {
    "schema_version": "tag-map-v1", "tag_family": "36h11",
    "room_scale_m_per_unit": ROOM_SCALE_M_PER_UNIT,
    "room": {"width_units": 100, "height_units": 100, "origin": "LEFT_ALLEY_FRONT_CORNER"},
    "tags": [{"tag_id": 0, "zone_id": "LEFT_ALLEY_FRONT",
              "x_units": TAG_X_UNITS, "y_units": TAG_Y_UNITS, "z_units": TAG_Z_UNITS,
              "yaw_deg": 0.0, "size_m": SIZE_M, "mounting": "WALL"}],
    "camera_calibration": {"width": 960, "height": 720, "fx": 547.0, "fy": 547.0,
                           "cx": 480.0, "cy": 360.0, "dist_coeffs": [], "calibration_source": "nominal"},
}

# Tag object frame: tag plane z=0, tag centre at origin, x right / y up when
# facing the tag, z pointing into the wall (IPPE_SQUARE order: TL,TR,BR,BL).
OBJ_CORNERS = np.array([
    [-SIZE_M / 2, SIZE_M / 2, 0], [SIZE_M / 2, SIZE_M / 2, 0],
    [SIZE_M / 2, -SIZE_M / 2, 0], [-SIZE_M / 2, -SIZE_M / 2, 0]],
    dtype=np.float64)


def _room_geometry(yaw_deg: float):
    """Room-frame axes for a wall tag with outward floor normal n=(cos,sin)."""
    c, s = np.cos(np.deg2rad(yaw_deg)), np.sin(np.deg2rad(yaw_deg))
    n = np.array([c, s, 0.0])            # outward normal (tag -> room)
    x_tag = np.array([s, -c, 0.0])       # tag frame: right when facing the tag
    y_tag = np.array([0.0, 0.0, 1.0])    # tag frame: up
    z_tag = -n                           # tag frame: into the wall
    assert np.allclose(np.cross(x_tag, y_tag), z_tag)
    return n, np.column_stack([x_tag, y_tag, z_tag])  # normal, R_room_tag


def _render(yaw_deg: float, distance_m: float):
    """Render tag 0 as seen by a level camera at `distance_m` along the normal.

    Returns (frame, truth_point_xy_units, truth_heading_deg).
    """
    doc = dict(TAG_MAP_DOC)
    doc["tags"] = [dict(TAG_MAP_DOC["tags"][0], yaw_deg=yaw_deg)]
    S = ROOM_SCALE_M_PER_UNIT
    tag_pos = np.array([TAG_X_UNITS * S, TAG_Y_UNITS * S, TAG_Z_UNITS])
    n, R_room_tag = _room_geometry(yaw_deg)

    cam_pos = tag_pos + n * distance_m          # camera == drone, same height
    truth_point = (cam_pos[0] / S, cam_pos[1] / S)

    # Camera faces the tag head-on, level, z up in the room:
    # forward = -n, right = forward x up, y_cam = down (OpenCV convention).
    fwd = -n
    x_cam = np.cross(fwd, np.array([0.0, 0.0, 1.0]))
    y_cam = np.array([0.0, 0.0, -1.0])
    z_cam = fwd
    R_room_cam = np.column_stack([x_cam, y_cam, z_cam])

    tvec = R_room_cam.T @ (tag_pos - cam_pos)   # camera -> tag, metres
    rvec, _ = cv2.Rodrigues(R_room_cam.T @ R_room_tag)
    pts, _ = cv2.projectPoints(OBJ_CORNERS, rvec, tvec.reshape(3, 1), K, DIST)
    pts = pts.reshape(-1, 2).astype(np.float32)

    aruco = cv2.aruco
    marker = aruco.generateImageMarker(
        aruco.getPredefinedDictionary(aruco.DICT_APRILTAG_36h11), 0, 200)
    frame = np.full((720, 960, 3), 255, dtype=np.uint8)
    h, w = marker.shape[:2]
    # marker image corners in BL,BR,TR,TL order to match projected OBJ_CORNERS
    # order (y-up object frame vs y-down image frame)
    src = np.array([[0, h], [w, h], [w, 0], [0, 0]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(src, pts)
    warped = cv2.warpPerspective(marker, M, (960, 720),
                                 flags=cv2.INTER_NEAREST,
                                 borderMode=cv2.BORDER_CONSTANT,
                                 borderValue=(255, 255, 255))
    frame = np.where(warped[..., None] == 255, frame, warped[..., None])

    # Drone heading: it faces the tag, i.e. along -(outward normal).
    truth_heading = (yaw_deg + 180.0) % 360.0
    return frame, doc, truth_point, truth_heading


@pytest.mark.parametrize("yaw_deg", [0.0, 45.0, 90.0, 180.0, 270.0])
@pytest.mark.parametrize("distance_m", [0.6, 1.5])
def test_room_composition_matches_ground_truth(yaw_deg, distance_m):
    frame, doc, truth_point, truth_heading = _render(yaw_deg, distance_m)
    report = estimate_pose(frame, TagMap(doc), run_id="r1", report_id="p1")

    assert report.method == "APRILTAG_36H11_PNP"
    assert [o.tag_id for o in report.tag_observations] == [0]
    assert report.tag_observations[0].distance_m == pytest.approx(distance_m, abs=0.02)
    assert abs(report.point[0] - truth_point[0]) <= 1.0
    assert abs(report.point[1] - truth_point[1]) <= 1.0
    heading_err = abs((report.heading_deg - truth_heading + 180.0) % 360.0 - 180.0)
    assert heading_err <= 5.0
    assert 0.0 <= report.point[0] <= 100.0 and 0.0 <= report.point[1] <= 100.0
