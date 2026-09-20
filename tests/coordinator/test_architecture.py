"""P4 Task 3: the architecture switch. F stays exactly
``ShadowLadder(RulePolicy(), advisors, sink)``, returned bare. E wraps that
same ladder in an ``ArchitectureRouter`` that gives ``SELECT_PLAN`` to a
proposer and folds every proposer failure back onto the ladder (architecture
F) on the same turn. The router, not the proposer, is the approval boundary."""
from __future__ import annotations

import pytest

from sentinel.coordinator.architecture import (
    ARCHITECTURE_ENV,
    ArchitectureUnavailable,
    build_policy,
    resolve_architecture,
)
from sentinel.coordinator.decision_state import DecisionState
from sentinel.coordinator.legal_tools import (
    ESCALATE_LABEL,
    EVIDENCE_GATE,
    SELECT_PLAN,
    Candidate,
    LegalSet,
    load_catalogue_plans,
)
from sentinel.coordinator.policies import RulePolicy
from sentinel.coordinator.policy import PolicyContext
from sentinel.coordinator.shadow import MemoryAuditSink, ShadowLadder

from .harness import AGENT_RUN_ID, ARTIFACT_ID, RESULT_ID, RUN_ID


def _request() -> dict:
    return {
        "contract_version": "sentinel.v0", "wire_type": "agent_request",
        "agent_run_id": AGENT_RUN_ID, "run_id": RUN_ID, "state_version": 1,
        "allowed_intent_schema_id": "https://sentinel.local/contracts/sentinel-v0-wire.schema.json#AgentModelResult",
        "deadline": "2099-09-17T00:00:00Z", "phase": "PLAN",
        "event_context": [], "evidence_context": [], "legal_tools": ["wait_for_event"],
        "previous_result": None, "user_request": "inspect one station",
    }


def _dummy_state(state_version: int = 1) -> DecisionState:
    """Only ``to_packet()``/``run_id``/``state_version`` matter here -- the
    specific field values are not under test (mirrors test_shadow.py)."""
    return DecisionState(
        run_id=RUN_ID, state_version=state_version, run_status="AGENT_TURN", agent_phase="TASK",
        emergency_latched=False, request_text="inspect one station",
        plan_id=None, plan_mode=None, plan_station_ids=(),
        plan_approved=False, detection=None,
        open_task_station_ids=(), task_id=None, task_status=None,
    )


def _select_plan_legal() -> LegalSet:
    plans = tuple(
        Candidate("inspection.propose_plan", {"catalogue_plan_id": p.plan_id}, p.plan_id, p.description)
        for p in load_catalogue_plans()
    )
    escalate = Candidate("finish", {"status": "NEEDS_REVIEW", "summary": "no plan fits"}, ESCALATE_LABEL,
                          "hand the decision to the operator with the reason")
    return LegalSet(SELECT_PLAN, plans + (escalate,))


def _evidence_gate_legal(rule_choice: str | None = "create_task") -> LegalSet:
    create = Candidate("task.create", {"perception_result_id": RESULT_ID, "section_id": "LEFT_ALLEY_MIDDLE",
                                        "evidence_artifact_id": ARTIFACT_ID}, "create_task",
                        "create a patrol task for this detection")
    escalate = Candidate("finish", {"status": "NEEDS_REVIEW", "summary": "review"}, ESCALATE_LABEL,
                          "hand the decision to the operator with the reason")
    return LegalSet(EVIDENCE_GATE, (create, escalate), rule_choice=rule_choice)


def _propose_result(plan_id: str) -> dict:
    return {
        "contract_version": "sentinel.v0", "wire_type": "agent_result",
        "agent_run_id": AGENT_RUN_ID, "status": "TOOL_CALLS",
        "tool_calls": [{
            "contract_version": "sentinel.v0", "wire_type": "agent_tool_call",
            "tool_name": "inspection.propose_plan", "arguments": {"catalogue_plan_id": plan_id},
        }],
    }


def _task_create_result() -> dict:
    return {
        "contract_version": "sentinel.v0", "wire_type": "agent_result",
        "agent_run_id": AGENT_RUN_ID, "status": "TOOL_CALLS",
        "tool_calls": [{
            "contract_version": "sentinel.v0", "wire_type": "agent_tool_call",
            "tool_name": "task.create", "arguments": {
                "perception_result_id": RESULT_ID, "section_id": "LEFT_ALLEY_MIDDLE",
                "evidence_artifact_id": ARTIFACT_ID,
            },
        }],
    }


def _escalate_result() -> dict:
    return {
        "contract_version": "sentinel.v0", "wire_type": "agent_result",
        "agent_run_id": AGENT_RUN_ID, "status": "NEEDS_REVIEW", "error_code": "NEEDS_REVIEW",
    }


