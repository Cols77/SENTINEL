import json
import math
from pathlib import Path

import pytest

from sentinel.camera import TELLO_MEASURED
from sentinel.cube import observe
from sentinel.cube.observe import BELOW_HORIZON_DEG, observation_candidates
from sentinel.envelope import FlightEnvelope

ENV = FlightEnvelope(min_x_cm=60, max_x_cm=480, min_y_cm=230, max_y_cm=390,
                     min_z_cm=80, max_z_cm=180, max_move_cm=300)

ROOT = Path(__file__).resolve().parents[2]
CALIBRATION = ROOT / "calibration" / "tag-map-calibrated.json"


def test_a_narrow_alley_needs_only_the_close_candidate():
    # width 120 -> width_fraction 0.986 under the corrected (narrower) FOV, so
    # it now also gets a back_and_wide candidate; 110 keeps this alley inside
    # the single close_and_deep view, which is what the test means to pin.
    (c,) = observation_candidates(150, 500, 110, ENV)
    assert (c.kind, c.x_cm, c.y_cm, c.z_cm, c.look_deg) == ("close_and_deep", 150, 390, 80, 0)
    assert c.standoff_cm == 110 and c.width_fraction == 1.0
    # measured camera (coherence spec 10.5): bottom of the view is ~30.9 deg
    # below the horizon, not the old datasheet-derived 36.4 deg.
    assert c.near_blind_cm == pytest.approx(80 / math.tan(math.radians(BELOW_HORIZON_DEG)), abs=1.0)


def test_a_wide_alley_adds_a_back_and_wide_candidate():
    close, wide = observation_candidates(150, 500, 200, ENV)
    assert close.width_fraction == pytest.approx(observe.WIDTH_PER_RANGE * 110 / 200, abs=0.01)
    assert (wide.kind, wide.x_cm, wide.y_cm) == ("back_and_wide", 150, 314)
    assert wide.width_fraction == 1.0


def test_an_alley_beside_the_bounds_gets_an_oblique_look():
    (c,) = observation_candidates(20, 500, 120, ENV)
    assert (c.kind, c.x_cm, c.y_cm, c.look_deg) == ("oblique", 60, 390, 20)


def test_observe_constants_agree_with_the_calibrated_camera():
    """Pins observe.py's constants against the calibration artifact, same
    role test_the_measured_model_matches_the_calibration_artifact plays for
    TELLO_MEASURED itself (tests/survey/test_camera_model.py) -- the pitch is
    not in the calibration file (measured separately), so it is sourced from
    TELLO_MEASURED.pitch_down_deg, already pinned there against layout.py."""
    calibration = json.loads(CALIBRATION.read_text(encoding="utf-8"))["camera_calibration"]
    fx, fy, cx = calibration["fx"], calibration["fy"], calibration["cx"]
    cy, height = calibration["cy"], calibration["height"]
    left, right = math.atan(cx / fx), math.atan((calibration["width"] - cx) / fx)
    width_per_range = math.tan(left) + math.tan(right)
    below_horizon_deg = TELLO_MEASURED.pitch_down_deg + math.degrees(
        math.atan((height - cy) / fy))
    # TELLO_MEASURED's constants are rounded from the calibration file (see
    # test_the_measured_model_matches_the_calibration_artifact's rel=0.005),
    # so this only needs to agree to a similarly loose tolerance.
    assert observe.WIDTH_PER_RANGE == pytest.approx(width_per_range, rel=1e-3)
    assert BELOW_HORIZON_DEG == pytest.approx(below_horizon_deg, rel=1e-3)
