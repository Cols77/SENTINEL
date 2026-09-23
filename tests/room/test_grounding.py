"""Plan 03 residual task R (F2): per-frame grounded count evidence and the
session COUNT projection built from it.

``ground_frame`` turns one authorized perception result into persisted,
replayable count evidence (support-aware spatial estimates, a per-frame
people split, and every detection that could not be placed).
``project_objective_count`` associates those estimates across frames and
projects them into the confirmed/uncertain counts a terminal answer renders.
"""
from __future__ import annotations

from dataclasses import replace
import json
import math

import pytest

from sentinel.camera import CameraModel
from sentinel.room.coverage import CellObservation, CoverageCell, project_coverage
from sentinel.room.goals import GoalObjective
from sentinel.room.grounding import (
    CountGeometry, FrameCountEvidence, ground_frame, project_objective_count, render_objective_count,
)
from sentinel.room.ingest import DetectionEvidence, detection_digest
from sentinel.room.models import GoalKind
from sentinel.room.spatial import PoseEvidence, SupportDeclaration, SupportKind

LEVEL = CameraModel("test-level", 683.0, 683.0, 480.0, 360.0, 0.0)
FLOOR = SupportDeclaration(SupportKind.FLOOR)
GEOMETRY = CountGeometry(LEVEL, (("computer", FLOOR), ("phone", FLOOR)))


def pose(x_cm=0.0, y_cm=0.0, z_cm=50.0, *, captured_at="2026-09-23T00:00:00+00:00"):
    # Compass heading 0: the camera looks along +y, +x is to its right.
    return PoseEvidence(x_cm, y_cm, z_cm, 0.0, captured_at=captured_at)


def bbox_for(target_xy, camera_pose, camera=LEVEL, *, width=30.0, height=40.0):
    """Invert estimate_detection for a level camera looking along +y: the
    bottom-centre pixel whose floor ray lands exactly on ``target_xy``."""
    forward = target_xy[1] - camera_pose.y_cm
    lateral = target_xy[0] - camera_pose.x_cm
    u = camera.cx + camera.fx * lateral / forward
    v = camera.cy + camera.fy * camera_pose.z_cm / forward
    return (u - width / 2.0, v - height, width, height)


def detection(detection_id, class_name="computer", band="high", bbox=(465.0, 400.0, 30.0, 40.0),
              *, width=960, height=720):
    evidence = DetectionEvidence(detection_id, class_name, band, None, "VLM_CONFIDENCE_BAND", bbox,
                                 "FULL_FRAME_PIXELS", width, height, None, "")
    return replace(evidence, evidence_sha256=detection_digest(evidence))


def count_objective(classes=("computer",), scope="ROOM"):
    return GoalObjective("g1", 1, GoalKind.COUNT, classes, None, None, scope, "REPORTED", None, None, ())


_CELL = CoverageCell("cell1", ((0, 0), (400, 0), (400, 400), (0, 400)), (0, 100), ("computer",))


def complete_coverage():
    return project_coverage((_CELL,), (CellObservation("cell1", "computer", "p1", "OBSERVED",
                                                       "2026-09-23T00:00:01+00:00", ("ev",)),))


# --- ground_frame --------------------------------------------------------

def test_floor_detection_is_placed_where_its_bottom_ray_meets_the_floor():
    camera_pose = pose()
    frame = ground_frame((detection("d1", bbox=bbox_for((40.0, 200.0), camera_pose)),),
                         observation_id="obs-1", frame_id="frame-1", pose_id="p1",
                         pose=camera_pose, geometry=GEOMETRY)
    estimate, = frame.estimates
    assert math.isclose(estimate.x_cm, 40.0, abs_tol=1e-6)
    assert math.isclose(estimate.y_cm, 200.0, abs_tol=1e-6)
    assert estimate.class_name == "computer" and estimate.support == SupportKind.FLOOR
    # Detection IDs are delivery-local; the estimate's source is qualified by
    # its observation so two frames' "d1" can never collide.
    assert estimate.source_detection_id == "obs-1/d1"
    assert frame.unplaced == () and frame.low_confidence_estimate_ids == ()


