"""AprilTag 36h11 detector + single-tag PnP pose estimation (fail-closed)."""
import cv2
import numpy as np

from sentinel.contracts.pose import DronePoseReport, TagObservation
from sentinel.pose.tagmap import TagMap

ROOM_SCALE_M_PER_UNIT = 0.1  # metres per room unit


def _report_point(rvec, tvec, tag, scale: float):
    """Compose a PnP solution into the drone's room position and heading.

    ``rvec``/``tvec`` (from solvePnP) map tag-frame points (metres, tag plane
    z=0, z into the wall) into the camera frame: p_cam = R @ p_tag + t, with
    tvec the tag centre in camera coordinates. The tag's map entry gives its
    room position and yaw; with the room z axis up, the tag frame in the room
    is exactly the yaw rotation (x_tag = right when facing the tag, y_tag = up,
    z_tag = into the wall). Composing R_room_tag @ R_cam_tag.T yields the
    camera orientation in the room, and cam = tag_pos - R_room_cam @ tvec
    yields the camera (== drone) position. Heading is the horizontal direction
    the camera faces (its +z axis in the room).
    """
    R_cam_tag, _ = cv2.Rodrigues(np.asarray(rvec, dtype=float))
    tvec = np.asarray(tvec, dtype=float).flatten()
    yaw = np.deg2rad(tag.yaw_deg)
    # OpenCV tag frame (from _tag_object_points + image corner order): x to the
    # right as the camera sees it, y downward in the tag plane, z out of the
    # wall toward the viewer. With room z up and the tag yawed by yaw_deg
    # (outward normal = (cos yaw, sin yaw, 0)): z_tag = normal, y_tag = down,
    # x_tag = y x z = right when facing the tag.
    R_room_tag = np.array([
        [np.sin(yaw), 0.0, np.cos(yaw)],
        [-np.cos(yaw), 0.0, np.sin(yaw)],
        [0.0, -1.0, 0.0],
    ])
    R_room_cam = R_room_tag @ R_cam_tag.T
    tag_pos = np.array([tag.x_units, tag.y_units, 0.0]) * scale + np.array([0.0, 0.0, tag.z_units])
    cam_pos = tag_pos - R_room_cam @ tvec
    fwd = R_room_cam[:, 2]  # camera +z axis in the room = viewing direction
    heading = float(np.degrees(np.arctan2(fwd[1], fwd[0])) % 360.0)
    return (round(float(cam_pos[0] / scale), 3), round(float(cam_pos[1] / scale), 3)), heading


def estimate_pose(frame, tag_map: TagMap, *, run_id: str, report_id: str) -> DronePoseReport:
    """Detect AprilTag 36h11 tags, PnP-solve mapped ones, emit the best report.

    Fail-closed: raises ValueError when no tags are detected or only unknown
    tags are detected.
    """
    from datetime import datetime

    aruco = cv2.aruco
    detector = aruco.ArucoDetector(
        aruco.getPredefinedDictionary(aruco.DICT_APRILTAG_36h11),
        aruco.DetectorParameters(),
    )
    corners, ids, _ = detector.detectMarkers(frame)
    if ids is None or len(ids) == 0:
        raise ValueError("no AprilTag markers detected: pose fails closed")

    mapped = [i for i, mid in enumerate(ids.flatten()) if tag_map.has_tag(int(mid))]
    if not mapped:
        unknown = sorted(set(int(i) for i in ids.flatten()))
        raise ValueError(f"only unmapped tag(s) detected {unknown}: pose fails closed")

    best = None
    for i in mapped:
        tag_id = int(ids.flatten()[i])
        tag = tag_map.tag(tag_id)
        obj = _tag_object_points(tag.size_m)
        ok, rvs, tvs, _ = cv2.solvePnPGeneric(
            obj, corners[i].astype(np.float64), tag_map.K, tag_map.dist,
            flags=cv2.SOLVEPNP_IPPE_SQUARE)
        if not ok:
            raise ValueError(f"PnP failed for tag {tag_id}: pose fails closed")
        # IPPE_SQUARE has a two-fold planar ambiguity; on frontal tags OpenCV 5.0
        # can return a front/back solution whose reprojection does not match the
        # detected corners. Pick the best candidate by actual reprojection error,
        # falling back to ITERATIVE when every candidate exceeds the gate.
        best_tag = None
        for rvec_c, tvec_c in zip(rvs, tvs):
            proj, _ = cv2.projectPoints(obj, rvec_c, tvec_c, tag_map.K, tag_map.dist)
            err_c = float(np.mean(np.linalg.norm(
                proj.reshape(-1, 2) - corners[i].reshape(-1, 2), axis=1)))
            if best_tag is None or err_c < best_tag[0]:
                best_tag = (err_c, rvec_c, tvec_c)
        if best_tag[0] > 2.0:
            ok, rvec_i, tvec_i = cv2.solvePnP(
                obj, corners[i].astype(np.float64), tag_map.K, tag_map.dist)
            if not ok:
                raise ValueError(f"PnP failed for tag {tag_id}: pose fails closed")
            proj, _ = cv2.projectPoints(obj, rvec_i, tvec_i, tag_map.K, tag_map.dist)
            err_i = float(np.mean(np.linalg.norm(
                proj.reshape(-1, 2) - corners[i].reshape(-1, 2), axis=1)))
            if err_i < best_tag[0]:
                best_tag = (err_i, rvec_i, tvec_i)
        err, rvec, tvec = best_tag
        if err > 2.0:
            raise ValueError(f"reprojection error {err:.2f}px exceeds 2.0px: pose fails closed")
        if best is None or err < best[0]:
            best = (err, tag, rvec, tvec)

    err, tag, rvec, tvec = best
    point, heading = _report_point(rvec, tvec, tag, tag_map.room_scale_m_per_unit)
    return DronePoseReport(
        report_id=report_id,
        run_id=run_id,
        observed_at=datetime.now(),
        source_kind="SIMULATED",
        point=point,
        heading_deg=round(heading, 2),
        altitude_m=None,
        method="APRILTAG_36H11_PNP",
        tag_observations=(TagObservation(
            tag_id=tag.tag_id,
            zone_id=tag.zone_id,
            distance_m=round(float(np.linalg.norm(tvec)), 4),
            reprojection_error_px=round(err, 4),
        ),),
        reprojection_error_px=round(err, 4),
    )


def _tag_object_points(size_m: float) -> np.ndarray:
    """Corners of the tag square in the tag frame (IPPE_SQUARE order: TL,TR,BR,BL)."""
    s = size_m / 2.0
    return np.array([[-s, s, 0], [s, s, 0], [s, -s, 0], [-s, -s, 0]], dtype=np.float64)
