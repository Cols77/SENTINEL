"""Unit contract for the approval-gated Jev plan proposer."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import math

import pytest

from sentinel.coordinator.decision_state import DecisionState
from sentinel.coordinator.legal_tools import Candidate, EVIDENCE_GATE, LegalSet, SELECT_PLAN
from sentinel.coordinator.policy import ContextualPolicy, PolicyContext
from sentinel.coordinator.proposer import JevProposalGates, JevProposer
from sentinel.coordinator.shadow import MemoryAuditSink


RUN_ID = "11111111-1111-4111-8111-111111111111"
AGENT_RUN_ID = "22222222-2222-4222-8222-222222222222"
ROUTE = "PLAN_ROUTE_ONLY_2_ZONE"
FULL = "PLAN_MARKER_VERIFIED_6"


def _request() -> dict:
    return {"agent_run_id": AGENT_RUN_ID}


def _state() -> DecisionState:
    return DecisionState(
        run_id=RUN_ID, state_version=4, run_status="RUNNING", agent_phase="PLAN",
        emergency_latched=False, request_text="inspect the route", plan_id=None,
        plan_mode=None, plan_station_ids=(), plan_approved=False, detection=None,
        open_task_station_ids=(), task_id=None, task_status=None,
    )


def _context(point: str = SELECT_PLAN) -> PolicyContext:
    return PolicyContext(_state(), LegalSet(point, (
        Candidate("inspection.propose_plan", {"catalogue_plan_id": ROUTE},
                  ROUTE, "route-only plan"),
        Candidate("inspection.propose_plan", {"catalogue_plan_id": FULL}, FULL,
                  "full marker-verified plan"),
        Candidate("finish", {"status": "NEEDS_REVIEW", "summary": "ask"},
                  "escalate", "ask the operator"),
    )))


class Judge:
    def __init__(self, answer=None, error: Exception | None = None):
        self.answer = {
            "value": ROUTE,
            "confidence": 0.8,
            "probabilities": {ROUTE: 0.7, FULL: 0.3},
        } if answer is None else answer
        self.error = error
        self.calls = []

    def choice(self, state, instruction, *, criteria, name):
        self.calls.append((state, instruction, criteria, name))
        if self.error:
            raise self.error
        return self.answer


def _assert_escalation(result: dict) -> None:
    call = result["tool_calls"][0]
    assert call["tool_name"] == "finish"
    assert call["arguments"]["status"] == "NEEDS_REVIEW"


def test_calls_jev_with_packet_described_candidates_and_no_escalate() -> None:
    judge = Judge()
    proposer = JevProposer(judge, MemoryAuditSink())

    assert isinstance(proposer, ContextualPolicy)
    proposer.decide(_request(), _context())

    state, instruction, criteria, name = judge.calls[0]
    assert state == _state().to_packet()
    assert name == "select_plan"
    assert criteria == {ROUTE: "route-only plan", FULL: "full marker-verified plan"}
    assert "SELECT_PLAN" in instruction
    assert "escalate" not in criteria


def test_accepts_top_ranked_answer_and_preserves_arguments_with_derived_reason_and_audit() -> None:
    sink = MemoryAuditSink()
    result = JevProposer(Judge(), sink).decide(_request(), _context())

    call = result["tool_calls"][0]
    assert call["tool_name"] == "inspection.propose_plan"
    assert call["arguments"] == {
        "catalogue_plan_id": ROUTE,
        "reason": "SELECT_PLAN: route-only plan",
    }
    assert sink.records == [{
        "record": "proposal_recommendation",
        "run_id": RUN_ID,
        "state_version": 4,
        "decision_point": SELECT_PLAN,
        "label": ROUTE,
        "reason": "SELECT_PLAN: route-only plan",
        "facts": {
            "operator_request": "inspect the route",
            "candidate_description": "route-only plan",
        },
        "advisor": "jev",
        "judge_label": ROUTE,
        "policy_label": ROUTE,
        "confidence": 0.8,
        "probabilities": {ROUTE: 0.7, FULL: 0.3},
        "margin": pytest.approx(0.4),
    }]


@pytest.mark.parametrize("answer", [
    {},
    {"value": "UNKNOWN", "confidence": 0.9, "probabilities": {ROUTE: 0.1, FULL: 0.9}},
    {"value": ROUTE, "confidence": 0.59, "probabilities": {ROUTE: 0.8, FULL: 0.2}},
    {"value": ROUTE, "confidence": 0.9, "probabilities": {ROUTE: 0.55, FULL: 0.45}},
    {"value": ROUTE, "confidence": 0.9, "probabilities": {ROUTE: 0.4, FULL: 0.6}},
    {"value": ROUTE, "confidence": float("nan"), "probabilities": {ROUTE: 0.8, FULL: 0.2}},
    {"value": ROUTE, "confidence": 0.9, "probabilities": {ROUTE: math.inf, FULL: 0.2}},
    {"value": ROUTE, "confidence": 0.9, "probabilities": {ROUTE: 0.8}},
    {"value": ROUTE, "confidence": 0.9, "probabilities": {ROUTE: 0.8, FULL: 0.2, "extra": 0.0}},
])
def test_invalid_or_below_gate_answers_fail_closed_without_audit(answer) -> None:
    sink = MemoryAuditSink()
    judge = Judge(answer=answer)

    result = JevProposer(judge, sink).decide(_request(), _context())

    _assert_escalation(result)
    assert sink.records == []


def test_provider_error_and_non_select_plan_fail_closed_without_call_or_audit() -> None:
    sink = MemoryAuditSink()
    raising = Judge(error=TimeoutError("late"))
    _assert_escalation(JevProposer(raising, sink).decide(_request(), _context()))
    assert sink.records == []

    untouched = Judge()
    _assert_escalation(JevProposer(untouched, sink).decide(_request(), _context(EVIDENCE_GATE)))
    assert untouched.calls == []
    assert sink.records == []


def test_gates_are_frozen_and_have_the_approved_defaults() -> None:
    gates = JevProposalGates()
    assert gates.act_at == 0.60
    assert gates.min_margin == 0.15
    with pytest.raises(FrozenInstanceError):
        gates.act_at = 0.1
