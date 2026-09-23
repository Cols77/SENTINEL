"""Unit contract for the approval-gated Jev plan proposer."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import math

import pytest

from sentinel.coordinator.decision_state import DecisionState
from sentinel.coordinator.legal_tools import (
    ESCALATE_LABEL,
    Candidate,
    EVIDENCE_GATE,
    LegalSet,
    SELECT_PLAN,
)
from sentinel.coordinator.policy import ContextualPolicy, PolicyContext
from sentinel.coordinator.proposer import JevProposalGates, JevProposer
from sentinel.coordinator.shadow import MemoryAuditSink


RUN_ID = "11111111-1111-4111-8111-111111111111"
AGENT_RUN_ID = "22222222-2222-4222-8222-222222222222"
ROUTE = "PLAN_ROUTE_ONLY_2_ZONE"
FULL = "PLAN_MARKER_VERIFIED_6"


def _request() -> dict:
    return {
        "agent_run_id": AGENT_RUN_ID,
        "run_id": RUN_ID,
        "state_version": 4,
    }


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
                  ESCALATE_LABEL, "ask the operator"),
    )))


class Judge:
    def __init__(self, answer=None, error: Exception | None = None):
        self.answer = {
            "value": ROUTE,
            "confidence": 0.8,
            "probabilities": {ROUTE: 0.7, FULL: 0.2, ESCALATE_LABEL: 0.1},
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


def test_calls_jev_with_packet_and_every_described_legal_candidate() -> None:
    judge = Judge()
    proposer = JevProposer(judge, MemoryAuditSink())

    assert isinstance(proposer, ContextualPolicy)
    proposer.decide(_request(), _context())

    state, instruction, criteria, name = judge.calls[0]
    assert state == {
        **_state().to_packet(),
        "run_id": RUN_ID,
        "agent_run_id": AGENT_RUN_ID,
        "state_version": 4,
    }
    assert name == "select_plan"
    assert criteria == {
        ROUTE: (
            "Choose this catalogue plan when the operator request is fully matched by "
            "the future work it will perform: route-only plan"
        ),
        FULL: (
            "Choose this catalogue plan when the operator request is fully matched by "
            "the future work it will perform: full marker-verified plan"
        ),
        ESCALATE_LABEL: (
            "Choose escalation only when no catalogue plan fully matches every explicit "
            "request constraint, the constraints conflict, or the request lacks enough "
            "detail to choose. Do not escalate merely because the selected work has not "
            "run yet or downstream results are absent."
        ),
    }
    assert "SELECT_PLAN" in instruction
    assert "fully satisfy every explicit operator constraint" in instruction
    assert "closest" in instruction
    assert "future work" in instruction
    assert "downstream facts" in instruction


@pytest.mark.parametrize("field, mismatched", [
    ("run_id", "33333333-3333-4333-8333-333333333333"),
    ("state_version", 5),
])
def test_request_and_context_binding_mismatch_fails_closed(field, mismatched) -> None:
    request = _request()
    request[field] = mismatched
    judge = Judge()
    sink = MemoryAuditSink()

    result = JevProposer(judge, sink).decide(request, _context())

    _assert_escalation(result)
    assert judge.calls == []
    attempt, = sink.records
    assert attempt["gate_outcome"] == "invalid_answer"
    assert attempt["policy_label"] == ESCALATE_LABEL


def test_accepts_top_ranked_answer_and_preserves_arguments_with_derived_reason_and_audit() -> None:
    sink = MemoryAuditSink()
    result = JevProposer(Judge(), sink).decide(_request(), _context())

    call = result["tool_calls"][0]
    assert call["tool_name"] == "inspection.propose_plan"
    assert call["arguments"] == {
        "catalogue_plan_id": ROUTE,
        "reason": "SELECT_PLAN: route-only plan",
    }
    assert [r for r in sink.records if r["record"] == "proposal_recommendation"] == [{
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
        "probabilities": {ROUTE: 0.7, FULL: 0.2, ESCALATE_LABEL: 0.1},
        "margin": pytest.approx(0.5),
    }]


def test_accepts_jev_escalation_without_writing_a_recommendation() -> None:
    sink = MemoryAuditSink()
    answer = {
        "value": ESCALATE_LABEL,
        "confidence": 0.85,
        "probabilities": {ROUTE: 0.05, FULL: 0.10, ESCALATE_LABEL: 0.85},
    }

    result = JevProposer(Judge(answer), sink).decide(_request(), _context())

    assert result == {
        "contract_version": "sentinel.v0",
        "wire_type": "agent_result",
        "agent_run_id": AGENT_RUN_ID,
        "status": "TOOL_CALLS",
        "tool_calls": [{
            "contract_version": "sentinel.v0",
            "wire_type": "agent_tool_call",
            "tool_name": "finish",
            "arguments": {
                "status": "NEEDS_REVIEW",
                "summary": "plan proposal needs operator review",
            },
        }],
    }
    attempt, = sink.records
    assert attempt["raw_choice"] == ESCALATE_LABEL
    assert attempt["policy_label"] == ESCALATE_LABEL
    assert attempt["gate_outcome"] == "accepted"
    assert not [r for r in sink.records if r["record"] == "proposal_recommendation"]


@pytest.mark.parametrize("answer", [
    ["invalid"],
    {},
    {"value": "UNKNOWN", "confidence": 0.9,
     "probabilities": {ROUTE: 0.1, FULL: 0.1, ESCALATE_LABEL: 0.8}},
    {"value": ROUTE, "confidence": 0.59,
     "probabilities": {ROUTE: 0.8, FULL: 0.1, ESCALATE_LABEL: 0.1}},
    {"value": ROUTE, "confidence": 0.9,
     "probabilities": {ROUTE: 0.55, FULL: 0.45, ESCALATE_LABEL: 0.0}},
    {"value": ROUTE, "confidence": 0.9,
     "probabilities": {ROUTE: 0.4, FULL: 0.5, ESCALATE_LABEL: 0.1}},
    {"value": ROUTE, "confidence": float("nan"),
     "probabilities": {ROUTE: 0.8, FULL: 0.1, ESCALATE_LABEL: 0.1}},
    {"value": ROUTE, "confidence": 0.9,
     "probabilities": {ROUTE: math.inf, FULL: 0.1, ESCALATE_LABEL: 0.1}},
    # Intentionally missing the legal escalation label.
    {"value": ROUTE, "confidence": 0.9, "probabilities": {ROUTE: 0.8}},
    # Intentionally includes a non-legal extra label.
    {"value": ROUTE, "confidence": 0.9,
     "probabilities": {ROUTE: 0.8, FULL: 0.1, ESCALATE_LABEL: 0.1, "extra": 0.0}},
    {"value": ROUTE,
     "probabilities": {ROUTE: 0.59, FULL: 0.31, ESCALATE_LABEL: 0.10}},
    {"value": ROUTE, "confidence": None,
     "probabilities": {ROUTE: 0.55, FULL: 0.35, ESCALATE_LABEL: 0.10}},
    {"value": ROUTE,
     "probabilities": {ROUTE: True, FULL: 0.1, ESCALATE_LABEL: 0.1}},
])
def test_invalid_or_below_gate_answers_fail_closed_with_attempt_audit(answer) -> None:
    sink = MemoryAuditSink()
    judge = Judge(answer=answer)

    result = JevProposer(judge, sink).decide(_request(), _context())

    _assert_escalation(result)
    assert len(sink.records) == 1
    assert sink.records[0]["record"] == "proposal_attempt"
    assert sink.records[0]["policy_label"] == "escalate"
    assert sink.records[0]["gate_outcome"] in {"invalid_answer", "low_confidence", "low_margin"}
    assert sink.records[0]["raw_choice"] == (answer.get("value") if isinstance(answer, dict) else None)


def test_provider_error_is_audited_but_non_select_plan_does_not_attempt() -> None:
    sink = MemoryAuditSink()
    raising = Judge(error=TimeoutError("late"))
    _assert_escalation(JevProposer(raising, sink).decide(_request(), _context()))
    assert len(sink.records) == 1
    assert sink.records[0]["gate_outcome"] == "provider_error"
    assert sink.records[0]["error_category"] == "provider_error"

    untouched = Judge()
    _assert_escalation(JevProposer(untouched, sink).decide(_request(), _context(EVIDENCE_GATE)))
    assert untouched.calls == []
    assert len(sink.records) == 1


@pytest.mark.parametrize("confidence", [None, "absent"])
def test_optional_confidence_uses_selected_probability(confidence):
    answer = {"value": ROUTE,
              "probabilities": {ROUTE: 0.8, FULL: 0.1, ESCALATE_LABEL: 0.1}}
    if confidence != "absent":
        answer["confidence"] = confidence
    sink = MemoryAuditSink()
    result = JevProposer(Judge(answer), sink).decide(_request(), _context())
    assert result["tool_calls"][0]["tool_name"] == "inspection.propose_plan"
    attempt, recommendation = sink.records
    assert attempt["record"] == "proposal_attempt"
    assert attempt["judge_label"] == attempt["policy_label"] == ROUTE
    assert attempt["confidence"] == recommendation["confidence"] == 0.8
    assert attempt["selected_probability"] == 0.8
    assert attempt["confidence_source"] == "selected_probability_fallback"
    assert attempt["gate_outcome"] == "accepted"
    assert attempt["error_category"] is None
    assert attempt["latency_ms"] >= 0


@pytest.mark.parametrize("confidence", ["0.8", True, float("inf"), -0.1])
def test_explicit_malformed_confidence_does_not_fall_back(confidence):
    sink = MemoryAuditSink()
    answer = {"value": ROUTE, "confidence": confidence,
              "probabilities": {ROUTE: 0.8, FULL: 0.1, ESCALATE_LABEL: 0.1}}
    _assert_escalation(JevProposer(Judge(answer), sink).decide(_request(), _context()))
    assert sink.records[0]["gate_outcome"] == "invalid_answer"
    assert sink.records[0]["judge_label"] == ROUTE
    assert sink.records[0]["probabilities"] == {
        ROUTE: 0.8, FULL: 0.1, ESCALATE_LABEL: 0.1,
    }


def test_explicit_escalation_remains_subject_to_confidence_gates():
    sink = MemoryAuditSink()
    answer = {
        "value": ESCALATE_LABEL,
        "confidence": 0.20,
        "probabilities": {ROUTE: 0.35, FULL: 0.25, ESCALATE_LABEL: 0.40},
    }

    result = JevProposer(Judge(answer), sink).decide(_request(), _context())

    _assert_escalation(result)
    attempt, = sink.records
    assert attempt["raw_choice"] == ESCALATE_LABEL
    assert attempt["policy_label"] == ESCALATE_LABEL
    assert attempt["gate_outcome"] == "low_confidence"


@pytest.mark.parametrize("confidence, probabilities, outcome", [
    (0.59, {ROUTE: 0.8, FULL: 0.1, ESCALATE_LABEL: 0.1}, "low_confidence"),
    (0.9, {ROUTE: 0.55, FULL: 0.45, ESCALATE_LABEL: 0.0}, "low_margin"),
])
def test_rejected_attempt_preserves_gate_metrics(confidence, probabilities, outcome):
    sink = MemoryAuditSink()
    answer = {"value": ROUTE, "confidence": confidence, "probabilities": probabilities}
    _assert_escalation(JevProposer(Judge(answer), sink).decide(_request(), _context()))
    attempt, = sink.records
    assert attempt["gate_outcome"] == outcome
    assert attempt["error_category"] is None
    assert attempt["confidence"] == confidence
    assert attempt["selected_probability"] == probabilities[ROUTE]
    assert attempt["confidence_source"] == "provider_distribution_confidence"
    assert attempt["probabilities"] == probabilities
    assert attempt["margin"] == pytest.approx(probabilities[ROUTE] - probabilities[FULL])


@pytest.mark.parametrize(("confidence", "expected_label", "expected_outcome"), [
    (0.8, ROUTE, "accepted"),
    (0.4, ESCALATE_LABEL, "low_confidence"),
])
def test_jev_attempt_audit_keeps_its_label_and_gate_reason_after_shared_validation(
    confidence, expected_label, expected_outcome,
):
    sink = MemoryAuditSink()
    answer = {
        "value": ROUTE,
        "confidence": confidence,
        "probabilities": {ROUTE: 0.8, FULL: 0.1, ESCALATE_LABEL: 0.1},
    }

    JevProposer(Judge(answer), sink).decide(_request(), _context())

    attempt = sink.records[0]
    assert attempt["judge_label"] == ROUTE
    assert attempt["policy_label"] == expected_label
    assert attempt["gate_outcome"] == expected_outcome


def test_gates_are_frozen_and_have_the_approved_defaults() -> None:
    gates = JevProposalGates()
    assert gates.act_at == 0.60
    assert gates.min_margin == 0.15
    with pytest.raises(FrozenInstanceError):
        gates.act_at = 0.1


def test_invalid_coordinator_gate_configuration_escalates_without_crashing() -> None:
    sink = MemoryAuditSink()
    result = JevProposer(
        Judge(), sink, gates=JevProposalGates(min_margin="0.15"),
    ).decide(_request(), _context())

    _assert_escalation(result)
    assert sink.records[0]["gate_outcome"] == "invalid_answer"


@pytest.mark.parametrize("probabilities", [
    {ROUTE: True, FULL: False, ESCALATE_LABEL: 0.0},
    {ROUTE: "0.9", FULL: "0.1", ESCALATE_LABEL: 0.0},
])
def test_real_judge_transport_cannot_coerce_invalid_probability_types(monkeypatch, probabilities):
    from sentinel.judgment import JevJudge

    monkeypatch.setenv("TEST_JEV_KEY", "offline-test-only")
    judge = JevJudge(api_key_env="TEST_JEV_KEY", transport=lambda payload: {
        "answers": {"select_plan": {
            "choice": ROUTE, "confidence": None, "probabilities": probabilities,
        }},
    })
    sink = MemoryAuditSink()
    result = JevProposer(judge, sink).decide(_request(), _context())
    _assert_escalation(result)
    assert sink.records[0]["gate_outcome"] != "accepted"
