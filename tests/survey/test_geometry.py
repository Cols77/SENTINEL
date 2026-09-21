import math

import pytest

from sentinel.survey.geometry import bearing_of, ground_depth, position_of, ray_intersection
from sentinel.survey.observability import FOCAL_PX, PRINCIPAL_X, PRINCIPAL_Y


def test_an_object_in_the_centre_of_the_frame_has_zero_bearing():
    assert bearing_of({"x": 460, "y": 300, "width": 40, "height": 40}) == pytest.approx(0.0)


def test_an_object_to_the_right_has_a_positive_bearing():
    bearing = bearing_of({"x": 680, "y": 300, "width": 40, "height": 40})
    assert bearing == pytest.approx(math.atan(220 / FOCAL_PX))
    assert bearing > 0


def test_two_rays_crossing_ahead_return_their_intersection():
    point = ray_intersection((0.0, 0.0), (0.0, 1.0), (100.0, 0.0), (-1.0, 1.0))
    assert point == pytest.approx((0.0, 100.0))


def test_parallel_rays_do_not_intersect():
    assert ray_intersection((0.0, 0.0), (0.0, 1.0), (100.0, 0.0), (0.0, 1.0)) is None


def test_an_intersection_behind_either_camera_is_rejected():
    assert ray_intersection((0.0, 0.0), (0.0, 1.0), (100.0, 0.0), (1.0, 1.0)) is None


# --- rev 3 R1: depth from the ground plane, not from cross-stop triangulation ---


def test_ground_depth_sanity_pinned_from_the_spec():
    """Design spec 5.2: at delta_z 55 cm and D 300 cm, v must be ~125 px and
    dD/dv must be ~2.40 cm/px. Pinned against the real constants so a
    future change to FOCAL_PX or the formula is caught here."""
    delta_z_cm = 55.0
    v_px = delta_z_cm * FOCAL_PX / 300.0
    assert v_px == pytest.approx(125.0, abs=0.5)

    bbox = {"x": 440, "y": PRINCIPAL_Y + v_px - 20, "width": 40, "height": 40}
    depth_cm = ground_depth(bbox, delta_z_cm)
    assert depth_cm == pytest.approx(300.0, abs=1.0)

    sensitivity = delta_z_cm * FOCAL_PX / v_px ** 2
    assert sensitivity == pytest.approx(2.40, abs=0.02)


def test_ground_depth_grows_with_altitude_and_shrinks_with_pixel_offset():
    bbox = {"x": 440, "y": PRINCIPAL_Y + 100, "width": 40, "height": 40}
    near = ground_depth(bbox, 55.0)
    far = ground_depth(bbox, 110.0)
    assert far == pytest.approx(2 * near)


def test_a_box_at_or_above_the_image_centre_has_no_ground_plane_depth():
    """A box whose centre is at or above the principal row cannot be on the
    measured surface below the camera -- there is nothing to solve for."""
    bbox = {"x": 440, "y": PRINCIPAL_Y - 20, "width": 40, "height": 40}
    with pytest.raises(ValueError):
        ground_depth(bbox, 55.0)


def test_position_of_a_dead_ahead_sighting_has_zero_lateral_offset():
    v_px = 55.0 * FOCAL_PX / 300.0
    bbox = {"x": PRINCIPAL_X - 20, "y": PRINCIPAL_Y + v_px - 20, "width": 40, "height": 40}
    sighting = {"bbox": bbox, "x_cm": 0.0, "y_cm": 0.0, "heading_deg": 0.0, "look_deg": 0.0}
    x_cm, y_cm = position_of(sighting, 55.0)
    assert x_cm == pytest.approx(0.0, abs=1e-6)
    assert y_cm == pytest.approx(300.0, abs=1.0)


def test_position_of_combines_depth_and_bearing_from_the_cameras_own_stop():
    """A camera parked at x=75 looking at a target off to its left recovers
    that target's absolute room position, not a position relative to the
    origin stop."""
    target_x, target_y = -50.0, 250.0
    cam_x = 75.0
    dx, dy = target_x - cam_x, target_y
    range_cm = math.hypot(dx, dy)
    bearing = math.atan2(dx, dy)
    u = PRINCIPAL_X + FOCAL_PX * math.tan(bearing)
    v_px = 55.0 * FOCAL_PX / range_cm
    bbox = {"x": u - 20, "y": PRINCIPAL_Y + v_px - 20, "width": 40, "height": 40}
    sighting = {"bbox": bbox, "x_cm": cam_x, "y_cm": 0.0, "heading_deg": 0.0, "look_deg": 0.0}
    x_cm, y_cm = position_of(sighting, 55.0)
    assert x_cm == pytest.approx(target_x, abs=1.0)
    assert y_cm == pytest.approx(target_y, abs=1.0)
