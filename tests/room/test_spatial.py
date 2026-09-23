"""Support-aware spatial estimation: bottom-box floor rays, declared-plane
intersections, and rejection of held/table/unknown-support center-ray guessing.

Plan 03 Task 2.
"""
from __future__ import annotations

import math

import pytest

from sentinel.camera import CameraModel
from sentinel.room.ingest import DetectionEvidence
from sentinel.room.spatial import (
    PoseEvidence,
    SpatialEstimate,
    SupportDeclaration,
    SupportKind,
    estimate_detection,
    semantic_heading_to_compass_deg,
)

LEVEL = CameraModel("test-level", 683.0, 683.0, 480.0, 360.0, 0.0)


def _point_bbox(u: float, v: float) -> tuple[float, float, float, float]:
    """A minimal box whose bottom-center pixel lands exactly on (u, v)."""
    return (u - 0.5, v - 1.0, 1.0, 1.0)


def _detection(detection_id="det-1", class_name="phone", bbox=None):
    return DetectionEvidence(
        detection_id=detection_id, class_name=class_name, confidence_band="high",
        raw_score=None, score_kind=None, bbox_xywh=bbox or _point_bbox(480.0, 530.75),
        bbox_coordinate_space="FULL_FRAME_PIXELS",
        frame_width_px=960, frame_height_px=720, tile_origin_xy_px=None,
        evidence_sha256="0" * 64,
    )


def _pose(**overrides):
    values = dict(x_cm=200.0, y_cm=100.0, z_cm=150.0, heading_deg=0.0, look_deg=0.0,
                  pose_provenance="MEASURED", captured_at="2026-09-23T00:00:00+00:00", stale=False)
    values.update(overrides)
    return PoseEvidence(**values)


FLOOR = SupportDeclaration(SupportKind.FLOOR)
TABLE = SupportDeclaration(SupportKind.DECLARED_HORIZONTAL_PLANE, "table", 90.0)
UNKNOWN = SupportDeclaration(SupportKind.UNKNOWN)


def test_support_kind_has_exactly_the_declared_members():
    assert {member.value for member in SupportKind} == {"FLOOR", "DECLARED_HORIZONTAL_PLANE", "UNKNOWN"}


def test_spatial_estimate_is_frozen_and_normalizes_sequence_fields():
    estimate = SpatialEstimate(
        "est-1", "det-1", "phone", 10.0, 20.0, [0.0, 30.0], [400.0, 400.0, 0.0],
        SupportKind.FLOOR, "FLOOR", "2026-09-23T00:00:00+00:00", "MEASURED", ["det-1"],
    )
    assert estimate.z_interval_cm == (0.0, 30.0)
    assert estimate.xy_covariance_cm2 == (400.0, 400.0, 0.0)
    assert estimate.evidence_refs == ("det-1",)
    with pytest.raises(AttributeError):
        estimate.x_cm = 99.0


@pytest.mark.parametrize("bad_kwargs", [
    dict(estimate_id=""),
    dict(z_interval_cm=(30.0, 0.0)),
    dict(xy_covariance_cm2=(0.0, 400.0, 0.0)),
    dict(evidence_refs=()),
])
def test_spatial_estimate_rejects_invalid_geometry(bad_kwargs):
    base = dict(estimate_id="est-1", source_detection_id="det-1", class_name="phone",
                x_cm=10.0, y_cm=20.0, z_interval_cm=(0.0, 30.0), xy_covariance_cm2=(400.0, 400.0, 0.0),
                support=SupportKind.FLOOR, support_plane_id="FLOOR",
                captured_at="2026-09-23T00:00:00+00:00", pose_provenance="MEASURED", evidence_refs=("det-1",))
    base.update(bad_kwargs)
    with pytest.raises(ValueError):
        SpatialEstimate(**base)


def test_floor_phone_uses_bottom_box_ray_with_measured_pose():
    estimate = estimate_detection(_detection(), _pose(), FLOOR, LEVEL)
    assert estimate is not None
    assert estimate.support == SupportKind.FLOOR
    assert estimate.support_plane_id == "FLOOR"
    assert estimate.z_interval_cm[0] == 0.0
    assert estimate.source_detection_id == "det-1"
    assert estimate.class_name == "phone"
    assert estimate.pose_provenance == "MEASURED"
    assert estimate.evidence_refs == ("det-1",)


