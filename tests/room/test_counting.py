"""Static-object association and grounded/lower-bound count projection.

Plan 03 Task 2. Association is a pure one-to-one matcher over class, support
plane, covariance overlap, time and multi-view geometry; people bypass it
entirely and are counted as the maximum simultaneously visible in one frame.
"""
from __future__ import annotations

import pytest

from sentinel.room.association import ObjectRevision, associate_static
from sentinel.room.coverage import CellObservation, CoverageCell, project_coverage
from sentinel.room.counting import ClassCount, FramePeople, project_counts, render_count_answer
from sentinel.room.spatial import SpatialEstimate, SupportKind


def _estimate(estimate_id, class_name="phone", x_cm=100.0, y_cm=100.0, *,
              support=SupportKind.FLOOR, support_plane_id="FLOOR", captured_at="t0",
              z_interval_cm=(0.0, 30.0), variance=400.0, view_id=None):
    return SpatialEstimate(estimate_id, f"det-{estimate_id}", class_name, x_cm, y_cm,
                           z_interval_cm, (variance, variance, 0.0), support, support_plane_id,
                           captured_at, "MEASURED", (f"det-{estimate_id}",), view_id)


def test_single_estimate_becomes_a_new_object_with_no_previous_state():
    result = associate_static((), (_estimate("e1"),))
    assert len(result) == 1
    obj = result[0]
    assert isinstance(obj, ObjectRevision)
    assert obj.revision == 1
    assert obj.member_estimate_ids == ("e1",)
    assert obj.class_name == "phone"


def test_far_apart_estimates_of_the_same_class_stay_distinct_objects():
    result = associate_static((), (_estimate("e1", x_cm=0.0, y_cm=0.0), _estimate("e2", x_cm=900.0, y_cm=900.0)))
    assert len(result) == 2
    assert len({obj.object_id for obj in result}) == 2
    assert {obj.member_estimate_ids for obj in result} == {("e1",), ("e2",)}


def test_close_estimates_from_different_frames_merge_into_one_object():
    result = associate_static((), (
        _estimate("e1", x_cm=100.0, y_cm=100.0, captured_at="t0"),
        _estimate("e2", x_cm=105.0, y_cm=102.0, captured_at="t1"),
    ))
    assert len(result) == 1
    assert set(result[0].member_estimate_ids) == {"e1", "e2"}
    assert result[0].revision == 1


def test_two_sightings_in_the_same_frame_are_always_two_objects():
    result = associate_static((), (
        _estimate("e1", x_cm=100.0, y_cm=100.0, captured_at="t0"),
        _estimate("e2", x_cm=101.0, y_cm=101.0, captured_at="t0"),
    ))
    assert len(result) == 2


def test_floor_phone_and_table_phone_never_merge_even_at_the_same_xy():
    floor = _estimate("e1", "phone", 100.0, 100.0, support=SupportKind.FLOOR, support_plane_id="FLOOR")
    table = _estimate("e2", "phone", 100.0, 100.0, support=SupportKind.DECLARED_HORIZONTAL_PLANE,
                      support_plane_id="table", z_interval_cm=(90.0, 120.0))
    result = associate_static((), (floor, table))
    assert len(result) == 2
    assert {obj.support_plane_id for obj in result} == {"FLOOR", "table"}


def test_object_identity_is_stable_across_a_later_revision():
    first = associate_static((), (_estimate("e1", x_cm=100.0, y_cm=100.0, captured_at="t0"),))
    second = associate_static(first, (_estimate("e2", x_cm=104.0, y_cm=101.0, captured_at="t1"),))
    assert len(second) == 1
    assert second[0].object_id == first[0].object_id
    assert second[0].revision == 2
    assert set(second[0].member_estimate_ids) == {"e1", "e2"}


def test_each_estimate_and_object_is_consumed_at_most_once():
    previous = associate_static((), (_estimate("e1", x_cm=100.0, y_cm=100.0, captured_at="t0"),))
    result = associate_static(previous, (
        _estimate("e2", x_cm=101.0, y_cm=101.0, captured_at="t1"),
        _estimate("e3", x_cm=650.0, y_cm=650.0, captured_at="t1"),
    ))
    # e2 is much closer to the existing object than e3 is; e3 must not be
    # silently absorbed into it just because both were offered this round.
    assert len(result) == 2
    updated = next(obj for obj in result if obj.object_id == previous[0].object_id)
    assert set(updated.member_estimate_ids) == {"e1", "e2"}
    fresh = next(obj for obj in result if obj.object_id != previous[0].object_id)
    assert fresh.member_estimate_ids == ("e3",)


