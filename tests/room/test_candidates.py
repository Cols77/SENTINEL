"""Legal actions shrink as reads/refusals consume unchanged evidence."""
from dataclasses import asdict, replace

import pytest

from sentinel.room.candidates import build_candidates, build_observation_candidates
from sentinel.room.coverage import CellObservation, CoverageCell, project_coverage
from sentinel.room.goals import GoalObjective
from sentinel.room.models import CandidateKind, GoalKind
from sentinel.room.queries import ReadFact, ReadQueries, ToolOutcome, relevant_hash
from tests.room.test_queries import append, drone, goal, room
from tests.room.test_coverage import POSE_A, POSE_B, RECT, _site


READS = {CandidateKind.READ_DRONE, CandidateKind.READ_OBSERVATIONS, CandidateKind.READ_COUNTS}
SAFE = READS | {CandidateKind.ANSWER, CandidateKind.CLARIFY, CandidateKind.ESCALATE}


@pytest.mark.parametrize("kind", list(GoalKind))
def test_plan01_never_offers_physical_candidates_even_for_parsed_goals(room, kind):
    goal(room, kind)
    snapshot = ReadQueries(room).project("s1")
    candidates = build_candidates(snapshot)
    assert candidates and {c.kind for c in candidates} <= SAFE
    assert all(c.snapshot_version == snapshot.state_version for c in candidates)
    assert all(c.physical_cost.distance_cm == 0 and not c.physical_cost.requires_airborne for c in candidates)
    assert CandidateKind.ANSWER not in {c.kind for c in candidates}


def test_read_repeats_only_after_its_evidence_changes(room):
    goal(room)
    drone(room)
    queries = ReadQueries(room)
    before = queries.project("s1")
    read = next(c for c in build_candidates(before) if c.kind == CandidateKind.READ_DRONE)
    append(room, "ROOM_TOOL_OUTCOME", {
        "candidate_id": read.candidate_id, "kind": read.kind,
        "relevant_state_sha256": read.relevant_state_sha256, "code": "READ_SUCCEEDED",
        "facts": [asdict(f) for f in queries.read(read.kind, before)],
    })
    candidates = build_candidates(queries.project("s1"))
    assert read.candidate_id not in {c.candidate_id for c in candidates}
    assert CandidateKind.ANSWER in {c.kind for c in candidates}
    append(room, "UNRELATED_EVENT", {})
    assert read.candidate_id not in {c.candidate_id for c in build_candidates(queries.project("s1"))}
    drone(room, 69)
    refreshed = build_candidates(queries.project("s1"))
    assert read.candidate_id in {c.candidate_id for c in refreshed}
    assert CandidateKind.ANSWER not in {c.kind for c in refreshed}


def test_refusal_suppression_survives_reprojection_and_requires_relevant_change(room):
    goal(room)
    queries = ReadQueries(room)
    selected = next(c for c in build_candidates(queries.project("s1")) if c.kind == CandidateKind.READ_DRONE)
    append(room, "ROOM_TOOL_OUTCOME", {
        "candidate_id": selected.candidate_id, "kind": selected.kind,
        "relevant_state_sha256": selected.relevant_state_sha256, "code": "LOW_CONFIDENCE",
        "refusal_code": "LOW_CONFIDENCE", "facts": [],
    })
    assert selected.candidate_id not in {c.candidate_id for c in build_candidates(queries.project("s1"))}
    drone(room)
    assert selected.candidate_id in {c.candidate_id for c in build_candidates(queries.project("s1"))}


def test_disabled_terminal_obligation_cannot_be_answered_as_complete(room):
    goal(room, GoalKind.COUNT, terminal="RETURN_HOME")
    assert CandidateKind.ANSWER not in {c.kind for c in build_candidates(ReadQueries(room).project("s1"))}


