"""Deterministic legal-action state machine: DecisionState -> gateway-valid candidates."""
from __future__ import annotations

import json

import pytest

from sentinel.coordinator.agent_loop import AgentLoop
from sentinel.coordinator.decision_state import DecisionState, read_decision_state
from sentinel.coordinator.legal_tools import (
    ADVISORY_POINTS,
    ASSIGN,
    ESCALATE_LABEL,
    EVIDENCE_GATE,
    EVIDENCE_TASK_SCORE,
    EVIDENCE_VETO,
    JUDGMENT_POINTS,
    RANK,
    SELECT_PLAN,
    WAIT_APPROVAL,
    WAIT_OBSERVATION,
    load_catalogue_plans,
    legal_set,
)
from sentinel.contracts import AgentModelResult

from .harness import (
    AGENT_RUN_ID,
    RUN_ID,
    CoordinatorBoundary,
    FakeWorker,
    close,
    envelope,
    open_harness,
    perception_result as harness_perception_result,
)


def _plan_reference(repo):
    plan = repo.connection.execute("SELECT * FROM plans").fetchone()
    return {
        "plan_id": plan["plan_id"], "catalogue_id": plan["catalogue_id"],
        "catalogue_revision": plan["catalogue_revision"],
        "plan_sha256": plan["plan_sha256"],
        "station_ids": json.loads(plan["station_ids_json"]),
    }


def _agent_result_for(agent_run_id, candidate):
    """Build and wire-validate an agent_result carrying exactly this candidate's tool call."""
    document = {
        "contract_version": "sentinel.v0", "wire_type": "agent_result",
        "agent_run_id": agent_run_id, "status": "TOOL_CALLS",
        "tool_calls": [{"contract_version": "sentinel.v0", "wire_type": "agent_tool_call",
                         "tool_name": candidate.tool_name, "arguments": candidate.arguments}],
    }
    AgentModelResult.from_dict(document)  # raises if the candidate's own wire shape is invalid
    return document


def _run_candidate(boundary, candidate, *, max_turns=1):
    """Execute the candidate through AgentLoop with a one-shot policy; proves gateway-validity."""
    def _model(request):
        return _agent_result_for(request["agent_run_id"], candidate)

    return AgentLoop(boundary, _model, max_turns=max_turns).run(RUN_ID, AGENT_RUN_ID, "inspect one station")


def test_load_catalogue_plans_describes_both_fixture_plans():
    plans = load_catalogue_plans()
    by_id = {p.plan_id: p for p in plans}
    assert set(by_id) == {"PLAN_MARKER_VERIFIED_6", "PLAN_ROUTE_ONLY_2_ZONE"}
    marker = by_id["PLAN_MARKER_VERIFIED_6"].description
    route = by_id["PLAN_ROUTE_ONLY_2_ZONE"].description

    assert "schedules visits to exactly 6 stations" in marker
    assert "schedules a marker-verification step at every listed station" in marker
    assert "marker verification=required" not in marker
    assert "schedules visits to exactly 2 stations" in route
    assert "schedules no marker-verification step" in route
    assert "marker verification=not required" not in route