def test_declared_table_height_shifts_the_resting_band_off_the_floor():
    estimate = estimate_detection(_detection(class_name="computer"), _pose(), TABLE, LEVEL)
    assert estimate is not None
    assert estimate.support == SupportKind.DECLARED_HORIZONTAL_PLANE
    assert estimate.support_plane_id == "table"
    assert estimate.z_interval_cm[0] == pytest.approx(90.0)
    assert estimate.z_interval_cm[1] > 90.0


def test_floor_and_table_estimates_never_share_a_support_plane():
    floor_estimate = estimate_detection(_detection(), _pose(), FLOOR, LEVEL)
    table_estimate = estimate_detection(_detection(class_name="computer"), _pose(), TABLE, LEVEL)
    assert floor_estimate.support_plane_id != table_estimate.support_plane_id


def test_unknown_support_is_rejected_never_guesses_a_center_ray_range():
    assert estimate_detection(_detection(), _pose(), UNKNOWN, LEVEL) is None


def test_stale_pose_invalidates_the_estimate():
    assert estimate_detection(_detection(), _pose(stale=True), FLOOR, LEVEL) is None


def test_commanded_pose_widens_covariance_relative_to_measured():
    measured = estimate_detection(_detection(), _pose(pose_provenance="MEASURED"), FLOOR, LEVEL)
    commanded = estimate_detection(_detection(), _pose(pose_provenance="COMMANDED"), FLOOR, LEVEL)
    assert commanded.xy_covariance_cm2[0] > measured.xy_covariance_cm2[0]
    assert commanded.xy_covariance_cm2[1] > measured.xy_covariance_cm2[1]


# --- Fix round 3 (F6): the semantic (math) / spatial (compass) heading -----
# --- convention boundary -----------------------------------------------------

def test_semantic_heading_to_compass_deg_converts_the_fixed_offset():
    assert semantic_heading_to_compass_deg(90.0) == 0.0
    assert semantic_heading_to_compass_deg(0.0) == 90.0
    assert semantic_heading_to_compass_deg(-90.0) == 180.0
    assert semantic_heading_to_compass_deg(180.0) == 270.0


def test_resolved_semantic_pose_heading_converts_correctly_for_spatial_estimation():
    """A resolved semantic pose's ``yaw_deg`` is in the math convention (0
    deg = +x, counter-clockwise, matching ``sentinel.cube.localization``);
    ``estimate_detection``'s ``PoseEvidence.heading_deg`` is in the compass
    convention (0 deg = +y, clockwise, inherited from ``sentinel.survey``).
    Feed a resolved pose's own yaw through ``semantic_heading_to_compass_deg``
    before building a ``PoseEvidence``, and confirm the resulting estimate
    lands further along the pose's "toward_targets" look direction (this
    site's declared ``rear_to_front_axis_xy``, here world +y) -- proving the
    conversion, not the raw unconverted yaw, is what a caller must use at
    this boundary."""
    from sentinel.room.semantic import SemanticPose, resolve_semantic_pose
    from tests.room.test_semantic import _rect_frame, _site

    site = _site(semantic_frame=_rect_frame())
    resolved = resolve_semantic_pose(site, SemanticPose("center", "middle", "middle"))
    assert resolved.yaw_deg == 90  # math convention: "toward_targets" here faces world +y

    compass_heading = semantic_heading_to_compass_deg(resolved.yaw_deg)
    assert compass_heading == 0.0  # compass convention: 0 deg also faces +y
    pose = _pose(x_cm=float(resolved.x_cm), y_cm=float(resolved.y_cm), z_cm=float(resolved.z_cm),
                heading_deg=compass_heading, look_deg=0.0)
    estimate = estimate_detection(_detection(), pose, FLOOR, LEVEL)
    assert estimate is not None
    assert estimate.y_cm > pose.y_cm
    assert math.isclose(estimate.x_cm, pose.x_cm, abs_tol=1.0)


def test_invalid_pose_geometry_returns_none_instead_of_raising():
    # A box centred at or above the horizon has no ground-plane intersection
    # from a level camera pitched at zero -- there is nothing to solve for.
    above_horizon = _detection(bbox=_point_bbox(480.0, 300.0))
    assert estimate_detection(above_horizon, _pose(), FLOOR, LEVEL) is None


