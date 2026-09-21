"""Joint multi-tag PnP."""
import math

import numpy as np
import pytest

from sentinel.pose import detector
from sentinel.pose.detector import estimate_pose
from sentinel.pose.tagmap import TagMap
from tests.pose.rendering import SCALE, SPLAYED_PAIR, make_doc, render


def _scene(range_m=1.5, off_axis_deg=20.0):
    a = math.radians(off_axis_deg)
    cam = np.array([0.5 + range_m * math.cos(a), 4.0 + range_m * math.sin(a), 1.0])
    heading = math.degrees(math.atan2(4.0 - cam[1], 0.5 - cam[0]))
    return cam, heading, render(SPLAYED_PAIR, cam, heading)


def _error_units(report, cam):
    return math.hypot(report.point[0] - cam[0] / SCALE, report.point[1] - cam[1] / SCALE)


def test_joint_solve_uses_both_tags_and_recovers_truth():
    cam, heading, frame = _scene()
    report = estimate_pose(frame, TagMap(make_doc(SPLAYED_PAIR)), run_id="r", report_id="p")
    assert sorted(o.tag_id for o in report.tag_observations) == [0, 1]
    assert _error_units(report, cam) <= 0.5          # 5 cm; the spike measured 1.7 cm
    assert abs((report.heading_deg - heading + 180.0) % 360.0 - 180.0) <= 2.0
    assert report.reprojection_error_px <= 2.0
    assert report.method == "APRILTAG_36H11_PNP"


def test_joint_false_keeps_the_single_tag_path():
    cam, _, frame = _scene()
    report = estimate_pose(frame, TagMap(make_doc(SPLAYED_PAIR)), run_id="r", report_id="p",
                           joint=False)
    assert len(report.tag_observations) == 1
    assert _error_units(report, cam) <= 1.0


def test_joint_gate_falls_back_to_the_best_single_tag_on_a_wrong_map():
    _, _, frame = _scene()
    wrong = [dict(SPLAYED_PAIR[0]),
             dict(SPLAYED_PAIR[1], y_units=SPLAYED_PAIR[1]["y_units"] - 3.0)]  # 30 cm map error
    report = estimate_pose(frame, TagMap(make_doc(wrong)), run_id="r", report_id="p")
    assert len(report.tag_observations) == 1
    assert report.reprojection_error_px <= 2.0


def test_a_tag_that_fails_its_gate_is_dropped_not_fatal(monkeypatch):
    _, _, frame = _scene()
    real = detector._solve_one
    monkeypatch.setattr(detector, "_solve_one",
                        lambda tag, corners, tm: None if tag.tag_id == 1 else real(tag, corners, tm))
    report = estimate_pose(frame, TagMap(make_doc(SPLAYED_PAIR)), run_id="r", report_id="p")
    assert [o.tag_id for o in report.tag_observations] == [0]


def test_every_tag_dropped_fails_closed(monkeypatch):
    _, _, frame = _scene()
    monkeypatch.setattr(detector, "_solve_one", lambda tag, corners, tm: None)
    with pytest.raises(ValueError, match="no mapped tag passed"):
        estimate_pose(frame, TagMap(make_doc(SPLAYED_PAIR)), run_id="r", report_id="p")
