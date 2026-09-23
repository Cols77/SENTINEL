"""Synthetic multi-tag frames for detector tests (level camera, no distortion)."""
import math

import cv2
import numpy as np

K = np.array([[547.0, 0, 480.0], [0, 547.0, 360.0], [0, 0, 1]])
DIST = np.zeros((0,), dtype=np.float64)
SIZE_M = 0.16
SCALE = 0.1

_OBJ = np.array([[-SIZE_M / 2, SIZE_M / 2, 0], [SIZE_M / 2, SIZE_M / 2, 0],
                 [SIZE_M / 2, -SIZE_M / 2, 0], [-SIZE_M / 2, -SIZE_M / 2, 0]], dtype=np.float64)

# A splayed pair around (0.5 m, 4.0 m): normals at +25 and -25 degrees. Verified by the spike.
SPLAYED_PAIR = [
    {"tag_id": 0, "zone_id": "PAIR", "x_units": 4.4083, "y_units": 41.2688, "z_units": 1.0,
     "yaw_deg": 25.0, "size_m": SIZE_M, "mounting": "WALL"},
    {"tag_id": 1, "zone_id": "PAIR", "x_units": 4.4083, "y_units": 38.7312, "z_units": 1.0,
     "yaw_deg": -25.0, "size_m": SIZE_M, "mounting": "WALL"},
]


def make_doc(tags: list[dict]) -> dict:
    return {"schema_version": "tag-map-v1", "tag_family": "36h11",
            "room_scale_m_per_unit": SCALE,
            "room": {"width_units": 100, "height_units": 100, "origin": "LEFT_ALLEY_FRONT_CORNER"},
            "tags": tags,
            "camera_calibration": {"width": 960, "height": 720, "fx": 547.0, "fy": 547.0,
                                   "cx": 480.0, "cy": 360.0, "dist_coeffs": [],
                                   "calibration_source": "nominal"}}


def render(tags: list[dict], cam_pos_m, heading_deg: float) -> np.ndarray:
    """Render every tag as seen by a level camera at ``cam_pos_m`` facing ``heading_deg``."""
    h = math.radians(heading_deg)
    fwd = np.array([math.cos(h), math.sin(h), 0.0])
    x_cam = np.cross(fwd, [0.0, 0.0, 1.0])
    R_room_cam = np.column_stack([x_cam, [0.0, 0.0, -1.0], fwd])
    frame = np.full((720, 960, 3), 255, dtype=np.uint8)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    for tag in tags:
        c, s = math.cos(math.radians(tag["yaw_deg"])), math.sin(math.radians(tag["yaw_deg"]))
        # rendering tag frame: x right when facing the tag, y up, z into the wall
        R_room_tag = np.column_stack([[s, -c, 0.0], [0.0, 0.0, 1.0], [-c, -s, 0.0]])
        tag_pos = np.array([tag["x_units"] * SCALE, tag["y_units"] * SCALE, tag["z_units"]])
        tvec = R_room_cam.T @ (tag_pos - np.asarray(cam_pos_m, dtype=float))
        rvec, _ = cv2.Rodrigues(R_room_cam.T @ R_room_tag)
        pts, _ = cv2.projectPoints(_OBJ, rvec, tvec.reshape(3, 1), K, DIST)
        pts = pts.reshape(-1, 2).astype(np.float32)
        marker = cv2.aruco.generateImageMarker(dictionary, tag["tag_id"], 200)
        mh, mw = marker.shape[:2]
        src = np.array([[0, mh], [mw, mh], [mw, 0], [0, 0]], dtype=np.float32)
        M = cv2.getPerspectiveTransform(src, pts)
        warped = cv2.warpPerspective(marker, M, (960, 720), flags=cv2.INTER_NEAREST,
                                     borderMode=cv2.BORDER_CONSTANT, borderValue=255)
        mask = cv2.warpPerspective(np.full_like(marker, 255), M, (960, 720),
                                   flags=cv2.INTER_NEAREST)
        frame[mask == 255] = warped[mask == 255][:, None]
    return frame
