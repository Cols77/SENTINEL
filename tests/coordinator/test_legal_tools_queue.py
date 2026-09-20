"""P3a Task 7: oldest-first detection queue in legal_set; a duplicate or
repeat detection becomes a judgment point instead of a rule-decided one."""
from __future__ import annotations

from sentinel.contracts import AgentModelResult
from sentinel.coordinator.agent_loop import AgentLoop
from sentinel.coordinator.legal_tools import (
    ASSIGN,
    ESCALATE_LABEL,
    EVIDENCE_GATE,
    EVIDENCE_VETO,
    RANK,
    TERMINAL,
    legal_set,
    load_catalogue_plans,
    needs_judgment,
)
from sentinel.coordinator.policies import RulePolicy
from sentinel.coordinator.policy import bind_context

import pytest

from .harness import AGENT_RUN_ID, CoordinatorBoundary
from .p3_helpers import LEFT_FRONT, LEFT_MID, RIGHT_MID, P3Run

CATALOGUE = load_catalogue_plans()


@pytest.fixture
def run(tmp_path):
    r = P3Run(tmp_path, "queue")
    r.approve_route_plan()
    yield r
    r.close()


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


def _run_candidate(boundary, run_id, candidate, *, max_turns=1):
    """Execute the candidate through AgentLoop with a one-shot policy; proves gateway-validity."""
    def _model(request):
        return _agent_result_for(request["agent_run_id"], candidate)

    return AgentLoop(boundary, _model, max_turns=max_turns).run(run_id, AGENT_RUN_ID, "inspect one station")


def test_clean_detection_is_settled_by_rule(run):
    a = run.ingest("a", LEFT_MID, 0.90)
    legal = legal_set(run.state(), CATALOGUE)
    assert legal.decision_point == EVIDENCE_GATE and legal.rule_choice == "create_task"
    assert legal.by_label("create_task").arguments["perception_result_id"] == a["result_id"]


def test_queue_triages_the_oldest_detection_first(run):
    a = run.ingest("a", LEFT_MID, 0.90)
    c = run.ingest("c", RIGHT_MID, 0.95)
    legal = legal_set(run.state(), CATALOGUE)
    assert legal.by_label("create_task").arguments["perception_result_id"] == a["result_id"]
    run.create_task(a)
    legal = legal_set(run.state(), CATALOGUE)
    assert legal.by_label("create_task").arguments["perception_result_id"] == c["result_id"]


def test_duplicate_of_an_open_task_is_a_judgment_point(run):
    a = run.ingest("a", LEFT_MID, 0.90)
    run.ingest("b", LEFT_MID, 0.92)
    run.create_task(a)
    legal = legal_set(run.state(), CATALOGUE)
    assert legal.decision_point == EVIDENCE_GATE and legal.rule_choice is None
    assert legal.labels == ("create_task", "escalate") and needs_judgment(legal)


def test_out_of_plan_detection_escalates_by_rule(run):
    run.ingest("x", LEFT_FRONT, 0.90)
    legal = legal_set(run.state(), CATALOGUE)
    assert legal.rule_choice == "escalate"


def test_inside_plan_below_task_score_escalates_by_rule(run):
    run.ingest("a", LEFT_MID, 0.62)
    legal = legal_set(run.state(), CATALOGUE)
    assert legal.decision_point == EVIDENCE_GATE and legal.rule_choice == "escalate"


def test_below_static_threshold_is_still_the_veto(run):
    run.ingest("a", LEFT_MID, 0.35)
    legal = legal_set(run.state(), CATALOGUE)
    assert legal.decision_point == EVIDENCE_VETO
    assert len(legal.candidates) == 1 and legal.candidates[0].label == ESCALATE_LABEL


