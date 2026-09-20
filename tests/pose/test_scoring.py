"""Ground-truth scoring + cross-tag consistency tests."""
import json
import sqlite3
from datetime import datetime, timezone

import cv2
import numpy as np
import pytest

from sentinel.contracts.pose import DronePoseReport, TagObservation
from sentinel.pose.detector import estimate_all, flag_tag_disagreement
from sentinel.pose.persistence import append_pose_event, ensure_schema
from sentinel.pose.scoring import Thresholds, heading_error_deg, load_truth, score_runs
from sentinel.pose.tagmap import TagMap

K = np.array([[547.0, 0, 480.0], [0, 547.0, 360.0], [0, 0, 1]])
SIZE_M = 0.16


def _report(run_id, i, x, y, heading):
    return DronePoseReport(
        report_id=f"00000000-0000-4000-8000-{i:012d}", run_id=run_id,
        observed_at=datetime.now(timezone.utc), source_kind="SIMULATED",
        point=(x, y), heading_deg=heading, altitude_m=None,
        method="APRILTAG_36H11_PNP",
        tag_observations=(TagObservation(tag_id=0, zone_id="Z", distance_m=0.5,
                                         reprojection_error_px=0.5),),
        reprojection_error_px=0.5)


def _db(tmp_path, runs):
    path = str(tmp_path / "pose.db")
    conn = sqlite3.connect(path)
    ensure_schema(conn)
    n = 0
    for run_id, poses in runs.items():
        for x, y, h in poses:
            n += 1
            append_pose_event(conn, _report(run_id, n, x, y, h))
    conn.close()
    return path


def test_heading_error_wraps():
    assert heading_error_deg(179.0, -179.0) == pytest.approx(2.0)
    assert heading_error_deg(-179.0, 179.0) == pytest.approx(2.0)
    assert heading_error_deg(10.0, 350.0) == pytest.approx(20.0)


def test_load_truth(tmp_path):
    p = tmp_path / "truth.json"
    p.write_text(json.dumps({"r1": {"x": 1, "y": 2, "heading_deg": 90}}))
    assert load_truth(str(p)) == {"r1": {"x": 1.0, "y": 2.0, "heading_deg": 90.0}}


def test_load_truth_rejects_missing_keys(tmp_path):
    p = tmp_path / "truth.json"
    p.write_text(json.dumps({"r1": {"x": 1}}))
    with pytest.raises(ValueError):
        load_truth(str(p))


def test_score_pass(tmp_path):
    # 0.05 units = 0.5 cm error, well within 10 cm
    db = _db(tmp_path, {"r1": [(10.0, 10.0, 90.0), (10.05, 10.0, 91.0), (10.0, 10.05, 89.0)]})
    res = score_runs(db, {"r1": {"x": 10.0, "y": 10.0, "heading_deg": 90.0}}, Thresholds())
    r = res["runs"]["r1"]
    assert r["n"] == 3
    assert r["median_error_units"] == pytest.approx(0.05)
    assert r["median_error_cm"] == pytest.approx(0.5)
    assert r["p95_error_cm"] <= 0.5 + 1e-9
    assert r["heading_p95_deg"] == pytest.approx(1.0)
    assert r["passed"] is True and res["passed"] is True


def test_score_fail_position(tmp_path):
    # 2 units = 20 cm
    db = _db(tmp_path, {"r1": [(12.0, 10.0, 90.0)] * 3})
    res = score_runs(db, {"r1": {"x": 10.0, "y": 10.0, "heading_deg": 90.0}}, Thresholds())
    assert res["runs"]["r1"]["p95_error_cm"] == pytest.approx(20.0)
    assert res["runs"]["r1"]["passed"] is False and res["passed"] is False


def test_score_fail_heading_wrap_safe(tmp_path):
    # truth 179, measured -170 => 11 deg (wrap-safe), fails 5 deg
    db = _db(tmp_path, {"r1": [(10.0, 10.0, -170.0)] * 2})
    res = score_runs(db, {"r1": {"x": 10.0, "y": 10.0, "heading_deg": 179.0}}, Thresholds())
    assert res["runs"]["r1"]["heading_p95_deg"] == pytest.approx(11.0)
    assert res["passed"] is False


def test_score_fail_closed_missing_run_and_empty(tmp_path):
    db = _db(tmp_path, {"r1": [(10.0, 10.0, 0.0)]})
    truth = {"r1": {"x": 10.0, "y": 10.0, "heading_deg": 0.0},
             "r2": {"x": 0.0, "y": 0.0, "heading_deg": 0.0}}
    res = score_runs(db, truth, Thresholds())
    assert res["runs"]["r2"]["n"] == 0 and res["runs"]["r2"]["passed"] is False
    assert res["passed"] is False
    assert score_runs(db, {}, Thresholds())["passed"] is False