def test_detection_without_declared_support_or_geometry_is_unplaced_never_guessed():
    camera_pose = pose()
    geometry = CountGeometry(LEVEL, (("phone", FLOOR),))
    frame = ground_frame((detection("d1", "computer", bbox=bbox_for((0.0, 200.0), camera_pose)),),
                         observation_id="obs-1", frame_id="frame-1", pose_id="p1",
                         pose=camera_pose, geometry=geometry)
    assert frame.estimates == ()
    assert [(u.detection_id, u.class_name, u.reason) for u in frame.unplaced] == [
        ("obs-1/d1", "computer", "SUPPORT_UNDECLARED")]

    frame = ground_frame((detection("d1", bbox=bbox_for((0.0, 200.0), camera_pose)),),
                         observation_id="obs-1", frame_id="frame-1", pose_id="p1",
                         pose=camera_pose, geometry=None)
    assert frame.estimates == () and frame.unplaced[0].reason == "NO_COUNT_GEOMETRY"


def test_frame_size_that_does_not_match_the_camera_model_is_unplaced():
    frame = ground_frame((detection("d1", bbox=(1.0, 300.0, 30.0, 40.0), width=640, height=480),),
                         observation_id="obs-1", frame_id="frame-1", pose_id="p1",
                         pose=pose(), geometry=GEOMETRY)
    assert frame.estimates == () and frame.unplaced[0].reason == "FRAME_CAMERA_MISMATCH"


def test_box_above_the_horizon_or_stale_pose_is_unplaced():
    above = ground_frame((detection("d1", bbox=(465.0, 100.0, 30.0, 40.0)),),
                         observation_id="obs-1", frame_id="frame-1", pose_id="p1",
                         pose=pose(), geometry=GEOMETRY)
    assert above.unplaced[0].reason == "NOT_ON_SUPPORT_PLANE"
    stale = ground_frame((detection("d1", bbox=bbox_for((0.0, 200.0), pose())),),
                         observation_id="obs-1", frame_id="frame-1", pose_id="p1",
                         pose=replace(pose(), stale=True), geometry=GEOMETRY)
    assert stale.unplaced[0].reason == "STALE_POSE"


def test_people_are_split_by_confidence_and_never_become_estimates():
    frame = ground_frame((detection("p1", "person", "high"), detection("p2", "person", "high"),
                          detection("p3", "person", "low"), detection("p4", "person", "medium")),
                         observation_id="obs-1", frame_id="frame-1", pose_id="p1",
                         pose=pose(), geometry=GEOMETRY)
    assert frame.estimates == () and frame.unplaced == ()
    assert (frame.people_confirmed, frame.people_uncertain) == (2, 2)


def test_low_confidence_static_detection_is_placed_but_marked_uncorroborating():
    camera_pose = pose()
    frame = ground_frame((detection("d1", band="low", bbox=bbox_for((0.0, 200.0), camera_pose)),),
                         observation_id="obs-1", frame_id="frame-1", pose_id="p1",
                         pose=camera_pose, geometry=GEOMETRY)
    estimate, = frame.estimates
    assert frame.low_confidence_estimate_ids == (estimate.estimate_id,)


def test_unsupported_classes_are_ignored_not_counted():
    frame = ground_frame((detection("d1", "chair"),), observation_id="obs-1", frame_id="frame-1",
                         pose_id="p1", pose=pose(), geometry=GEOMETRY)
    assert frame.estimates == () and frame.unplaced == ()
    assert (frame.people_confirmed, frame.people_uncertain) == (0, 0)


def test_frame_evidence_round_trips_through_its_json_payload():
    camera_pose = pose()
    frame = ground_frame((detection("d1", bbox=bbox_for((0.0, 200.0), camera_pose)),
                          detection("d2", "phone", "low", bbox=bbox_for((60.0, 300.0), camera_pose)),
                          detection("d3", "person"), detection("d4", "computer", bbox=(465.0, 100.0, 30.0, 40.0))),
                         observation_id="obs-1", frame_id="frame-1", pose_id="p1",
                         pose=camera_pose, geometry=GEOMETRY)
    payload = json.loads(json.dumps(frame.to_payload()))
    assert FrameCountEvidence.from_payload(payload) == frame


# --- project_objective_count -------------------------------------------------

def _frame(observation_id, camera_pose, target_xy, *, band="high", class_name="computer"):
    return ground_frame((detection("d1", class_name, band, bbox_for(target_xy, camera_pose)),),
                        observation_id=observation_id, frame_id=f"frame-{observation_id}",
                        pose_id=observation_id, pose=camera_pose, geometry=GEOMETRY)


