"""Bounded coverage cells: per-(cell, class) status, canonical ordering,
immutability against source mutation, and honest incompleteness.

Plan 03 Task 2.
"""
from __future__ import annotations

import pytest

from sentinel.cube.checkpoint import HomeDefinition
from sentinel.models import Position
from sentinel.room.candidates import build_observation_candidates
from sentinel.room.coverage import (
    CellObservation,
    CoverageCell,
    CoverageProjection,
    digest_coverage,
    project_coverage,
)
from sentinel.room.goals import GoalObjective
from sentinel.room.models import CandidateKind, GoalKind
from sentinel.room.queries import ToolOutcome
from sentinel.room.semantic import SemanticPose, resolve_semantic_pose
from sentinel.room.site import (
    ContingencyKind,
    ContingencyTrigger,
    RoomSite,
    RouteDefinition,
    SemanticFrame,
    TerminalContingency,
    Volume3D,
)


def _cell(cell_id="cell-1", required_classes=("phone",)):
    return CoverageCell(cell_id, ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)),
                        (0.0, 150.0), required_classes)


def _observed(cell_id="cell-1", class_name="phone", pose_id="p1", evidence=("ev-1",)):
    return CellObservation(cell_id, class_name, pose_id, "OBSERVED", "2026-09-23T00:00:00+00:00", evidence)


def test_coverage_cell_rejects_unsupported_classes():
    with pytest.raises(ValueError):
        _cell(required_classes=("spaceship",))


def test_coverage_cell_rejects_degenerate_polygon_or_band():
    with pytest.raises(ValueError):
        CoverageCell("c", ((0.0, 0.0), (1.0, 0.0)), (0.0, 150.0), ("phone",))
    with pytest.raises(ValueError):
        CoverageCell("c", ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0)), (150.0, 0.0), ("phone",))


def test_empty_db_yields_a_vacuously_complete_projection():
    projection = project_coverage((), ())
    assert projection == CoverageProjection((), True, (), ())


def test_unvisited_cell_is_incomplete_not_a_silent_zero():
    projection = project_coverage((_cell(),), ())
    assert projection.by_cell_and_class == (("cell-1", "phone", "UNVISITED"),)
    assert projection.complete is False
    assert projection.uncovered == ("cell-1:phone",)


def test_observed_cell_resolves_and_completes():
    projection = project_coverage((_cell(),), (_observed(),))
    assert projection.by_cell_and_class == (("cell-1", "phone", "OBSERVED"),)
    assert projection.complete is True
    assert projection.uncovered == ()
    assert projection.evidence_refs == ("ev-1",)


def test_not_resolvable_class_is_terminal_and_counts_as_complete():
    observation = CellObservation("cell-1", "phone", "p1", "NOT_RESOLVABLE", "2026-09-23T00:00:00+00:00", ())
    projection = project_coverage((_cell(),), (observation,))
    assert projection.by_cell_and_class == (("cell-1", "phone", "NOT_RESOLVABLE"),)
    assert projection.complete is True


def test_unanalysed_frame_is_incomplete_not_observed():
    observation = CellObservation("cell-1", "phone", "p1", "UNANALYSED", "2026-09-23T00:00:00+00:00", ())
    projection = project_coverage((_cell(),), (observation,))
    assert projection.by_cell_and_class[0][2] == "UNANALYSED"
    assert projection.complete is False


def test_occluded_requires_positive_evidence_otherwise_defaults_to_unvisited():
    unevidenced = CellObservation("cell-1", "phone", "p1", "OCCLUDED", "2026-09-23T00:00:00+00:00", ())
    projection = project_coverage((_cell(),), (unevidenced,))
    assert projection.by_cell_and_class[0][2] == "UNVISITED"

    evidenced = CellObservation("cell-1", "phone", "p1", "OCCLUDED", "2026-09-23T00:00:00+00:00", ("ev-2",))
    projection = project_coverage((_cell(),), (evidenced,))
    assert projection.by_cell_and_class[0][2] == "OCCLUDED"
    assert projection.complete is False


def test_duplicate_sightings_of_the_same_cell_and_class_do_not_double_count():
    projection = project_coverage((_cell(),), (_observed(evidence=("ev-1",)), _observed(evidence=("ev-2",))))
    assert projection.by_cell_and_class == (("cell-1", "phone", "OBSERVED"),)
    assert projection.evidence_refs == ("ev-1", "ev-2")


def test_observed_beats_a_stale_occluded_claim_for_the_same_pair():
    occluded = CellObservation("cell-1", "phone", "p1", "OCCLUDED", "t0", ("ev-occ",))
    observed = _observed(evidence=("ev-obs",))
    projection = project_coverage((_cell(),), (occluded, observed))
    assert projection.by_cell_and_class[0][2] == "OBSERVED"


