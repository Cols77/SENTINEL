"""AprilTag 36h11 detector + single-tag PnP pose estimation (fail-closed)."""
import cv2
import numpy as np

from sentinel.contracts.pose import DronePoseReport, TagObservation
from sentinel.pose.tagmap import TagMap

ROOM_SCALE_M_PER_UNIT = 0.1  # metres per room unit; refined in KB5-C4


def _report_point(tvec_room, tag):
    """Compose a camera-frame metres tvec into the drone's floor position.

    Camera forward is assumed to face the tag head-on: forward = -(tag outward
    normal), camera right = (-ny, nx) on the floor plane. The tag position in
    the camera frame is tvec, so the drone sits at
        drone = tag - (tvec_z * forward + tvec_x * right)
    This metres-to-room-units composition is the KB5-C4 open point; it is
    isolated in this one helper so C4 can refine it precisely.
    """
    tvec = np.asarray(tvec_room, dtype=float)
    yaw = np.deg2rad(tag.yaw_deg)
    nx, ny = np.cos(yaw), np.sin(yaw)
    fwd = np.array([-nx, -ny])
    right = np.array([-ny, nx])
    dx_m = -(tvec[2] * fwd[0] + tvec[0] * right[0])
    dy_m = -(tvec[2] * fwd[1] + tvec[0] * right[1])
    x_units = tag.x_units + dx_m / ROOM_SCALE_M_PER_UNIT
    y_units = tag.y_units + dy_m / ROOM_SCALE_M_PER_UNIT
    return (round(float(x_units), 3), round(float(y_units), 3))


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
    tvec_room = tvec.flatten().copy()  # camera frame, metres (camera->tag)
    return DronePoseReport(
        report_id=report_id,
        run_id=run_id,
        observed_at=datetime.now(),
        source_kind="SIMULATED",
        point=_report_point(tvec_room, tag),
        heading_deg=None,
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