def test_observation_descriptions_name_new_cells_and_region_without_changing_ids():
    left = CoverageCell("table-left", RECT, (60, 140), ("phone", "computer"))
    right = CoverageCell("table-right", RECT, (60, 140), ("phone",))
    site = _site(coverage_cells=(left, right),
                 pose_visibility=((POSE_A, ("table-left", "table-right")),
                                  (POSE_B, ("table-right",))))
    coverage = project_coverage((left, right),
                                (CellObservation("table-left", "phone", POSE_A, "OBSERVED", "t0", ("ev-1",)),))
    objective = GoalObjective("g1", 1, GoalKind.COUNT, ("phone", "computer"),
                              None, None, "ROOM", "REPORTED", None, None, ())

    candidates = build_observation_candidates((objective,), site, coverage, (), 1)
    by_id = {candidate.candidate_id: candidate for candidate in candidates}

    assert set(by_id) == {f"OBSERVE:g1:{POSE_A}", f"OBSERVE:g1:{POSE_B}"}
    both = by_id[f"OBSERVE:g1:{POSE_A}"].description
    right_only = by_id[f"OBSERVE:g1:{POSE_B}"].description
    assert "2 unexplored cells" in both
    assert "table-left" in both and "table-right" in both
    assert "2 unresolved cell/class pairs" in both
    assert "1 unexplored cell" in right_only and "table-right" in right_only
    assert "table-left" not in right_only


def test_requested_region_description_identifies_coverage_it_adds():
    cell = CoverageCell("table-left", RECT, (60, 140), ("phone",))
    site = _site(coverage_cells=(cell,), pose_visibility=((POSE_B, ("table-left",)),))
    coverage = project_coverage((cell,), ())
    objective = GoalObjective("g1", 1, GoalKind.COUNT, ("phone",),
                              None, None, "left/front/bottom", "REPORTED", None, None, ())

    (candidate,) = build_observation_candidates((objective,), site, coverage, (), 1)

    assert candidate.candidate_id == f"OBSERVE:g1:{POSE_B}"
    assert "requested region" in candidate.description
    assert "1 unexplored cell" in candidate.description
    assert "table-left" in candidate.description


def test_observation_description_bounds_the_cell_sample():
    cells = tuple(CoverageCell(f"table-{index}", RECT, (60, 140), ("phone",)) for index in range(6))
    site = _site(coverage_cells=cells,
                 pose_visibility=((POSE_A, tuple(cell.cell_id for cell in cells)),))
    coverage = project_coverage(cells, ())
    objective = GoalObjective("g1", 1, GoalKind.COUNT, ("phone",),
                              None, None, "ROOM", "REPORTED", None, None, ())

    (candidate,) = build_observation_candidates((objective,), site, coverage, (), 1)

    assert "6 unexplored cells" in candidate.description
    assert "table-0" in candidate.description and "table-2" in candidate.description
    assert "+3 more" in candidate.description
    assert "table-5" not in candidate.description
    assert "6 unresolved cell/class pairs" in candidate.description


@pytest.mark.parametrize("refusal", ("LOW_CONFIDENCE", "THIN_MARGIN", "STALE_DECISION"))
def test_jev_decision_refusal_does_not_consume_unflown_observation(refusal):
    cell = CoverageCell("table-left", RECT, (60, 140), ("phone",))
    site = _site(coverage_cells=(cell,), pose_visibility=((POSE_A, ("table-left",)),))
    coverage = project_coverage((cell,), ())
    objective = GoalObjective("g1", 1, GoalKind.COUNT, ("phone",),
                              None, None, "ROOM", "REPORTED", None, None, ())
    (candidate,) = build_observation_candidates((objective,), site, coverage, (), 1)
    outcome = ToolOutcome(candidate.candidate_id, candidate.kind.value,
                          candidate.relevant_state_sha256, refusal, refusal)

    retry = build_observation_candidates((objective,), site, coverage, (outcome,), 2)

    assert [item.candidate_id for item in retry] == [candidate.candidate_id]


