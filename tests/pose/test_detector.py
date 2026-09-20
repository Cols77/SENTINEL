"""Synthetic-frame tests for the AprilTag 36h11 detector + single-tag PnP."""
import cv2
import numpy as np
import pytest

from sentinel.pose.detector import estimate_pose
from sentinel.pose.tagmap import TagMap

K = np.array([[547.0, 0, 480.0], [0, 547.0, 360.0], [0, 0, 1]])
DIST = np.zeros((0,), dtype=np.float64)
SIZE_M = 0.16
DISTANCE_M = 0.5

TAG_MAP_DOC = {
    "schema_version": "tag-map-v1", "tag_family": "36h11",
    "room": {"width_units": 100, "height_units": 100, "origin": "LEFT_ALLEY_FRONT_CORNER"},
    "tags": [{"tag_id": 0, "zone_id": "LEFT_ALLEY_FRONT", "x_units": 5.0, "y_units": 10.0,
              "z_units": 1.5, "yaw_deg": 0.0, "size_m": SIZE_M, "mounting": "WALL"}],
    "camera_calibration": {"width": 960, "height": 720, "fx": 547.0, "fy": 547.0,
                           "cx": 480.0, "cy": 360.0, "dist_coeffs": [], "calibration_source": "nominal"},
}

# Tag object frame: tag plane z=0, tag centre at origin, z pointing into the
# wall. yaw_deg=0 => tag normal (= -z_tag) points along room +x.
OBJ_CORNERS = np.array([
    [-SIZE_M / 2, SIZE_M / 2, 0], [SIZE_M / 2, SIZE_M / 2, 0],
    [SIZE_M / 2, -SIZE_M / 2, 0], [-SIZE_M / 2, -SIZE_M / 2, 0]],
    dtype=np.float64)


def render_frontal_tag(tag_id: int) -> np.ndarray:
    """Frontal tag at DISTANCE_M, rendered with the same K the detector uses."""
    aruco = cv2.aruco
    dictionary = aruco.getPredefinedDictionary(aruco.DICT_APRILTAG_36h11)
    marker = aruco.generateImageMarker(dictionary, tag_id, 200)
    rvec = np.zeros((3, 1), dtype=np.float64)
    tvec = np.array([[0.0], [0.0], [DISTANCE_M]])
    pts, _ = cv2.projectPoints(OBJ_CORNERS, rvec, tvec, K, DIST)
    pts = pts.reshape(-1, 2).astype(np.float32)
    frame = np.full((720, 960, 3), 255, dtype=np.uint8)
    h, w = marker.shape[:2]
    # marker image corners in BL,BR,TR,TL order to match projected OBJ_CORNERS
    # order (y-up object frame vs y-down image frame)
    src = np.array([[0, h], [w, h], [w, 0], [0, 0]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(src, pts)
    warped = cv2.warpPerspective(marker, M, (960, 720),
                                 flags=cv2.INTER_NEAREST,
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
    frame = np.where(warped[..., None] == 255, frame, warped[..., None])
    return frame


def test_frontal_tag_detection_invariants():
    report = estimate_pose(render_frontal_tag(0), TagMap(TAG_MAP_DOC),
                           run_id="r1", report_id="p1")
    assert report.method == "APRILTAG_36H11_PNP"
    assert [o.tag_id for o in report.tag_observations] == [0]
    assert report.tag_observations[0].zone_id == "LEFT_ALLEY_FRONT"
    assert report.tag_observations[0].distance_m == pytest.approx(DISTANCE_M, abs=0.02)
    assert report.reprojection_error_px < 2.0
    # drone 0.5 m in front of the tag along room +x; 0.1 m per room unit
    assert report.point[0] == pytest.approx(10.0, abs=1.0)
    assert report.point[1] == pytest.approx(10.0, abs=1.0)
    assert 0.0 <= report.point[0] <= 100.0 and 0.0 <= report.point[1] <= 100.0


def test_no_tags_raises():
    frame = np.full((720, 960, 3), 255, dtype=np.uint8)
    with pytest.raises(ValueError):
        estimate_pose(frame, TagMap(TAG_MAP_DOC), run_id="r1", report_id="p2")


def test_unknown_tag_only_raises():
    with pytest.raises(ValueError):
        estimate_pose(render_frontal_tag(5), TagMap(TAG_MAP_DOC),
                      run_id="r1", report_id="p3")


def test_observed_at_is_timezone_aware_utc():
    report = estimate_pose(render_frontal_tag(0), TagMap(TAG_MAP_DOC),
                           run_id="r1", report_id="p1")
    assert report.observed_at.tzinfo is not None
    assert report.observed_at.utcoffset().total_seconds() == 0
    assert -180.0 <= report.heading_deg <= 180.0


def test_source_kind_keyword_defaults_to_simulated():
    tm = TagMap(TAG_MAP_DOC)
    default = estimate_pose(render_frontal_tag(0), tm, run_id="r1", report_id="p1")
    assert default.source_kind == "SIMULATED"
    live = estimate_pose(render_frontal_tag(0), tm, run_id="r1", report_id="p1",
                         source_kind="LIVE")
    assert live.source_kind == "LIVE"


@pytest.mark.parametrize("raw,expected", [(0, 0), (90, 90), (180, 180), (270, -90), (359, -1)])
def test_heading_normalized_to_signed_range(raw, expected):
    from sentinel.pose.detector import normalize_heading_deg
    assert normalize_heading_deg(raw) == pytest.approx(expected)
