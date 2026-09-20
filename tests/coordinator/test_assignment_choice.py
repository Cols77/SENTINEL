"""P3a Task 8: ASSIGN near-tie becomes a second judgment point, and
SELECT_PLAN omits catalogue entries already proposed (and rejected) in this
run. Extends P2b's `rule_choice` rather than rewriting it -- a clear gap or
the gateway's own single-patroller ranking (blocker A3) is settled by rule,
exactly as before; only a near-tie between rank 1 and rank 2 is handed to
the operator, via the same `needs_judgment` seam Task 7 introduced."""
from __future__ import annotations

import pytest

from sentinel.contracts import AgentModelResult
from sentinel.coordinator.agent_loop import AgentLoop
from sentinel.coordinator.errors import CoordinatorError
from sentinel.coordinator.legal_tools import (
    ASSIGN,
    SELECT_PLAN,
    legal_set,
    load_catalogue_plans,
    needs_judgment,
)
from sentinel.coordinator.operator_choice import pending_decision, submit_choice
from sentinel.coordinator.policies import Decision, RulePolicy
from sentinel.coordinator.policy import bind_context
from sentinel.coordinator.shadow import MemoryAuditSink, ShadowLadder
from sentinel.coordinator.tasks import SimulatedPatroller

from .harness import AGENT_RUN_ID, CoordinatorBoundary
from .p3_helpers import LEFT_MID, P3Run

CATALOGUE = load_catalogue_plans()


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


class _AssignRankOneAdvisor:
    """Always picks assign_rank_1, the opposite of what RulePolicy does at a
    near-tie (it escalates) -- proves the ladder's returned result is the
    executor's regardless of what an advisor 'wants'."""

    def __init__(self) -> None:
        self.last: Decision | None = None
        self.calls = 0

    def decide(self, request, context):
        self.calls += 1
        candidate = context.legal.by_label("assign_rank_1")
        self.last = Decision(chooser="JEV", label=candidate.label, confidence=0.99, band="HIGH",
                              legal_labels=context.legal.labels, model="assign-test", error=None, latency_ms=1)
        return {"ignored": True}


def _rank_task(run, station=LEFT_MID, raw_score=0.90):
    task_id = run.create_task(run.ingest("a", station, raw_score))
    return task_id


# --- clear gap / single patroller: unchanged from P2b -----------------------

def test_single_patroller_is_unchanged_from_p2(tmp_path):
    run = P3Run(tmp_path, "assign-single")
    try:
        run.approve_route_plan()
        task_id = _rank_task(run)
        run.rank(task_id)  # gateway TASK_RANK -> always a single PATROLLER_1 ranking (blocker A3)
        legal = legal_set(run.state(), CATALOGUE)
        assert legal.decision_point == ASSIGN
        assert legal.labels == ("assign_rank_1",)
        assert legal.candidates[0].description == "propose the rank-1 patroller"
        assert not needs_judgment(legal)
    finally:
        run.close()


def test_clear_gap_is_settled_by_rule(tmp_path):
    run = P3Run(tmp_path, "assign-clear-gap")
    try:
        run.approve_route_plan()
        task_id = _rank_task(run)
        SimulatedPatroller(run.repo).rank_for_task(task_id, {"PATROLLER_1": 0.90, "PATROLLER_2": 0.60})
        legal = legal_set(run.state(), CATALOGUE)
        assert legal.decision_point == ASSIGN
        assert legal.labels == ("assign_rank_1",)
        assert legal.candidates[0].description == "propose the rank-1 patroller"
        assert not needs_judgment(legal)
    finally:
        run.close()


# --- near-tie: the new judgment point ---------------------------------------

def test_near_tie_is_a_judgment_point(tmp_path):
    run = P3Run(tmp_path, "assign-near-tie")
    try:
        run.approve_route_plan()
        task_id = _rank_task(run)
        SimulatedPatroller(run.repo).rank_for_task(task_id, {"PATROLLER_1": 0.81, "PATROLLER_2": 0.79})
        legal = legal_set(run.state(), CATALOGUE)
        assert legal.decision_point == ASSIGN
        assert legal.labels == ("assign_rank_1", "escalate")
        assert legal.rule_choice is None
        assert needs_judgment(legal)
        assert "very_close" in legal.by_label("assign_rank_1").description
    finally:
        run.close()


def test_near_tie_candidate_is_accepted_by_the_gateway(tmp_path):
    run = P3Run(tmp_path, "assign-near-tie-gateway")
    try:
        run.approve_route_plan()
        task_id = _rank_task(run)
        SimulatedPatroller(run.repo).rank_for_task(task_id, {"PATROLLER_1": 0.81, "PATROLLER_2": 0.79})
        boundary = CoordinatorBoundary(run.repo, run.gateway)
        legal = legal_set(run.state(), CATALOGUE)

        result = _run_candidate(boundary, run.run_id, legal.by_label("assign_rank_1"), max_turns=2)

        assert result["status"] == "ACCEPTED"
        assert run.state().run_status == "WAITING_FOR_APPROVAL"
    finally:
        run.close()