def test_legal_set_walks_one_station_flow_with_gateway_valid_candidates(tmp_path):
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"}, sequence=1, actor_type="OPERATOR"))
        boundary = CoordinatorBoundary(repo, gateway)
        catalogue = load_catalogue_plans()

        # SELECT_PLAN: multiple candidates (one per catalogue plan, plus escalate).
        state = read_decision_state(repo, RUN_ID)
        legal = legal_set(state, catalogue)
        assert legal.decision_point == SELECT_PLAN
        assert len(legal.candidates) == len(catalogue) + 1
        chosen = legal.by_label("PLAN_ROUTE_ONLY_2_ZONE")
        _agent_result_for(AGENT_RUN_ID, chosen)  # gateway-valid wire shape
        _run_candidate(boundary, chosen)

        # WAIT_APPROVAL: exactly one candidate.
        state = read_decision_state(repo, RUN_ID)
        legal = legal_set(state, catalogue)
        assert legal.decision_point == WAIT_APPROVAL
        assert len(legal.candidates) == 1
        _agent_result_for(AGENT_RUN_ID, legal.candidates[0])

        reference = _plan_reference(repo)
        gateway.submit(envelope("OPERATOR_DECIDE", {
            "decision": "CONFIRM", "target": {"kind": "PLAN", "plan_reference": reference},
        }, sequence=10, actor_type="OPERATOR", expected=repo.read_state(RUN_ID).to_dict()["state_version"]))

        # WAIT_OBSERVATION: exactly one candidate.
        state = read_decision_state(repo, RUN_ID)
        legal = legal_set(state, catalogue)
        assert legal.decision_point == WAIT_OBSERVATION
        assert len(legal.candidates) == 1
        _agent_result_for(AGENT_RUN_ID, legal.candidates[0])

        worker = FakeWorker(gateway)
        worker.preflight(RUN_ID, reference["plan_id"], reference["plan_sha256"])
        worker.dispatch(RUN_ID, reference["plan_id"], "LEFT_ALLEY_MIDDLE", repo.read_state(RUN_ID).to_dict()["state_version"])
        worker.capture_and_perceive(RUN_ID, reference["plan_id"], reference["plan_sha256"])

        # EVIDENCE_GATE: create_task + escalate. Default fixture perception is
        # raw_score=0.99 at LEFT_ALLEY_MIDDLE, which is inside the approved
        # 2-zone plan -- Task A's rule_choice is "create_task".
        state = read_decision_state(repo, RUN_ID)
        legal = legal_set(state, catalogue)
        assert legal.decision_point == EVIDENCE_GATE
        assert {c.label for c in legal.candidates} == {"create_task", "escalate"}
        assert legal.rule_choice == "create_task"
        create = legal.by_label("create_task")
        _run_candidate(boundary, create)

        # RANK: exactly one candidate.
        state = read_decision_state(repo, RUN_ID)
        legal = legal_set(state, catalogue)
        assert legal.decision_point == RANK
        assert len(legal.candidates) == 1
        _run_candidate(boundary, legal.candidates[0])

        # ASSIGN: exactly one candidate.
        state = read_decision_state(repo, RUN_ID)
        legal = legal_set(state, catalogue)
        assert legal.decision_point == ASSIGN
        assert len(legal.candidates) == 1
        assign = legal.candidates[0]
        assert assign.tool_name == "assignment.propose"
        _run_candidate(boundary, assign)

        # WAIT_APPROVAL again: exactly one candidate.
        state = read_decision_state(repo, RUN_ID)
        legal = legal_set(state, catalogue)
        assert legal.decision_point == WAIT_APPROVAL
        assert len(legal.candidates) == 1
        _agent_result_for(AGENT_RUN_ID, legal.candidates[0])
    finally:
        close(repo)


def test_legal_set_evidence_veto_below_threshold(tmp_path):
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"}, sequence=1, actor_type="OPERATOR"))
        catalogue = load_catalogue_plans()

        command = envelope("AGENT_INTENT_PROPOSE", {"intent": "REQUEST_OPERATOR_APPROVAL",
                                                      "catalogue_plan_id": "PLAN_ROUTE_ONLY_2_ZONE"},
                            sequence=2, actor_type="AGENT")
        gateway.submit(command)
        reference = _plan_reference(repo)
        gateway.submit(envelope("OPERATOR_DECIDE", {
            "decision": "CONFIRM", "target": {"kind": "PLAN", "plan_reference": reference},
        }, sequence=10, actor_type="OPERATOR", expected=repo.read_state(RUN_ID).to_dict()["state_version"]))

        worker = FakeWorker(gateway)
        worker.preflight(RUN_ID, reference["plan_id"], reference["plan_sha256"])
        worker.dispatch(RUN_ID, reference["plan_id"], "LEFT_ALLEY_MIDDLE", repo.read_state(RUN_ID).to_dict()["state_version"])

        low_score = dict(harness_perception_result(reference["plan_id"], reference["plan_sha256"]))
        low_score["raw_score"] = 0.3
        gateway.submit(envelope("PERCEPTION_INGEST", {"perception_result": low_score},
                                 sequence=21, actor_type="WORKER"))

        state = read_decision_state(repo, RUN_ID)
        legal = legal_set(state, catalogue)
        assert legal.decision_point == EVIDENCE_VETO
        assert len(legal.candidates) == 1
        assert legal.candidates[0].label == "escalate"
        _agent_result_for(AGENT_RUN_ID, legal.candidates[0])
    finally:
        close(repo)