class _ScriptedProposer:
    """Returns a fixed ``agent_result`` (or raises a fixed exception)."""

    def __init__(self, result: dict | None = None, exc: Exception | None = None) -> None:
        self.result = result
        self.exc = exc
        self.calls = 0

    def decide(self, request: dict, context: PolicyContext) -> dict:
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.result


class _NeverCallMe:
    def decide(self, request: dict, context: PolicyContext) -> dict:
        raise AssertionError("proposer must not be called off SELECT_PLAN")


def _bare_ladder_result(legal: LegalSet) -> dict:
    ladder = ShadowLadder(RulePolicy(), [], MemoryAuditSink())
    return ladder.decide(_request(), PolicyContext(state=_dummy_state(), legal=legal))


# --- resolve_architecture ----------------------------------------------------

def test_resolve_architecture_defaults_to_f_with_no_value_and_no_env():
    assert resolve_architecture(None, environ={}) == "F"


def test_resolve_architecture_honours_the_env_var():
    assert resolve_architecture(None, environ={ARCHITECTURE_ENV: "E"}) == "E"


def test_resolve_architecture_explicit_value_beats_env():
    assert resolve_architecture("F", environ={ARCHITECTURE_ENV: "E"}) == "F"


def test_resolve_architecture_is_case_insensitive_and_stripped():
    assert resolve_architecture(" e ", environ={}) == "E"


def test_resolve_architecture_unknown_value_raises():
    with pytest.raises(ValueError):
        resolve_architecture("G", environ={})


# --- build_policy -------------------------------------------------------------

def test_build_policy_f_is_a_bare_shadow_ladder():
    policy = build_policy("F", advisors=[], sink=MemoryAuditSink())
    assert type(policy) is ShadowLadder


def test_build_policy_e_with_no_proposer_raises_architecture_unavailable():
    with pytest.raises(ArchitectureUnavailable):
        build_policy("E", advisors=[], sink=MemoryAuditSink())


# --- ArchitectureRouter: accepted proposal at SELECT_PLAN ---------------------

def test_e_at_select_plan_accepts_a_legal_proposal_with_no_judgment_record():
    sink = MemoryAuditSink()
    legal = _select_plan_legal()
    plan_id = legal.candidates[0].label
    proposer = _ScriptedProposer(result=_propose_result(plan_id))
    router = build_policy("E", advisors=[], sink=sink, proposer=proposer)

    result = router.decide(_request(), PolicyContext(state=_dummy_state(), legal=legal))

    assert result == _propose_result(plan_id)
    assert proposer.calls == 1
    assert [r["record"] for r in sink.records] == ["architecture_route"]
    assert sink.records[0] == {
        "record": "architecture_route", "architecture": "E", "run_id": RUN_ID, "state_version": 1,
        "decision_point": SELECT_PLAN, "route": "proposer", "label": plan_id,
    }


# --- ArchitectureRouter: fallback reasons -------------------------------------

@pytest.mark.parametrize("make_proposer, expected_reason", [
    (lambda: _ScriptedProposer(exc=RuntimeError("boom")), "exception:RuntimeError"),
    (lambda: _ScriptedProposer(result=_escalate_result()), "proposer_escalated"),
    (lambda: _ScriptedProposer(result=_task_create_result()), "illegal_tool:task.create"),
    (lambda: _ScriptedProposer(result=_propose_result("NOT_A_REAL_CATALOGUE_PLAN")), "unknown_plan"),
    (lambda: _ScriptedProposer(result={"status": "TOOL_CALLS", "tool_calls": []}), "malformed_result"),
])
def test_e_select_plan_fallback_reasons_return_the_bare_ladder_result(make_proposer, expected_reason):
    legal = _select_plan_legal()
    expected = _bare_ladder_result(legal)

    sink = MemoryAuditSink()
    router = build_policy("E", advisors=[], sink=sink, proposer=make_proposer())

    result = router.decide(_request(), PolicyContext(state=_dummy_state(), legal=legal))

    assert result == expected
    assert [r["record"] for r in sink.records] == ["architecture_route", "judgment"]
    route = sink.records[0]
    assert route["route"] == "fallback_F"
    assert route["reason"] == expected_reason
    assert route["architecture"] == "E"
    assert route["run_id"] == RUN_ID
    assert route["state_version"] == 1
    assert route["decision_point"] == SELECT_PLAN
    assert "label" not in route


# --- ArchitectureRouter: never touches the proposer off SELECT_PLAN ----------

def test_e_at_a_non_select_plan_point_never_calls_the_proposer():
    legal = _evidence_gate_legal()
    expected = _bare_ladder_result(legal)

    sink = MemoryAuditSink()
    router = build_policy("E", advisors=[], sink=sink, proposer=_NeverCallMe())

    result = router.decide(_request(), PolicyContext(state=_dummy_state(), legal=legal))

    assert result == expected
    assert [r["record"] for r in sink.records] == ["judgment"]
