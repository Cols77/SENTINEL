"""Semantic room-pose resolution: 3-axis vocabulary -> concrete executor poses.

Plan 03 Task 1. Covers only endpoint resolution; corridor/swept-path validation
is Task 2+ and is deliberately not exercised here.
"""
from __future__ import annotations

import itertools
import json
import jsonschema
from pathlib import Path
import re

import pytest

from sentinel.cube.checkpoint import HomeDefinition
from sentinel.models import Position
from sentinel.room.semantic import (
    DEPTH_VALUES,
    HEIGHT_VALUES,
    HORIZONTAL_VALUES,
    LOOK_VALUES,
    ResolvedPose,
    SemanticPose,
    canonical_semantic_poses,
    render_semantic_vocabulary,
    resolve_landmark,
    resolve_semantic_pose,
    resolve_semantic_sequence,
    semantic_vocabulary,
)
from sentinel.room.site import (
    ContingencyKind,
    ContingencyTrigger,
    RoomSite,
    RouteDefinition,
    SemanticFrame,
    TerminalContingency,
    Volume3D,
    parse_room_site,
    room_site_document,
)

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures/room_agent/v1/semantic-site.json"
CORNERS_PATH = Path(__file__).resolve().parents[2] / "docs/wp5-flight3-corners.json"
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs/superpowers/specs/contracts/room-site-base-v1.schema.json"

RECT = ((150, 120), (350, 120), (350, 260), (150, 260))
TRIANGLE = ((0, 0), (300, 0), (0, 300))


def _terminal_contingencies(polygon, z_band):
    return tuple(TerminalContingency(trigger.value, trigger, ContingencyKind.LAND_IN_PLACE,
        polygon, z_band, "COASTING", "survey", "checked-clear", 5000) for trigger in ContingencyTrigger)


def _site(*, semantic_frame=None, envelope=RECT, z_band=(60, 140), home_xy=(250, 140)):
    """A minimal, valid RoomSite for resolver unit tests (bypasses JSON parsing)."""
    return RoomSite(
        "room", "a" * 64, "b" * 64,
        (Volume3D("room", envelope, z_band),), (),
        (Volume3D("corridor", envelope, z_band),),
        (RouteDefinition("home-route", (Position(home_xy[0], home_xy[1], 90),), ("corridor",)),),
        HomeDefinition("pad", Position(home_xy[0], home_xy[1], 90), 90, "home-route"),
        _terminal_contingencies(envelope, z_band), "survey-revision-1",
        semantic_frame=semantic_frame,
    )


def _rect_frame(landmarks=()):
    return SemanticFrame("room", (1.0, 0.0), (0.0, 1.0), "FRONT", landmarks)


def _fixture_site() -> RoomSite:
    doc = json.loads(FIXTURE_PATH.read_text())
    return parse_room_site(doc)


def test_all_27_canonical_positions_resolve_with_distinct_pose_ids():
    site = _fixture_site()
    poses = canonical_semantic_poses()
    assert len(poses) == 27
    resolved = [resolve_semantic_pose(site, pose) for pose in poses]
    assert all(isinstance(r, ResolvedPose) for r in resolved)
    assert len({r.pose_id for r in resolved}) == 27


def test_pose_ids_are_deterministic_strings_distinct_per_combination():
    site = _fixture_site()
    ids = set()
    for horizontal, depth, height, look in itertools.product(HORIZONTAL_VALUES, DEPTH_VALUES, HEIGHT_VALUES, LOOK_VALUES):
        pose = SemanticPose(horizontal, depth, height, look)
        ids.add(resolve_semantic_pose(site, pose).pose_id)
    # 3 horizontal x 3 depth x 3 height x 3 look, pinned as a literal so a change
    # to the pinned vocabulary size cannot silently pass by tracking itself.
    assert len(ids) == 81