# --- Task A: EVIDENCE_GATE rule_choice (operator policy 2026-09-20) --------

def _evidence_gate_state(raw_score: float, station_id: str, *,
                          plan_station_ids: tuple[str, ...] = ("LEFT_ALLEY_MIDDLE", "RIGHT_ALLEY_MIDDLE")) -> DecisionState:
    """A minimal DecisionState sitting at EVIDENCE_GATE: a plan already
    exists and approved, a DETECTED perception is on file, no task yet."""
    return DecisionState(
        run_id="RUN", state_version=1, run_status="AGENT_TURN", agent_phase="TASK",
        emergency_latched=False, request_text="inspect one station",
        plan_id="p1", plan_mode="ROUTE_ONLY_2_ZONE", plan_station_ids=plan_station_ids,
        plan_approved=True,
        detection={"result_id": "r1", "artifact_id": "a1", "station_id": station_id,
                   "status": "DETECTED", "raw_score": raw_score, "canonical_class": "rock"},
        open_task_station_ids=(), task_id=None, task_status=None,
    )


def test_evidence_task_score_is_075_and_distinct_from_the_static_veto_threshold():
    from sentinel.coordinator.command_gateway import EVIDENCE_SCORE_THRESHOLD
    assert EVIDENCE_TASK_SCORE == 0.75
    assert EVIDENCE_TASK_SCORE != EVIDENCE_SCORE_THRESHOLD


def test_judgment_points_is_select_plan_only_and_advisory_points_is_the_superset():
    # JUDGMENT_POINTS is pinned exactly: widening it adds operator actions and
    # would break the operator's "approvals only" load target (2026-09-20).
    assert JUDGMENT_POINTS == frozenset({SELECT_PLAN})
    # ADVISORY_POINTS is only pinned as a superset, as this test's name says:
    # shadowing more decision points is safe (advisors never act) and P3 adds
    # ASSIGN near-ties and REPLAN. Every judgment point must be shadowed.
    assert ADVISORY_POINTS >= frozenset({SELECT_PLAN, EVIDENCE_GATE})
    assert JUDGMENT_POINTS <= ADVISORY_POINTS


@pytest.mark.parametrize("raw_score", [0.51, 0.6, 0.74])
def test_rule_choice_escalates_below_evidence_task_score_even_inside_the_plan(raw_score):
    state = _evidence_gate_state(raw_score, "LEFT_ALLEY_MIDDLE")
    legal = legal_set(state, load_catalogue_plans())
    assert legal.decision_point == EVIDENCE_GATE
    assert legal.rule_choice == ESCALATE_LABEL


@pytest.mark.parametrize("raw_score", [0.75, 0.9, 0.99])
def test_rule_choice_creates_task_at_or_above_evidence_task_score_inside_the_plan(raw_score):
    state = _evidence_gate_state(raw_score, "LEFT_ALLEY_MIDDLE")
    legal = legal_set(state, load_catalogue_plans())
    assert legal.decision_point == EVIDENCE_GATE
    assert legal.rule_choice == "create_task"


@pytest.mark.parametrize("raw_score", [0.51, 0.6, 0.74, 0.75, 0.9, 0.99])
def test_rule_choice_escalates_when_the_station_is_outside_the_plan_regardless_of_score(raw_score):
    state = _evidence_gate_state(raw_score, "SOME_OTHER_STATION",
                                  plan_station_ids=("LEFT_ALLEY_MIDDLE",))
    legal = legal_set(state, load_catalogue_plans())
    assert legal.decision_point == EVIDENCE_GATE
    assert legal.rule_choice == ESCALATE_LABEL


def test_rule_choice_candidates_stay_create_task_and_escalate_regardless_of_the_rule_choice():
    """Advisors still choose among both candidates even though a rule
    already decided -- rule_choice narrows nothing about `candidates`."""
    inside_low = legal_set(_evidence_gate_state(0.6, "LEFT_ALLEY_MIDDLE"), load_catalogue_plans())
    inside_high = legal_set(_evidence_gate_state(0.9, "LEFT_ALLEY_MIDDLE"), load_catalogue_plans())
    assert {c.label for c in inside_low.candidates} == {"create_task", "escalate"}
    assert {c.label for c in inside_high.candidates} == {"create_task", "escalate"}