def test_unsupported_class_is_rejected_at_construction():
    with pytest.raises(ValueError):
        ObjectRevision("obj-1", "spaceship", SupportKind.FLOOR, "FLOOR", 0.0, 0.0,
                       (0.0, 30.0), (400.0, 400.0, 0.0), 1, "t0", ("e1",), ("det-e1",))


def test_three_sightings_of_one_object_in_a_single_batch_form_one_group():
    # All three estimates are mutually close and at distinct capture times;
    # a group must be able to grow past a single pair in one call.
    result = associate_static((), (
        _estimate("e1", x_cm=100.0, y_cm=100.0, captured_at="t0"),
        _estimate("e2", x_cm=101.0, y_cm=101.0, captured_at="t1"),
        _estimate("e3", x_cm=102.0, y_cm=99.0, captured_at="t2"),
    ))
    assert len(result) == 1
    assert set(result[0].member_estimate_ids) == {"e1", "e2", "e3"}


def test_a_new_pair_does_not_steal_estimates_from_a_compatible_existing_object():
    previous = associate_static((), (_estimate("e0", x_cm=100.0, y_cm=100.0, captured_at="t0"),))
    result = associate_static(previous, (
        _estimate("e2", x_cm=110.0, y_cm=100.0, captured_at="t1"),
        _estimate("e3", x_cm=110.0, y_cm=101.0, captured_at="t2"),
    ))
    assert len(result) == 1
    updated = result[0]
    assert updated.object_id == previous[0].object_id
    assert set(updated.member_estimate_ids) == {"e0", "e2", "e3"}


def test_same_frame_guard_checks_every_member_not_just_the_latest():
    # obj has members a@t0 and b@t1 (captured_at displays "t1"); a third
    # sighting at t0 from a different real object must still be rejected,
    # not merged just because it does not collide with the *latest* member.
    previous = associate_static((), (_estimate("a", x_cm=100.0, y_cm=100.0, captured_at="t0"),))
    previous = associate_static(previous, (_estimate("b", x_cm=101.0, y_cm=100.0, captured_at="t1"),))
    assert previous[0].captured_at == "t1"
    result = associate_static(previous, (_estimate("c", x_cm=105.0, y_cm=100.0, captured_at="t0"),))
    assert len(result) == 2
    updated = next(obj for obj in result if obj.object_id == previous[0].object_id)
    assert set(updated.member_estimate_ids) == {"a", "b"}
    fresh = next(obj for obj in result if obj.object_id != previous[0].object_id)
    assert fresh.member_estimate_ids == ("c",)


def test_replaying_the_same_estimate_is_idempotent():
    first = associate_static((), (_estimate("e1", x_cm=100.0, y_cm=100.0, captured_at="t0"),))
    replayed = associate_static(first, (_estimate("e1", x_cm=100.0, y_cm=100.0, captured_at="t0"),))
    assert replayed == first


def test_confirmed_requires_a_distinct_view_not_merely_a_second_sighting():
    # Two captures from one unmoved hover pose (same view_id) must not
    # promote an object to "confirmed" -- confirmation needs a second
    # genuinely distinct viewpoint.
    same_pose = associate_static((), (
        _estimate("e1", x_cm=100.0, y_cm=100.0, captured_at="t0", view_id="pose-A"),
        _estimate("e2", x_cm=101.0, y_cm=100.0, captured_at="t1", view_id="pose-A"),
    ))
    coverage = project_coverage((_PHONE_CELL,), (CellObservation("cell-1", "phone", "p1", "OBSERVED", "t0", ("e",)),))
    counts = project_counts(same_pose, (), coverage)
    phone = next(c for c in counts if c.class_name == "phone")
    assert phone.confirmed == 0
    assert phone.uncertain == 1

    different_pose = associate_static((), (
        _estimate("e3", x_cm=100.0, y_cm=100.0, captured_at="t0", view_id="pose-A"),
        _estimate("e4", x_cm=101.0, y_cm=100.0, captured_at="t1", view_id="pose-B"),
    ))
    counts = project_counts(different_pose, (), coverage)
    phone = next(c for c in counts if c.class_name == "phone")
    assert phone.confirmed == 1
    assert phone.uncertain == 0


# --- counting -----------------------------------------------------------