def test_one_to_one_source_detection_mapping_and_class_preservation():
    left = _detection("det-left", "phone", bbox=_point_bbox(300.0, 530.75))
    right = _detection("det-right", "computer", bbox=_point_bbox(650.0, 530.75))
    left_estimate = estimate_detection(left, _pose(), FLOOR, LEVEL)
    right_estimate = estimate_detection(right, _pose(), FLOOR, LEVEL)
    assert left_estimate.source_detection_id == "det-left"
    assert right_estimate.source_detection_id == "det-right"
    assert left_estimate.class_name == "phone"
    assert right_estimate.class_name == "computer"
    assert left_estimate.estimate_id != right_estimate.estimate_id


def test_estimate_id_is_stable_for_identical_inputs():
    first = estimate_detection(_detection(), _pose(), FLOOR, LEVEL)
    second = estimate_detection(_detection(), _pose(), FLOOR, LEVEL)
    assert first.estimate_id == second.estimate_id


def test_covariance_grows_with_range_instead_of_a_fixed_isotropic_sigma():
    # A closer sighting and a farther sighting of the same class must not
    # share one fixed sigma -- ground-plane depth error grows with range
    # (survey.merge's tolerance model), so the farther estimate's variance
    # must be strictly larger.
    near = estimate_detection(_detection(bbox=_point_bbox(480.0, 530.75)), _pose(z_cm=150.0), FLOOR, LEVEL)
    far = estimate_detection(_detection(bbox=_point_bbox(480.0, 500.0)), _pose(z_cm=150.0), FLOOR, LEVEL)
    assert near is not None and far is not None
    near_range = math.hypot(near.x_cm - 200.0, near.y_cm - 100.0)
    far_range = math.hypot(far.x_cm - 200.0, far.y_cm - 100.0)
    assert far_range > near_range
    assert max(far.xy_covariance_cm2[:2]) > max(near.xy_covariance_cm2[:2])


def test_two_views_of_one_object_at_realistic_range_recover_a_tight_enough_estimate():
    # At a realistic ~2 m range, two independent views of one real object
    # must not each carry so little variance that the association gate
    # (normalized separation <= 1) can never merge them -- see Q9.
    from sentinel.room.association import _separation

    front = estimate_detection(
        _detection("det-a", bbox=_point_bbox(LEVEL.cx, 530.75)),
        _pose(x_cm=200.0, y_cm=100.0, z_cm=50.0, heading_deg=0.0), FLOOR, LEVEL,
    )
    side = estimate_detection(
        _detection("det-b", bbox=_point_bbox(LEVEL.cx, 587.6666666666666)),
        _pose(x_cm=350.0, y_cm=300.0, z_cm=50.0, heading_deg=270.0), FLOOR, LEVEL,
    )
    assert front is not None and side is not None
    separation = _separation(front.x_cm, front.y_cm, front.xy_covariance_cm2,
                             side.x_cm, side.y_cm, side.xy_covariance_cm2)
    assert separation <= 1.0


def test_view_id_matches_for_an_unmoved_pose_and_differs_for_a_moved_one():
    same_pose_a = estimate_detection(_detection("det-a"), _pose(captured_at="t0"), FLOOR, LEVEL)
    same_pose_b = estimate_detection(_detection("det-b"), _pose(captured_at="t1"), FLOOR, LEVEL)
    moved = estimate_detection(_detection("det-c"), _pose(captured_at="t2", x_cm=260.0), FLOOR, LEVEL)
    assert same_pose_a.view_id == same_pose_b.view_id
    assert same_pose_a.view_id != moved.view_id


def test_multi_view_consistency_two_poses_looking_at_the_same_floor_point_agree():
    """Two independently measured poses looking at one real floor point (200,
    300) recover nearly the same (x, y) -- from straight ahead, and from the
    side. The exact pixel geometry is worked out from the pinhole model so
    this is a real cross-check, not a tautology."""
    camera = LEVEL
    front = estimate_detection(
        _detection("det-a", bbox=_point_bbox(camera.cx, 530.75)),
        _pose(x_cm=200.0, y_cm=100.0, z_cm=50.0, heading_deg=0.0), FLOOR, camera,
    )
    side = estimate_detection(
        _detection("det-b", bbox=_point_bbox(camera.cx, 587.6666666666666)),
        _pose(x_cm=350.0, y_cm=300.0, z_cm=50.0, heading_deg=270.0), FLOOR, camera,
    )
    assert front is not None and side is not None
    assert math.isclose(front.x_cm, 200.0, abs_tol=0.1)
    assert math.isclose(front.y_cm, 300.0, abs_tol=0.1)
    assert math.isclose(front.x_cm, side.x_cm, abs_tol=0.5)
    assert math.isclose(front.y_cm, side.y_cm, abs_tol=0.5)