def test_resolution_is_deterministic_for_identical_input():
    site = _fixture_site()
    pose = SemanticPose("left", "rear", "bottom")
    first = resolve_semantic_pose(site, pose)
    second = resolve_semantic_pose(site, pose)
    assert first == second


def test_edge_thirds_are_inset_from_the_envelope_wall():
    site = _fixture_site()
    left = resolve_semantic_pose(site, SemanticPose("left", "middle", "middle"))
    right = resolve_semantic_pose(site, SemanticPose("right", "middle", "middle"))
    rear = resolve_semantic_pose(site, SemanticPose("center", "rear", "middle"))
    front = resolve_semantic_pose(site, SemanticPose("center", "front", "middle"))
    assert 150 < left.x_cm < 350 and left.x_cm != 150
    assert 150 < right.x_cm < 350 and right.x_cm != 350
    assert 120 < rear.y_cm < 260 and rear.y_cm != 120
    assert 120 < front.y_cm < 260 and front.y_cm != 260
    # 'left' must sit lower along the declared left_to_right axis than 'right' --
    # not just anywhere inside the inset band -- so swapping the fraction table
    # entries for left/right cannot silently pass.
    assert left.x_cm < right.x_cm


def test_front_is_the_max_y_side_for_this_site():
    site = _fixture_site()
    rear = resolve_semantic_pose(site, SemanticPose("center", "rear", "middle"))
    front = resolve_semantic_pose(site, SemanticPose("center", "front", "middle"))
    assert front.y_cm > rear.y_cm


def test_height_values_move_only_altitude():
    site = _fixture_site()
    bottom = resolve_semantic_pose(site, SemanticPose("center", "middle", "bottom"))
    top = resolve_semantic_pose(site, SemanticPose("center", "middle", "top"))
    assert top.z_cm > bottom.z_cm
    assert (bottom.x_cm, bottom.y_cm) == (top.x_cm, top.y_cm)


def test_depth_and_front_direction_are_axis_derived_not_hardcoded_to_y():
    # Flip rear_to_front_axis_xy to point toward -y; front must follow the
    # declared axis, not a hard-coded assumption that front == max Y. Every
    # other test in this file uses an identity frame, so without this test a
    # hard-coded x/y implementation would pass the whole suite.
    frame = SemanticFrame("room", (1.0, 0.0), (0.0, -1.0), "FRONT", ())
    site = _site(semantic_frame=frame, envelope=RECT, z_band=(60, 140), home_xy=(250, 140))
    rear = resolve_semantic_pose(site, SemanticPose("center", "rear", "middle"))
    front = resolve_semantic_pose(site, SemanticPose("center", "front", "middle"))
    assert front.y_cm < rear.y_cm


def test_horizontal_and_depth_track_rotated_axes_not_hardcoded_to_x_and_y():
    # Rotate the frame 90 degrees: horizontal now runs along Y, depth along X.
    # A hard-coded "horizontal writes x, depth writes y" implementation would
    # fail this even though it passes every identity-frame test above.
    frame = SemanticFrame("room", (0.0, 1.0), (-1.0, 0.0), "FRONT", ())
    site = _site(semantic_frame=frame, envelope=RECT, z_band=(60, 140), home_xy=(250, 140))
    left = resolve_semantic_pose(site, SemanticPose("left", "middle", "middle"))
    right = resolve_semantic_pose(site, SemanticPose("right", "middle", "middle"))
    rear = resolve_semantic_pose(site, SemanticPose("center", "rear", "middle"))
    front = resolve_semantic_pose(site, SemanticPose("center", "front", "middle"))
    assert left.x_cm == right.x_cm and left.y_cm != right.y_cm
    assert rear.y_cm == front.y_cm and rear.x_cm != front.x_cm


