"""Legal actions shrink as reads/refusals consume unchanged evidence."""
from dataclasses import asdict

import pytest

from sentinel.room.candidates import build_candidates
from sentinel.room.models import CandidateKind, GoalKind
from sentinel.room.queries import ReadQueries
from tests.room.test_queries import append, drone, goal, room


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