def test_custom_thresholds(tmp_path):
    db = _db(tmp_path, {"r1": [(12.0, 10.0, 90.0)]})
    res = score_runs(db, {"r1": {"x": 10.0, "y": 10.0, "heading_deg": 90.0}},
                     Thresholds(p95_cm=25.0, heading_deg=5.0))
    assert res["passed"] is True


# ---- cross-tag consistency ----

def _map_with_positions(p0, p1):
    def tag(tid, p):
        return {"tag_id": tid, "zone_id": f"Z{tid}", "x_units": p[0], "y_units": p[1],
                "z_units": 1.5, "yaw_deg": 0.0, "size_m": SIZE_M, "mounting": "WALL"}
    return TagMap({
        "schema_version": "tag-map-v1", "tag_family": "36h11",
        "room": {"width_units": 100, "height_units": 100, "origin": "X"},
        "tags": [tag(0, p0), tag(1, p1)],
        "camera_calibration": {"width": 960, "height": 720, "fx": 547.0, "fy": 547.0,
                               "cx": 480.0, "cy": 360.0, "dist_coeffs": [],
                               "calibration_source": "nominal"}})


def _render_two_tags():
    """Two tags on one wall plane, camera 0.5 m away, frontal.

    Tag 0 is 0.15 m left of the camera axis, tag 1 0.15 m right.
    """
    aruco = cv2.aruco
    dictionary = aruco.getPredefinedDictionary(aruco.DICT_APRILTAG_36h11)
    frame = np.full((720, 960, 3), 255, dtype=np.uint8)
    s = SIZE_M / 2
    for tid, cx in ((0, -0.15), (1, 0.15)):
        marker = aruco.generateImageMarker(dictionary, tid, 200)
        obj = np.array([[cx - s, s, 0], [cx + s, s, 0], [cx + s, -s, 0], [cx - s, -s, 0]],
                       dtype=np.float64)
        pts, _ = cv2.projectPoints(obj, np.zeros((3, 1)), np.array([[0.0], [0.0], [0.5]]),
                                   K, np.zeros((0,)))
        h, w = marker.shape[:2]
        src = np.array([[0, h], [w, h], [w, 0], [0, 0]], dtype=np.float32)
        M = cv2.getPerspectiveTransform(src, pts.reshape(-1, 2).astype(np.float32))
        warped = cv2.warpPerspective(marker, M, (960, 720), flags=cv2.INTER_NEAREST,
                                     borderMode=cv2.BORDER_CONSTANT,
                                     borderValue=(255, 255, 255))
        frame = np.where(warped[..., None] == 255, frame, warped[..., None])
    return frame


def test_estimate_all_returns_pose_per_mapped_tag():
    poses = estimate_all(_render_two_tags(), _map_with_positions((5.0, 10.0), (5.0, 13.0)))
    assert sorted(p.tag_id for p in poses) == [0, 1]
    for p in poses:
        assert p.reprojection_error_px < 2.0
        assert -180.0 <= p.heading_deg <= 180.0


def test_estimate_all_no_tags_raises():
    with pytest.raises(ValueError):
        estimate_all(np.full((720, 960, 3), 255, dtype=np.uint8),
                     _map_with_positions((5.0, 10.0), (5.0, 13.0)))


def _flags(tm):
    return flag_tag_disagreement(estimate_all(_render_two_tags(), tm),
                                 max_position_units=0.5, max_heading_deg=3.0)


def test_consistent_tags_not_flagged_and_inconsistent_flagged():
    # Exactly one sign of the 3-unit (0.3 m) y offset matches the rendering.
    results = {dy: _flags(_map_with_positions((5.0, 10.0), (5.0, 10.0 + dy)))
               for dy in (3.0, -3.0)}
    consistent = [dy for dy, f in results.items() if not f]
    assert len(consistent) == 1
    good = consistent[0]
    flags = _flags(_map_with_positions((5.0, 10.0), (5.0, 10.0 + good + 5.0)))
    assert flags and flags[0]["tag_ids"] == (0, 1)
    assert flags[0]["position_delta_units"] > 0.5


def test_single_tag_never_flagged():
    poses = estimate_all(_render_two_tags(), _map_with_positions((5.0, 10.0), (5.0, 13.0)))
    assert flag_tag_disagreement(poses[:1], max_position_units=0.5, max_heading_deg=3.0) == []


def test_nominal_calibration_fails_run(tmp_path):
    db = _db(tmp_path, {"r1": [(10.0, 10.0, 90.0)]})
    truth = {"r1": {"x": 10.0, "y": 10.0, "heading_deg": 90.0}}
    res = score_runs(db, truth, Thresholds(), calibration_source="nominal")
    assert res["passed"] is False and res["calibration_ok"] is False
    assert res["runs"]["r1"]["passed"] is True  # errors are fine; the calibration is not
    ok = score_runs(db, truth, Thresholds(), calibration_source="calibrated")
    assert ok["passed"] is True and ok["calibration_ok"] is True
    allowed = score_runs(db, truth, Thresholds(), calibration_source="nominal", allow_nominal=True)
    assert allowed["passed"] is True