_PHONE_CELL = CoverageCell("cell-1", ((0.0, 0.0), (200.0, 0.0), (200.0, 200.0), (0.0, 200.0)),
                          (0.0, 150.0), ("phone",))


def test_empty_db_reports_zero_not_not_observable():
    coverage = project_coverage((_PHONE_CELL,), ())
    counts = project_counts((), (), coverage)
    phone = next(c for c in counts if c.class_name == "phone")
    assert phone.confirmed == 0 and phone.uncertain == 0
    assert phone.method == "GROUNDED_STATIC"


def test_not_resolvable_coverage_marks_the_class_not_observable():
    observation = CellObservation("cell-1", "phone", "p1", "NOT_RESOLVABLE", "t0", ("ev-nr",))
    coverage = project_coverage((_PHONE_CELL,), (observation,))
    counts = project_counts((), (), coverage)
    phone = next(c for c in counts if c.class_name == "phone")
    assert phone.method == "NOT_OBSERVABLE"
    assert phone.confirmed == 0 and phone.uncertain == 0


def test_multi_view_objects_are_confirmed_single_view_objects_are_uncertain():
    objects = associate_static((), (
        _estimate("e1", x_cm=100.0, y_cm=100.0, captured_at="t0"),
        _estimate("e2", x_cm=104.0, y_cm=101.0, captured_at="t1"),
        _estimate("e3", x_cm=900.0, y_cm=900.0, captured_at="t0"),
    ))
    coverage = project_coverage((_PHONE_CELL,), (CellObservation("cell-1", "phone", "p1", "OBSERVED", "t0", ("e1",)),))
    counts = project_counts(objects, (), coverage)
    phone = next(c for c in counts if c.class_name == "phone")
    assert phone.confirmed == 1  # the two-estimate merged object
    assert phone.uncertain == 1  # the lone singleton
    assert phone.method == "GROUNDED_STATIC"


def test_people_bypass_association_and_use_the_max_simultaneous_frame():
    people = (
        FramePeople("frame-1", "pose-a", "t0", confirmed_count=1, uncertain_count=0),
        FramePeople("frame-2", "pose-b", "t1", confirmed_count=2, uncertain_count=1),
    )
    coverage = project_coverage((), ())
    counts = project_counts((), people, coverage)
    person = next(c for c in counts if c.class_name == "person")
    assert person.confirmed == 2
    assert person.uncertain == 1
    assert person.method == "MAX_SIMULTANEOUS_LOWER_BOUND"


def test_pinned_honest_people_wording_is_never_reported_as_an_exact_count():
    people = (FramePeople("frame-2", "pose-b", "t1", confirmed_count=2, uncertain_count=1),)
    coverage = project_coverage((), ())
    counts = project_counts((), people, coverage)
    text = render_count_answer(counts, coverage, scope="ROOM")
    assert "at least 2 people visible; 1 additional detection is uncertain" in text


def test_zero_confirmed_with_incomplete_coverage_never_reads_as_none_in_the_room():
    coverage = project_coverage((_PHONE_CELL,), ())
    counts = project_counts((), (), coverage)
    text = render_count_answer(counts, coverage, scope="ROOM")
    assert "does not mean none are present" in text


def test_rendered_answer_states_latest_capture_and_analysed_frames():
    observation = CellObservation("cell-1", "phone", "p1", "OBSERVED", "2026-09-23T00:10:00+00:00", ("ev-1",))
    coverage = project_coverage((_PHONE_CELL,), (observation,))
    counts = project_counts((), (), coverage)
    text = render_count_answer(counts, coverage, scope="ROOM")
    assert "latest capture" in text.lower()
    assert "analysed" in text.lower() and "unanalysed" in text.lower()
    assert "2026-09-23t00:10:00+00:00" in text.lower()


def test_requested_class_with_no_cells_and_no_objects_still_gets_a_line():
    # A class nobody has declared a coverage cell for, and nothing has ever
    # been associated for, must not silently have no line at all -- it must
    # render as an honest zero, distinct from "not observable".
    coverage = project_coverage((), ())
    counts = project_counts((), (), coverage, requested_classes=("phone",))
    phone = next(c for c in counts if c.class_name == "phone")
    assert phone.confirmed == 0 and phone.uncertain == 0
    assert phone.method == "GROUNDED_STATIC"
    text = render_count_answer(counts, coverage, scope="ROOM")
    assert "phone" in text