def test_rank_then_assign_walk_tasks_oldest_first(run):
    a = run.ingest("a", LEFT_MID, 0.90)
    c = run.ingest("c", RIGHT_MID, 0.95)
    first = run.create_task(a)
    second = run.create_task(c)

    legal = legal_set(run.state(), CATALOGUE)
    assert legal.decision_point == RANK and legal.candidates[0].arguments["task_id"] == first
    run.rank(first)

    legal = legal_set(run.state(), CATALOGUE)
    assert legal.decision_point == RANK and legal.candidates[0].arguments["task_id"] == second
    run.rank(second)

    legal = legal_set(run.state(), CATALOGUE)
    assert legal.decision_point == ASSIGN and legal.candidates[0].arguments["task_id"] == first


def test_two_clean_detections_run_unattended_until_the_first_assignment(run):
    run.ingest("a", LEFT_MID, 0.90)
    run.ingest("c", RIGHT_MID, 0.95)
    boundary = CoordinatorBoundary(run.repo, run.gateway)
    policy = bind_context(RulePolicy(), run.repo, CATALOGUE)

    result = AgentLoop(boundary, policy, max_turns=8, repository=run.repo, catalogue=CATALOGUE).run(
        run.run_id, AGENT_RUN_ID, "route-only survey of the alley stations")

    assert result["status"] != "NEEDS_REVIEW"
    events = [e["event_type"] for e in run.repo.read_events(run.run_id, 0, 64)]
    assert events.count("TASK_CREATED") == 2

    state = run.state()
    assert len(state.tasks) == 2
    assert all(t.ranked_patrollers for t in state.tasks)
    assert state.run_status == "WAITING_FOR_APPROVAL"
    assert state.tasks[0].status == "ASSIGNMENT_PROPOSED"
    assert state.tasks[1].status == "PROPOSED"


def test_characterization_second_task_is_stranded_by_run_completion(run):
    """Blocker A1 (proposal item P-1). When P-1 is granted this test flips; do not work around it."""
    run.ingest("a", LEFT_MID, 0.90)
    run.ingest("c", RIGHT_MID, 0.95)
    boundary = CoordinatorBoundary(run.repo, run.gateway)
    policy = bind_context(RulePolicy(), run.repo, CATALOGUE)
    AgentLoop(boundary, policy, max_turns=8, repository=run.repo, catalogue=CATALOGUE).run(
        run.run_id, AGENT_RUN_ID, "route-only survey of the alley stations")

    first_task_id = run.state().tasks[0].task_id
    run.submit("OPERATOR_DECIDE", {"decision": "CONFIRM", "target": {"kind": "TASK", "task_id": first_task_id}},
               actor_type="OPERATOR", with_version=True)

    state = run.state()
    assert state.run_status == "COMPLETED"
    assert legal_set(state, CATALOGUE).decision_point == TERMINAL
    second_task = next(t for t in state.tasks if t.task_id != first_task_id)
    assert second_task.status == "PROPOSED"


def test_every_queue_candidate_is_accepted_by_the_gateway(run):
    """Each candidate the queue offers is gateway-valid: AgentLoop's own turn
    budget only ever surfaces a top-level "ACCEPTED" status through the
    WAITING_FOR_APPROVAL early-return (create_task/rank do not trigger that),
    so create_task and rank are proven accepted by the state mutation they
    produce, and assign_rank_1 -- which does trigger WAITING_FOR_APPROVAL --
    is proven with the literal "ACCEPTED" tool-result status."""
    a = run.ingest("a", LEFT_MID, 0.90)
    boundary = CoordinatorBoundary(run.repo, run.gateway)

    legal = legal_set(run.state(), CATALOGUE)
    create = legal.by_label("create_task")
    _run_candidate(boundary, run.run_id, create)
    state = run.state()
    assert len(state.tasks) == 1 and state.tasks[0].perception_result_id == a["result_id"]

    legal = legal_set(state, CATALOGUE)
    assert legal.decision_point == RANK
    _run_candidate(boundary, run.run_id, legal.candidates[0])
    state = run.state()
    assert state.tasks[0].ranked_patrollers == ("PATROLLER_1",)

    legal = legal_set(state, CATALOGUE)
    assert legal.decision_point == ASSIGN
    result = _run_candidate(boundary, run.run_id, legal.candidates[0], max_turns=2)
    assert result["status"] == "ACCEPTED"
    assert run.state().run_status == "WAITING_FOR_APPROVAL"