def test_missing_frame_or_rejected_detection_leaves_the_pair_unvisited():
    # A rejected detection or a frame that never arrived contributes no
    # CellObservation at all; the pair must not silently read as resolved.
    cells = (_cell("cell-1", ("phone",)), _cell("cell-2", ("computer",)))
    projection = project_coverage(cells, (_observed("cell-1"),))
    statuses = {(cell_id, class_name): status for cell_id, class_name, status in projection.by_cell_and_class}
    assert statuses[("cell-1", "phone")] == "OBSERVED"
    assert statuses[("cell-2", "computer")] == "UNVISITED"
    assert projection.complete is False
    assert projection.uncovered == ("cell-2:computer",)


def test_by_cell_and_class_is_sorted_by_cell_id_then_class_name():
    cells = (_cell("cell-b", ("phone", "computer")), _cell("cell-a", ("pen",)))
    projection = project_coverage(cells, ())
    pairs = [(c, k) for c, k, _ in projection.by_cell_and_class]
    assert pairs == sorted(pairs)
    assert pairs == [("cell-a", "pen"), ("cell-b", "computer"), ("cell-b", "phone")]


def test_mutating_source_containers_after_projection_cannot_change_it_or_its_digest():
    cells_list = [_cell()]
    observations_list = [_observed()]
    projection = project_coverage(cells_list, observations_list)
    digest_before = digest_coverage(projection)
    cells_list.append(_cell("cell-2", ("computer",)))
    observations_list.append(_observed("cell-2", "computer"))
    observations_list.clear()
    assert projection.by_cell_and_class == (("cell-1", "phone", "OBSERVED"),)
    assert digest_coverage(projection) == digest_before


def test_projection_is_a_plain_immutable_value():
    projection = project_coverage((_cell(),), (_observed(),))
    with pytest.raises(AttributeError):
        projection.complete = False


def test_identical_inputs_produce_identical_digests_and_different_state_differs():
    a = project_coverage((_cell(),), (_observed(),))
    b = project_coverage((_cell(),), (_observed(),))
    assert digest_coverage(a) == digest_coverage(b)
    c = project_coverage((_cell(),), ())
    assert digest_coverage(a) != digest_coverage(c)


def test_viewpoint_scoped_and_room_scoped_incomplete_wording_differ():
    from sentinel.room.coverage import render_coverage_note

    projection = project_coverage((_cell(),), ())
    room_text = render_coverage_note(projection, scope="ROOM")
    viewpoint_text = render_coverage_note(projection, scope="VIEWPOINT")
    assert "room" in room_text.lower()
    assert "viewpoint" in viewpoint_text.lower()
    assert room_text != viewpoint_text
    assert "cell-1:phone" in room_text and "cell-1:phone" in viewpoint_text


def test_complete_coverage_note_says_so_for_either_scope():
    from sentinel.room.coverage import render_coverage_note

    projection = project_coverage((_cell(),), (_observed(),))
    assert "complete" in render_coverage_note(projection, scope="ROOM").lower()
    assert "complete" in render_coverage_note(projection, scope="VIEWPOINT").lower()


# --- OBSERVE candidate generation ---------------------------------------
#
# Ordinary OBSERVE:<objective-id>:<semantic-pose-id> generation, tested apart
# from closer inspection (Plan 03 Task 3, operator-deferred: see the task
# report). None of this ever produces a closer-inspection candidate --
# CandidateKind has no such member yet, so that separation holds by
# construction, and the assertions below pin it explicitly regardless.

RECT = ((150, 120), (350, 120), (350, 260), (150, 260))
ROOM_CELL = CoverageCell("room-floor", RECT, (60, 140), ("phone", "computer"))
POSE_A = "center-middle-middle-toward_targets"
POSE_B = "left-front-bottom-toward_targets"
POSE_C = "right-rear-top-toward_targets"


def _terminal_contingencies():
    return tuple(TerminalContingency(trigger.value, trigger, ContingencyKind.LAND_IN_PLACE,
        RECT, (60, 140), "COASTING", "survey", "checked-clear", 5000) for trigger in ContingencyTrigger)


def _site(*, coverage_cells=(), pose_visibility=()):
    return RoomSite(
        "room", "a" * 64, "b" * 64,
        (Volume3D("room", RECT, (60, 140)),), (),
        (Volume3D("corridor", RECT, (60, 140)),),
        (RouteDefinition("home-route", (Position(250, 140, 90),), ("corridor",)),),
        HomeDefinition("pad", Position(250, 140, 90), 90, "home-route"),
        _terminal_contingencies(), "survey-revision-1",
        semantic_frame=SemanticFrame("room", (1.0, 0.0), (0.0, 1.0), "FRONT", ()),
        coverage_cells=coverage_cells, pose_visibility=pose_visibility,
    )


def _objective(objective_id, ordinal, scope, terminal="REPORTED"):
    return GoalObjective(objective_id, ordinal, GoalKind.COUNT, ("phone", "computer"),
                        None, None, scope, terminal, None, None, ())


def _pose_id_of(scope: str) -> str:
    horizontal, depth, height = scope.split("/")
    return resolve_semantic_pose(_site(), SemanticPose(horizontal, depth, height)).pose_id


def test_region_pose_id_format_matches_semantic_pose_resolution():
    # candidates.py derives OBSERVE pose IDs from spatial_scope without
    # calling into semantic.py; this pins that derivation to the real one.
    assert _pose_id_of("left/front/bottom") == "left-front-bottom-toward_targets"