def test_tool_refusal_still_suppresses_same_observation():
    cell = CoverageCell("table-left", RECT, (60, 140), ("phone",))
    site = _site(coverage_cells=(cell,), pose_visibility=((POSE_A, ("table-left",)),))
    coverage = project_coverage((cell,), ())
    objective = GoalObjective("g1", 1, GoalKind.COUNT, ("phone",),
                              None, None, "ROOM", "REPORTED", None, None, ())
    (candidate,) = build_observation_candidates((objective,), site, coverage, (), 1)
    outcome = ToolOutcome(candidate.candidate_id, candidate.kind.value,
                          candidate.relevant_state_sha256, "TOOL_FAILURE", "TOOL_FAILURE")

    retry = build_observation_candidates((objective,), site, coverage, (outcome,), 2)

    assert not any(item.kind == CandidateKind.OBSERVE for item in retry)


@pytest.mark.parametrize("kind", (CandidateKind.READ_OBSERVATIONS, CandidateKind.READ_COUNTS))
def test_historical_reads_wait_while_observe_is_legal_until_new_source_evidence(room, kind):
    goal(room, GoalKind.COUNT)
    base = ReadQueries(room).project("s1")
    first_fact = ReadFact("fact-1", kind, (("class", "phone"),), "t1", "room", "historical", ("ev-1",))
    new_fact = ReadFact("fact-2", kind, (("class", "phone"),), "t2", "room", "historical", ("ev-2",))
    first_snapshot = replace(base, sources=(first_fact,))

    initial = {item.kind for item in build_candidates(first_snapshot, observation_legal=True)}

    assert kind not in initial
    assert CandidateKind.READ_DRONE in initial
    assert kind in {item.kind for item in build_candidates(first_snapshot)}

    read = ToolOutcome(kind.value, kind.value, relevant_hash(kind, first_snapshot),
                       "READ_SUCCEEDED", facts=(first_fact,))
    same_evidence = replace(first_snapshot, outcomes=(read,))
    new_evidence = replace(first_snapshot, sources=(first_fact, new_fact), outcomes=(read,))

    assert kind not in {item.kind for item in build_candidates(same_evidence, observation_legal=True)}
    assert kind in {item.kind for item in build_candidates(new_evidence, observation_legal=True)}


def test_observation_description_names_unresolved_classes_and_known_distance():
    cell = CoverageCell("table-left", RECT, (60, 140), ("phone", "computer"))
    site = _site(coverage_cells=(cell,), pose_visibility=((POSE_A, ("table-left",)),))
    coverage = project_coverage((cell,), ())
    objective = GoalObjective("g1", 1, GoalKind.COUNT, ("phone", "computer"),
                              None, None, "ROOM", "REPORTED", None, None, ())

    (candidate,) = build_observation_candidates((objective,), site, coverage, (), 1)

    assert "table-left:computer" in candidate.description
    assert "table-left:phone" in candidate.description
    assert "Approximate direct distance from nominal home:" in candidate.description
    assert "battery cost is assessed by the supervisor" in candidate.description
    assert "%" not in candidate.description


def test_search_description_treats_an_unvisited_view_as_progress_after_count_coverage():
    cell = CoverageCell("table", RECT, (60, 140), ("phone",))
    site = _site(coverage_cells=(cell,), pose_visibility=((POSE_A, ("table",)),))
    coverage = project_coverage((cell,),
                                (CellObservation("table", "phone", POSE_A, "OBSERVED", "t0", ("ev-1",)),))
    objective = GoalObjective("search-1", 1, GoalKind.FIND_OBSERVE, ("phone",),
                              None, None, "ROOM", "OBSERVED", None, None, ())

    (candidate,) = build_observation_candidates((objective,), site, coverage, (), 1)

    assert candidate.candidate_id == f"OBSERVE:search-1:{POSE_A}"
    assert "unvisited declared search viewpoint" in candidate.description
    assert "phone" in candidate.description
    assert "no declared unexplored cells" not in candidate.description