def test_one_object_seen_from_two_distinct_views_is_one_confirmed_object():
    first = _frame("a", pose(0.0, 0.0, captured_at="2026-09-23T00:00:00+00:00"), (40.0, 300.0))
    second = _frame("b", pose(80.0, 0.0, captured_at="2026-09-23T00:00:05+00:00"), (40.0, 300.0))
    projected = project_objective_count(count_objective(), (first, second), complete_coverage(), scope="ROOM")
    computer, = projected.counts
    assert (computer.confirmed, computer.uncertain) == (1, 0)
    text = render_objective_count(projected)
    assert text.splitlines()[0] == "Objective g1 (room-wide count):"
    assert "computer: 1 confirmed; 0 uncertain" in text
    assert "Coverage for the room is complete" in text


def test_low_confidence_sightings_never_confirm_an_object_even_from_two_views():
    first = _frame("a", pose(0.0, 0.0, captured_at="2026-09-23T00:00:00+00:00"), (40.0, 300.0), band="low")
    second = _frame("b", pose(80.0, 0.0, captured_at="2026-09-23T00:00:05+00:00"), (40.0, 300.0), band="low")
    projected = project_objective_count(count_objective(), (first, second), complete_coverage(), scope="ROOM")
    computer, = projected.counts
    assert (computer.confirmed, computer.uncertain) == (0, 1)


def test_unplaced_detections_are_reported_not_silently_dropped():
    frame = ground_frame((detection("d1", bbox=(465.0, 100.0, 30.0, 40.0)),), observation_id="a",
                         frame_id="frame-a", pose_id="a", pose=pose(), geometry=GEOMETRY)
    projected = project_objective_count(count_objective(), (frame,), complete_coverage(), scope="ROOM")
    computer, = projected.counts
    assert (computer.confirmed, computer.uncertain, computer.unplaced) == (0, 0, 1)
    assert "1 detection could not be placed in room coordinates and is not counted" in \
        render_objective_count(projected)


def test_projection_is_restricted_to_the_objective_classes_and_scope_wording():
    frame = ground_frame((detection("d1", "phone", bbox=bbox_for((0.0, 200.0), pose())),
                          detection("d2", "person")),
                         observation_id="a", frame_id="frame-a", pose_id="a", pose=pose(), geometry=GEOMETRY)
    projected = project_objective_count(count_objective(("computer",), "left/rear/bottom"), (frame,),
                                        project_coverage((), ()), scope="VIEWPOINT", pose_id="a")
    assert [c.class_name for c in projected.counts] == ["computer"]
    text = render_objective_count(projected)
    assert text.splitlines()[0] == "Objective g1 (count from declared viewpoint a):"
    assert "this viewpoint" in text and "the room" not in text
    assert "phone" not in text and "people" not in text


def test_people_count_is_a_lower_bound_from_the_best_single_frame():
    frames = tuple(
        ground_frame(tuple(detection(f"p{i}", "person", band) for i, band in enumerate(bands)),
                     observation_id=name, frame_id=f"frame-{name}", pose_id=name, pose=pose(), geometry=GEOMETRY)
        for name, bands in (("a", ("high",)), ("b", ("high", "high", "low")))
    )
    projected = project_objective_count(count_objective(("person",)), frames, complete_coverage(), scope="ROOM")
    person, = projected.counts
    assert (person.confirmed, person.uncertain, person.method) == (2, 1, "MAX_SIMULTANEOUS_LOWER_BOUND")
    assert "at least 2 people visible; 1 additional detection is uncertain" in render_objective_count(projected)


def test_objective_count_payload_is_json_and_names_its_scope_and_evidence():
    first = _frame("a", pose(0.0, 0.0, captured_at="2026-09-23T00:00:00+00:00"), (40.0, 300.0))
    projected = project_objective_count(count_objective(), (first,), complete_coverage(), scope="ROOM")
    payload = json.loads(json.dumps(projected.to_payload()))
    assert payload["objective_id"] == "g1" and payload["scope"] == "ROOM"
    assert payload["frame_ids"] == ["frame-a"]
    assert payload["counts"][0]["class_name"] == "computer"
    assert payload["coverage"]["complete"] is True


def test_count_geometry_rejects_person_and_unsupported_or_duplicate_classes():
    with pytest.raises(ValueError):
        CountGeometry(LEVEL, (("person", FLOOR),))
    with pytest.raises(ValueError):
        CountGeometry(LEVEL, (("chair", FLOOR),))
    with pytest.raises(ValueError):
        CountGeometry(LEVEL, (("phone", FLOOR), ("phone", FLOOR)))