def test_rule_policy_escalates_a_near_tie_and_operator_can_choose(tmp_path):
    run = P3Run(tmp_path, "assign-near-tie-operator")
    try:
        run.approve_route_plan()
        task_id = _rank_task(run)
        SimulatedPatroller(run.repo).rank_for_task(task_id, {"PATROLLER_1": 0.81, "PATROLLER_2": 0.79})

        boundary = CoordinatorBoundary(run.repo, run.gateway)
        policy = bind_context(RulePolicy(), run.repo, CATALOGUE)
        result = AgentLoop(boundary, policy, max_turns=2, repository=run.repo, catalogue=CATALOGUE).run(
            run.run_id, AGENT_RUN_ID, "route-only survey of the alley stations")
        assert result["status"] == "NEEDS_REVIEW"

        pending = pending_decision(run.repo, run.run_id, CATALOGUE)
        assert pending["decision_point"] == ASSIGN
        assert {option["label"] for option in pending["options"]} == {"assign_rank_1", "escalate"}

        sink = MemoryAuditSink()
        submit_choice(run.repo, run.gateway, run.run_id, "assign_rank_1", catalogue=CATALOGUE, sink=sink)
        assert run.state().run_status == "WAITING_FOR_APPROVAL"
    finally:
        run.close()


def test_pending_decision_is_none_on_a_clear_gap(tmp_path):
    run = P3Run(tmp_path, "assign-clear-gap-pending")
    try:
        run.approve_route_plan()
        task_id = _rank_task(run)
        SimulatedPatroller(run.repo).rank_for_task(task_id, {"PATROLLER_1": 0.90, "PATROLLER_2": 0.60})
        assert pending_decision(run.repo, run.run_id, CATALOGUE) is None
    finally:
        run.close()


# --- characterization: rank-2/3 candidates are not offered (A2 / P-2) ------

def test_characterization_rank_2_is_rejected_by_the_gateway(tmp_path):
    """Blocker A2 (proposal item P-2): ASSIGNMENT_PROPOSE only accepts the
    persisted rank-1 patroller. Not worked around here -- pinned so P3a-2
    knows exactly what to flip when the operator grants P-2."""
    run = P3Run(tmp_path, "assign-rank2-rejected")
    try:
        run.approve_route_plan()
        task_id = _rank_task(run)
        SimulatedPatroller(run.repo).rank_for_task(task_id, {"PATROLLER_1": 0.90, "PATROLLER_2": 0.60})

        with pytest.raises(CoordinatorError) as caught:
            run.submit("ASSIGNMENT_PROPOSE",
                       {"task_id": task_id, "patroller_id": "PATROLLER_2", "reason": "try rank 2"})
        assert caught.value.code == "INVALID_STATE_TRANSITION"
    finally:
        run.close()


# --- SELECT_PLAN omits already-proposed entries (A9 / P-3 support) ---------

def test_select_plan_omits_entries_already_proposed_in_this_run(tmp_path):
    run = P3Run(tmp_path, "select-plan-omit")
    try:
        run.propose_plan("PLAN_ROUTE_ONLY_2_ZONE")
        run.decide_latest_plan("REJECT")

        legal = legal_set(run.state(), CATALOGUE)
        assert legal.decision_point == SELECT_PLAN
        assert legal.labels == ("PLAN_MARKER_VERIFIED_6", "escalate")
    finally:
        run.close()


# --- non-interference: an advisor cannot change a near-tie assignment ------

def test_non_interference_advisor_cannot_change_a_near_tie_assignment(tmp_path):
    run = P3Run(tmp_path, "assign-near-tie-shadow")
    try:
        run.approve_route_plan()
        task_id = _rank_task(run)
        SimulatedPatroller(run.repo).rank_for_task(task_id, {"PATROLLER_1": 0.81, "PATROLLER_2": 0.79})

        boundary = CoordinatorBoundary(run.repo, run.gateway)
        executor = RulePolicy()
        advisor = _AssignRankOneAdvisor()
        sink = MemoryAuditSink()
        ladder = ShadowLadder(executor, [("optimistic", advisor)], sink)
        policy = bind_context(ladder, run.repo, CATALOGUE)

        result = AgentLoop(boundary, policy, max_turns=2, repository=run.repo, catalogue=CATALOGUE).run(
            run.run_id, AGENT_RUN_ID, "route-only survey of the alley stations")

        assert result["status"] == "NEEDS_REVIEW"  # the executor's own decision, unaffected by the advisor
        assert run.state().run_status != "WAITING_FOR_APPROVAL"  # nothing was actually assigned
        assert advisor.calls == 1

        judgment = next(r for r in sink.records if r["record"] == "judgment" and r["decision_point"] == ASSIGN)
        assert judgment["advisors"]["optimistic"]["label"] == "assign_rank_1"
        assert judgment["executor"] == {"chooser": "OPERATOR", "label": "escalate"}
    finally:
        run.close()