def test_yaw_deg_tracks_the_declared_look_direction():
    site = _fixture_site()
    toward_targets = resolve_semantic_pose(site, SemanticPose("center", "middle", "middle", "toward_targets"))
    right = resolve_semantic_pose(site, SemanticPose("center", "middle", "middle", "right"))
    left = resolve_semantic_pose(site, SemanticPose("center", "middle", "middle", "left"))
    # Fixture axes: left_to_right=(1,0), rear_to_front=(0,1). look="left" is
    # -180, not 180, due to the [-180, 180) normalization in _yaw_for_look.
    assert toward_targets.yaw_deg == 90
    assert right.yaw_deg == 0
    assert left.yaw_deg == -180


def test_toward_targets_yaw_tracks_a_flipped_rear_to_front_axis():
    frame = SemanticFrame("room", (1.0, 0.0), (0.0, -1.0), "FRONT", ())
    site = _site(semantic_frame=frame, envelope=RECT, z_band=(60, 140), home_xy=(250, 140))
    resolved = resolve_semantic_pose(site, SemanticPose("center", "middle", "middle", "toward_targets"))
    assert resolved.yaw_deg == -90


def test_resolve_semantic_sequence_preserves_arbitrary_order_subsets_and_repeats():
    site = _fixture_site()
    requested = (
        SemanticPose("right", "front", "top"),
        SemanticPose("left", "rear", "bottom"),
        SemanticPose("right", "front", "top"),
        SemanticPose("center", "middle", "middle"),
    )
    resolved = resolve_semantic_sequence(site, requested)
    assert len(resolved) == 4
    assert [r.pose_id for r in resolved] == [resolve_semantic_pose(site, p).pose_id for p in requested]
    assert resolved[0] == resolved[2]


def test_site_landmarks_are_resolvable_to_concrete_positions():
    site = _fixture_site()
    position = resolve_landmark(site, "table")
    assert isinstance(position, Position)


def test_unknown_landmark_is_rejected():
    site = _fixture_site()
    with pytest.raises(ValueError):
        resolve_landmark(site, "nonexistent")


def test_duplicate_landmark_ids_rejected_at_load():
    doc = json.loads(FIXTURE_PATH.read_text())
    landmarks = doc["semantic_frame"]["landmarks"]
    doc["semantic_frame"]["landmarks"] = landmarks + [landmarks[0]]
    with pytest.raises(ValueError):
        parse_room_site(doc)


def test_non_normalized_axis_is_rejected():
    with pytest.raises(ValueError):
        SemanticFrame("room", (2.0, 0.0), (0.0, 1.0), "FRONT", ())


def test_non_orthogonal_axes_are_rejected():
    with pytest.raises(ValueError):
        SemanticFrame("room", (1.0, 0.0), (0.70710678, 0.70710678), "FRONT", ())


def test_wrong_schema_version_is_rejected():
    doc = json.loads(FIXTURE_PATH.read_text())
    doc["schema_version"] = "room-site-base-v2"
    with pytest.raises(ValueError):
        parse_room_site(doc)


def test_bad_sha256_is_rejected():
    doc = json.loads(FIXTURE_PATH.read_text())
    doc["tag_map_sha256"] = "not-a-hash"
    with pytest.raises(ValueError):
        parse_room_site(doc)


def test_fixture_round_trips_through_parse_and_document():
    site = _fixture_site()
    doc = room_site_document(site)
    assert parse_room_site(doc) == site
    assert doc["semantic_frame"]["envelope_volume_id"] == "room"


def test_semantic_fixture_and_document_validate_against_the_json_schema():
    schema = json.loads(SCHEMA_PATH.read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(json.loads(FIXTURE_PATH.read_text()), schema)
    jsonschema.validate(room_site_document(_fixture_site()), schema)


def test_semantic_frame_with_an_unknown_key_is_rejected_by_the_json_schema():
    schema = json.loads(SCHEMA_PATH.read_text())
    doc = json.loads(FIXTURE_PATH.read_text())
    doc["semantic_frame"]["bogus_extra_key"] = "nope"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, schema)


