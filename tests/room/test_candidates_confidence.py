"""Task 4b items 1-3: a confidence refusal never burns a region, OBSERVE
descriptions differ, and reads are hidden while an observation is legal."""
from __future__ import annotations

from sentinel.room.models import CandidateKind, TerminalState
from tests.room.test_controller import Judge
from tests.room.test_count_return import (SESSION, PerceptionRig, count_objective, drone, make_controller,
                                          make_result, room_site, set_ledger)
from tests.room.test_supervisor import safety  # noqa: F401  (fixture)

LEFT = "OBSERVE:g1:left-rear-bottom-toward_targets"
RIGHT = "OBSERVE:g1:right-front-top-toward_targets"


def unsure(label):
    """Jev leans to ``label`` at 0.4 with only half its mass on observing:
    refused LOW_CONFIDENCE even by the observe kind-mass gate."""
    def reply(state, criteria):
        rest = [key for key in criteria if key not in (label, LEFT, RIGHT)]
        others = [key for key in (LEFT, RIGHT) if key != label and key in criteria]
        probabilities = {label: .4, **{key: .1 for key in others}}
        share = (1 - sum(probabilities.values())) / len(rest)
        return {"value": label, "probabilities": {**probabilities, **{key: share for key in rest}}}
    return reply


def count_session(safety, tmp_path, *responses, reads=False):
    supervisor, store, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    if reads:
        drone(store, SESSION)
    rig = PerceptionRig(store, SESSION)
    for pose in ("left-rear-bottom-toward_targets", "right-front-top-toward_targets"):
        rig.set(pose, make_result(tmp_path, SESSION, pose))
    judge = Judge(*responses)
    controller, executor = make_controller(safety, judge, rig)
    return controller, judge, store


def legal(controller):
    return {c.candidate_id: c for c in controller._legal_candidates(controller.queries.project(SESSION))}


# --- item 1 -------------------------------------------------------------------

def test_a_low_confidence_observe_stays_offered_next_turn(safety, tmp_path):
    controller, judge, store = count_session(safety, tmp_path, unsure(LEFT), LEFT)
    first = controller.step(SESSION)
    assert not first.accepted and first.terminal_state is None
    assert store.events(SESSION)[-1].payload["refusal_code"] == "LOW_CONFIDENCE"
    assert LEFT in legal(controller)
    second = controller.step(SESSION)
    assert LEFT in judge.calls[1][1]
    assert second.accepted and second.candidate_id == LEFT
    assert store.events(SESSION)[-1].payload["code"] == "OBSERVE_SUCCEEDED"


def test_repeated_confidence_refusals_still_end_the_session(safety, tmp_path):
    """The persisted NO_PROGRESS rule still terminates a Jev that keeps
    refusing the same unchanged OBSERVE."""
    controller, judge, store = count_session(safety, tmp_path, unsure(LEFT), unsure(LEFT))
    controller.step(SESSION)
    second = controller.step(SESSION)
    assert second.terminal_state == TerminalState.ESCALATED
    assert store.events(SESSION)[-1].payload["code"] == "NO_PROGRESS"


def test_a_tool_failure_still_suppresses_the_observe(safety, tmp_path):
    controller, judge, store = count_session(safety, tmp_path, LEFT)
    controller.resolve_checkpoint = lambda candidate, snapshot: None
    assert not controller.step(SESSION).accepted
    assert store.events(SESSION)[-1].payload["refusal_code"] == "TOOL_FAILURE"
    assert LEFT not in legal(controller) and RIGHT in legal(controller)


def test_a_supervisor_refusal_is_not_a_confidence_refusal():
    from sentinel.room.candidates import CONFIDENCE_GATE_REFUSALS
    assert {"LOW_CONFIDENCE", "THIN_MARGIN", "MALFORMED_ANSWER", "ILLEGAL_OR_MALFORMED",
            "MISSING_OR_NON_MAX_SELECTION", "PROVIDER_ERROR"} == CONFIDENCE_GATE_REFUSALS
    for physical in ("TOOL_FAILURE", "CHECKPOINT_FAILED", "STALE_DECISION", "STALE_RESULT", "ADMISSION_REFUSED"):
        assert physical not in CONFIDENCE_GATE_REFUSALS


# --- item 2 -------------------------------------------------------------------

def test_observe_descriptions_name_the_cells_they_resolve_and_the_distance(safety, tmp_path):
    controller, *_ = count_session(safety, tmp_path)
    candidates = legal(controller)
    left, right = candidates[LEFT].description, candidates[RIGHT].description
    assert left != right
    assert "resolves 1 unexplored cell/class pair (cell1: computer)" in left
    assert "resolves 1 unexplored cell/class pair (cell2: computer)" in right
    for text in (left, right):
        assert text.startswith("Observe declared pose ")
        assert "Approximate direct distance from nominal home:" in text
        assert "battery cost is assessed by the supervisor" in text


# --- item 3 -------------------------------------------------------------------

def test_reads_are_hidden_while_an_observe_is_legal_and_come_back_after(safety, tmp_path):
    controller, judge, store = count_session(safety, tmp_path, LEFT, RIGHT, reads=True)
    kinds = {c.kind for c in legal(controller).values()}
    assert CandidateKind.OBSERVE in kinds and not kinds & {
        CandidateKind.READ_DRONE, CandidateKind.READ_OBSERVATIONS, CandidateKind.READ_COUNTS}
    assert controller.step(SESSION).accepted
    assert not any(key.startswith("READ_") for key in judge.calls[0][1])
    assert controller.step(SESSION).accepted
    after = legal(controller)
    assert CandidateKind.OBSERVE not in {c.kind for c in after.values()}
    # No OBSERVE left: the reads are back next to the count's answer route
    # (here the explicit incomplete answer: without count geometry every
    # detection stays unplaced), which never needed a read first.
    assert "READ_DRONE" in after
    assert any(c.kind == CandidateKind.ANSWER and c.candidate_id.startswith("ANSWER:") for c in after.values())


def test_reads_stay_when_observation_is_disabled(safety):
    supervisor, store, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    drone(store, SESSION)
    controller, _ = make_controller(safety, Judge())
    assert "READ_DRONE" in legal(controller)