def test_region_objectives_are_generated_first_in_original_ordinal_order():
    objectives = (
        _objective("g2", 2, "left/front/bottom"),
        _objective("g1", 1, "right/rear/top"),
    )
    site = _site()
    coverage = project_coverage((), ())
    result = build_observation_candidates(objectives, site, coverage, (), 1)
    assert [c.kind for c in result] == [CandidateKind.OBSERVE, CandidateKind.OBSERVE]
    assert result[0].candidate_id == f"OBSERVE:g1:{_pose_id_of('right/rear/top')}"
    assert result[1].candidate_id == f"OBSERVE:g2:{_pose_id_of('left/front/bottom')}"


def test_room_scope_offers_only_legal_declared_views_that_add_coverage():
    site = _site(coverage_cells=(ROOM_CELL,),
                 pose_visibility=((POSE_A, ("room-floor",)), (POSE_B, ("room-floor",)), (POSE_C, ("room-floor",))))
    coverage = project_coverage((ROOM_CELL,), ())
    objective = _objective("g1", 1, "ROOM")
    result = build_observation_candidates((objective,), site, coverage, (), 1)
    assert {c.kind for c in result} == {CandidateKind.OBSERVE}
    assert {c.candidate_id for c in result} == {f"OBSERVE:g1:{pose}" for pose in (POSE_A, POSE_B, POSE_C)}
    # Deterministic order: descending benefit, then physical cost, then ID.
    # All three see the same single cell, so this is cost-then-ID.
    home = Position(250, 140, 90)

    def distance(pose_id):
        h, d, z, _look = pose_id.split("-")
        resolved = resolve_semantic_pose(site, SemanticPose(h, d, z))
        return ((resolved.x_cm - home.x_cm) ** 2 + (resolved.y_cm - home.y_cm) ** 2) ** 0.5

    expected = sorted((POSE_A, POSE_B, POSE_C), key=lambda pose: (distance(pose), pose))
    assert [c.candidate_id.split(":")[-1] for c in result] == expected


def test_room_scope_skips_a_view_that_adds_no_uncovered_coverage():
    site = _site(coverage_cells=(ROOM_CELL,), pose_visibility=((POSE_A, ("room-floor",)),))
    observations = (
        CellObservation("room-floor", "phone", POSE_A, "OBSERVED", "t0", ("ev-1",)),
        CellObservation("room-floor", "computer", POSE_A, "OBSERVED", "t0", ("ev-2",)),
    )
    coverage = project_coverage((ROOM_CELL,), observations)
    objective = _objective("g1", 1, "ROOM")
    result = build_observation_candidates((objective,), site, coverage, (), 1)
    assert result == ()


def test_room_scope_suppresses_a_view_after_a_typed_refusal():
    site = _site(coverage_cells=(ROOM_CELL,), pose_visibility=((POSE_A, ("room-floor",)),))
    coverage = project_coverage((ROOM_CELL,), ())
    objective = _objective("g1", 1, "ROOM")
    first_pass = build_observation_candidates((objective,), site, coverage, (), 1)
    assert len(first_pass) == 1
    candidate = first_pass[0]
    refusal = ToolOutcome(candidate.candidate_id, candidate.kind.value, candidate.relevant_state_sha256,
                          "UNSAFE", "UNSAFE_TO_FLY", (), None, "evt-1")
    suppressed = build_observation_candidates((objective,), site, coverage, (refusal,), 1)
    # The refused view is gone; since it was the only one, coverage can never
    # improve, so this surfaces the explicit incomplete answer, not nothing.
    assert candidate.candidate_id not in {c.candidate_id for c in suppressed}
    assert len(suppressed) == 1
    assert suppressed[0].kind == CandidateKind.ANSWER


def test_room_scope_produces_an_explicit_incomplete_answer_instead_of_looping():
    # No declared pose sees "room-floor" at all: coverage can never improve,
    # so no OBSERVE candidate exists, and this must not spin forever -- it
    # must surface one explicit, honest incomplete answer instead.
    site = _site(coverage_cells=(ROOM_CELL,), pose_visibility=())
    coverage = project_coverage((ROOM_CELL,), ())
    objective = _objective("g1", 1, "ROOM")
    result = build_observation_candidates((objective,), site, coverage, (), 1)
    assert len(result) == 1
    assert result[0].kind == CandidateKind.ANSWER
    assert "incomplete" in result[0].description.lower()
    assert result[0].evidence_refs == coverage.evidence_refs


def test_complete_room_scope_needs_no_fallback_answer():
    site = _site(coverage_cells=(ROOM_CELL,), pose_visibility=())
    observations = (
        CellObservation("room-floor", "phone", "p1", "OBSERVED", "t0", ("ev-1",)),
        CellObservation("room-floor", "computer", "p1", "OBSERVED", "t0", ("ev-2",)),
    )
    coverage = project_coverage((ROOM_CELL,), observations)
    objective = _objective("g1", 1, "ROOM")
    result = build_observation_candidates((objective,), site, coverage, (), 1)
    assert result == ()

