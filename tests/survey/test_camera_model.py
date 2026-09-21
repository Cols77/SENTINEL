"""Coherence checks on the camera model (coherence spec section 10.5).

One camera model applies to every consumer of real Tello pixels. These
tests are what makes that sentence enforceable: they fail when the survey
package's measured model drifts from the calibration artifact, or from the
pitch the pose package uses."""
import json
import math
from pathlib import Path

import pytest

from sentinel.survey.geometry import ground_depth, ground_point, position_of
from sentinel.survey.observability import (CAMERAS, SYNTHETIC_LEVEL, TELLO_MEASURED,
                                           pixels_at, resolvable_classes)

ROOT = Path(__file__).resolve().parents[2]
CALIBRATION = ROOT / "calibration" / "tag-map-calibrated.json"


def _project(camera, lateral_cm, forward_cm, delta_z_cm):
    """Forward model: a point on the plane ``delta_z_cm`` below the lens,
    seen by a camera pitched down. Written independently of ``ground_point``
    (rotate the point into camera axes, then divide), not as its inverse."""
    pitch = math.radians(camera.pitch_down_deg)
    depth = forward_cm * math.cos(pitch) + delta_z_cm * math.sin(pitch)
    below = -forward_cm * math.sin(pitch) + delta_z_cm * math.cos(pitch)
    return (camera.cx + camera.fx * lateral_cm / depth, camera.cy + camera.fy * below / depth)


def _box(u, v):
    return {"x": u - 20, "y": v - 20, "width": 40, "height": 40}


def test_the_measured_model_matches_the_calibration_artifact():
    calibration = json.loads(CALIBRATION.read_text(encoding="utf-8"))["camera_calibration"]
    assert calibration["calibration_source"] == "chessboard"
    assert (calibration["width"], calibration["height"]) == (TELLO_MEASURED.width,
                                                             TELLO_MEASURED.height)
    for field in ("fx", "fy", "cx", "cy"):
        assert getattr(TELLO_MEASURED, field) == pytest.approx(calibration[field], rel=0.005), field


def test_the_measured_pitch_matches_the_pose_package():
    layout = pytest.importorskip("sentinel.pose.layout")
    assert TELLO_MEASURED.pitch_down_deg == layout.CAMERA_PITCH_DOWN_DEG


def test_the_measured_horizon_is_where_the_recording_shows_it():
    """The pitch was measured from a wall/floor junction near row 215
    (docs/wp5-rehearsal-manifest.md); the model must put the horizon there,
    not on the principal row."""
    assert TELLO_MEASURED.horizon_row == pytest.approx(215.0, abs=6.0)
    assert SYNTHETIC_LEVEL.horizon_row == pytest.approx(SYNTHETIC_LEVEL.cy)


def test_the_stream_field_of_view_is_the_measured_one_not_the_datasheet_one():
    left, right = TELLO_MEASURED.horizontal_half_angles
    assert math.degrees(left + right) == pytest.approx(56.5, abs=0.3)


def test_both_models_are_selectable_by_name():
    assert set(CAMERAS) == {"synthetic-level", "tello-measured"}


def test_the_spec_worked_example_on_the_measured_camera():
    """Design spec 5.2 rev 5: 55 cm above the surface, 3 m ahead -> the box
    centre is about 28 px below the principal row, and one pixel is worth
    about 1.9 cm of depth there."""
    _, v = _project(TELLO_MEASURED, 0.0, 300.0, 55.0)
    assert v - TELLO_MEASURED.cy == pytest.approx(28.0, abs=1.0)
    depth = ground_depth(_box(TELLO_MEASURED.cx, v), 55.0, TELLO_MEASURED)
    assert depth == pytest.approx(300.0, abs=0.5)
    one_px_lower = ground_depth(_box(TELLO_MEASURED.cx, v + 1), 55.0, TELLO_MEASURED)
    assert depth - one_px_lower == pytest.approx(1.9, abs=0.1)


def test_the_level_shortcut_would_misplace_a_real_object_by_a_large_factor():
    """The defect this revision fixes, pinned so it cannot come back: real
    pixels inverted with the synthetic model land far beyond the truth."""
    u, v = _project(TELLO_MEASURED, 0.0, 300.0, 55.0)
    wrong = ground_depth(_box(u, v), 55.0, SYNTHETIC_LEVEL)
    assert wrong > 5.0 * 300.0
    # Further out the box rises above the synthetic principal row and the
    # level model refuses an object that is plainly on the table.
    u, v = _project(TELLO_MEASURED, 0.0, 450.0, 55.0)
    with pytest.raises(ValueError):
        ground_depth(_box(u, v), 55.0, SYNTHETIC_LEVEL)


def test_an_object_beyond_the_level_models_limit_is_still_placed():
    """With the axis pitched down, objects past 3.6 m sit above the
    principal row. They are below the horizon and must still be located."""
    u, v = _project(TELLO_MEASURED, 80.0, 450.0, 55.0)
    assert v < TELLO_MEASURED.cy
    lateral, forward = ground_point(_box(u, v), 55.0, TELLO_MEASURED)
    assert (lateral, forward) == pytest.approx((80.0, 450.0), abs=0.5)


def test_a_box_above_the_measured_horizon_has_no_ground_point():
    with pytest.raises(ValueError):
        ground_point(_box(480.0, TELLO_MEASURED.horizon_row - 5), 55.0, TELLO_MEASURED)


@pytest.mark.parametrize("camera", [SYNTHETIC_LEVEL, TELLO_MEASURED], ids=lambda c: c.name)
def test_an_off_axis_object_is_recovered_under_a_heading(camera):
    """Forward distance is not slant range: an object well off the axis,
    seen from a stop that is itself rotated, comes back where it is."""
    heading = 20.0
    lateral, forward = -120.0, 260.0
    u, v = _project(camera, lateral, forward, 55.0)
    sighting = {"bbox": _box(u, v), "x_cm": 75.0, "y_cm": 10.0,
                "heading_deg": heading, "look_deg": 0.0}
    angle = math.radians(heading)
    expected = (75.0 + forward * math.sin(angle) + lateral * math.cos(angle),
                10.0 + forward * math.cos(angle) - lateral * math.sin(angle))
    assert position_of(sighting, 55.0, camera) == pytest.approx(expected, abs=0.5)


def test_the_measured_camera_resolves_more_at_range():
    assert pixels_at("phone", 300.0, TELLO_MEASURED) == pytest.approx(44.6, abs=0.1)
    assert "phone" in resolvable_classes(600.0, TELLO_MEASURED)
    assert "phone" not in resolvable_classes(600.0, SYNTHETIC_LEVEL)
    assert "pen" not in resolvable_classes(300.0, TELLO_MEASURED)


def test_generated_options_follow_the_camera_they_are_generated_for():
    """The real camera sees a narrower strip and resolves more at range, so
    the options the agent is offered must say so."""
    from sentinel.survey.envelope import SweepEnvelope
    from sentinel.survey.options import generate_options

    envelope = SweepEnvelope(-150, 150, 0, 100, 80, 180, 300)
    synthetic = {o.option_id: o for o in generate_options(envelope)}
    measured = {o.option_id: o for o in generate_options(envelope, TELLO_MEASURED)}
    assert set(synthetic) == set(measured)
    for option_id, option in measured.items():
        assert option.stops == synthetic[option_id].stops
        assert option.viewed_width_cm < synthetic[option_id].viewed_width_cm
        assert set(synthetic[option_id].resolvable_classes) <= set(option.resolvable_classes)
