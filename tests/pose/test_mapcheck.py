import math

import numpy as np

from sentinel.pose.mapcheck import compare_frames
from sentinel.pose.tagmap import TagMap
from tests.pose.rendering import SPLAYED_PAIR, make_doc, render


def test_compare_frames_reports_both_paths_and_the_zone_residual():
    cam = np.array([2.0, 4.0, 1.0])
    frames = [render(SPLAYED_PAIR, cam, 180.0) for _ in range(3)]
    frames.append(np.full((720, 960, 3), 255, dtype=np.uint8))  # blank: skipped
    result = compare_frames(frames, TagMap(make_doc(SPLAYED_PAIR)))
    assert result["frames"] == 4 and result["multi_tag_frames"] == 3
    assert result["single"]["n"] == 3 and result["joint"]["n"] == 3
    assert result["joint"]["spread_p95_cm"] == 0.0  # identical frames
    assert math.isclose(result["joint"]["median_x_units"], 20.0, abs_tol=0.5)
    assert result["zones"]["PAIR"]["n"] == 3
    assert result["zones"]["PAIR"]["median_joint_reprojection_px"] <= 2.0