def test_site_without_semantic_frame_omits_the_key_and_still_round_trips():
    site = _site()
    assert site.semantic_frame is None
    doc = room_site_document(site)
    assert "semantic_frame" not in doc
    assert parse_room_site(doc) == site


def test_resolved_endpoint_outside_a_non_rectangular_envelope_is_rejected():
    # Triangle envelope: axis-projected bounding box (used to place the inset
    # third) is larger than the actual convex polygon, so the far corner
    # combination (right/front) lands outside the triangle itself even though
    # it is inside the rectangular bounding box. This is the "resolved
    # endpoint outside the envelope" containment case.
    frame = _rect_frame()
    site = _site(semantic_frame=frame, envelope=TRIANGLE, z_band=(0, 100), home_xy=(50, 50))
    with pytest.raises(ValueError):
        resolve_semantic_pose(site, SemanticPose("right", "front", "middle"))


def test_resolved_endpoint_inside_rectangular_envelope_is_accepted():
    frame = _rect_frame()
    site = _site(semantic_frame=frame, envelope=RECT, z_band=(60, 140), home_xy=(250, 140))
    resolved = resolve_semantic_pose(site, SemanticPose("right", "front", "top"))
    assert isinstance(resolved, ResolvedPose)


def test_site_with_no_semantic_frame_cannot_resolve():
    site = _site()
    with pytest.raises(ValueError):
        resolve_semantic_pose(site, SemanticPose("left", "rear", "bottom"))


def test_semantic_frame_referencing_missing_envelope_volume_is_rejected_at_load():
    doc = json.loads(FIXTURE_PATH.read_text())
    doc["semantic_frame"]["envelope_volume_id"] = "does-not-exist"
    with pytest.raises(ValueError):
        parse_room_site(doc)


def test_semantic_frame_referencing_a_forbidden_volume_is_rejected_at_load():
    # A semantic envelope must be free space: naming a forbidden volume would
    # let every resolved pose legally land inside a declared no-fly zone.
    doc = json.loads(FIXTURE_PATH.read_text())
    doc["forbidden_volumes"] = [{
        "volume_id": "hazard",
        "polygon_xy_cm": [[400, 400], [500, 400], [500, 500], [400, 500]],
        "z_interval_cm": [60, 140],
    }]
    doc["semantic_frame"]["envelope_volume_id"] = "hazard"
    with pytest.raises(ValueError):
        parse_room_site(doc)


def test_wp5_flight3_corners_document_is_never_a_valid_semantic_frame():
    doc = json.loads(CORNERS_PATH.read_text())
    with pytest.raises(ValueError):
        parse_room_site(doc)


def test_model_facing_vocabulary_contains_only_region_ids_and_landmark_ids():
    site = _fixture_site()
    vocabulary = semantic_vocabulary(site)
    # Pinned literals, not the implementation's own constants: renaming a region
    # (e.g. 'center' -> 'middle') would break the plan's pinned contract and
    # SemanticPose's Literal annotation, and must fail here too.
    assert tuple(vocabulary["horizontal"]) == ("left", "center", "right")
    assert tuple(vocabulary["depth"]) == ("rear", "middle", "front")
    assert tuple(vocabulary["height"]) == ("bottom", "middle", "top")
    assert "table" in vocabulary["landmark_ids"]
    assert len(vocabulary["pose_ids"]) == 27


def test_rendered_vocabulary_contains_no_coordinate_numbers():
    site = _fixture_site()
    rendered = render_semantic_vocabulary(site)
    assert isinstance(rendered, str) and rendered
    assert not re.search(r"\d", rendered)


def test_resolved_pose_is_not_exposed_by_the_vocabulary():
    site = _fixture_site()
    rendered = render_semantic_vocabulary(site)
    vocabulary = semantic_vocabulary(site)
    assert "x_cm" not in rendered and "y_cm" not in rendered and "z_cm" not in rendered
    assert "x_cm" not in json.dumps(vocabulary) and "yaw_deg" not in json.dumps(vocabulary)
