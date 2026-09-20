"""D11: RulePolicy (the F executor) and JevPolicy (fail-closed bands over
described legal candidates)."""
from __future__ import annotations

import pytest

from sentinel.coordinator.agent_loop import AgentLoop
from sentinel.coordinator.decision_state import DecisionState, read_decision_state
from sentinel.coordinator.legal_tools import (
    ESCALATE_LABEL,
    EVIDENCE_GATE,
    RANK,
    SELECT_PLAN,
    Candidate,
    LegalSet,
    legal_set,
    load_catalogue_plans,
)
from sentinel.coordinator.policies import INSTRUCTIONS, Decision, JevPolicy, RulePolicy
from sentinel.coordinator.policy import PolicyContext, bind_context
from sentinel.judgment import JudgeUnavailable

from .harness import ARTIFACT_ID, RESULT_ID, AGENT_RUN_ID, RUN_ID, CoordinatorBoundary, FakeWorker, close, envelope, open_harness

_TASK_ID = "77777777-7777-4777-8777-777777777777"


def _request() -> dict:
    return {
        "contract_version": "sentinel.v0", "wire_type": "agent_request",
        "agent_run_id": AGENT_RUN_ID, "run_id": RUN_ID, "state_version": 1,
        "allowed_intent_schema_id": "https://sentinel.local/contracts/sentinel-v0-wire.schema.json#AgentModelResult",
        "deadline": "2099-09-17T00:00:00Z", "phase": "PLAN",
        "event_context": [], "evidence_context": [], "legal_tools": ["wait_for_event"],
        "previous_result": None, "user_request": "inspect one station",
    }


def _rank_legal() -> LegalSet:
    return LegalSet(RANK, (Candidate("patroller.rank_for_task", {"task_id": _TASK_ID}, "rank",
                                      "rank patrollers for the task"),))


def _evidence_gate_legal_with_rule_choice(rule_choice: str | None) -> LegalSet:
    create = Candidate("task.create", {"perception_result_id": RESULT_ID, "section_id": "LEFT_ALLEY_MIDDLE",
                                        "evidence_artifact_id": ARTIFACT_ID}, "create_task",
                        "create a patrol task for this detection")
    escalate = Candidate("finish", {"status": "NEEDS_REVIEW", "summary": "review"}, ESCALATE_LABEL,
                          "hand the decision to the operator with the reason")
    return LegalSet(EVIDENCE_GATE, (create, escalate), rule_choice=rule_choice)


def _dummy_state() -> DecisionState:
    """A minimal DecisionState -- only ``to_packet()``/``facts()`` matter to
    JevPolicy, and the specific field values are not under test here."""
    return DecisionState(
        run_id=RUN_ID, state_version=1, run_status="AGENT_TURN", agent_phase="TASK",
        emergency_latched=False, request_text="inspect one station",
        plan_id="p1", plan_mode="ROUTE_ONLY", plan_station_ids=("LEFT_ALLEY_MIDDLE",),
        plan_approved=True, detection={"result_id": RESULT_ID, "artifact_id": ARTIFACT_ID,
                                        "station_id": "LEFT_ALLEY_MIDDLE", "status": "DETECTED",
                                        "raw_score": 0.99, "canonical_class": "rock"},
        open_task_station_ids=(), task_id=None, task_status=None,
    )


def _evidence_gate_legal() -> LegalSet:
    create = Candidate("task.create", {"perception_result_id": RESULT_ID, "section_id": "LEFT_ALLEY_MIDDLE",
                                        "evidence_artifact_id": ARTIFACT_ID}, "create_task",
                        "create a patrol task for this detection")
    escalate = Candidate("finish", {"status": "NEEDS_REVIEW", "summary": "review"}, ESCALATE_LABEL,
                          "hand the decision to the operator with the reason")
    return LegalSet("EVIDENCE_GATE", (create, escalate))


class _RaisingJudge:
    def choice(self, state, instruction, *, criteria, name):
        raise AssertionError("judge must not be called for a single candidate")


class _ScriptedJudge:
    def __init__(self, value, confidence=None, probabilities=None, model="test-jev"):
        self.value = value
        self.confidence = confidence
        self.probabilities = probabilities
        self._model = model
        self.calls: list[dict] = []

    def choice(self, state, instruction, *, criteria, name):
        self.calls.append({"state": state, "instruction": instruction, "criteria": criteria, "name": name})
        probabilities = self.probabilities
        if probabilities is None:
            probabilities = {self.value: self.confidence if self.confidence is not None else 1.0}
        return {"value": self.value, "confidence": self.confidence, "probabilities": probabilities}


class _FailingJudge:
    def choice(self, state, instruction, *, criteria, name):
        raise JudgeUnavailable("judge endpoint unreachable")


# --- RulePolicy -------------------------------------------------------------

def test_rule_policy_acts_on_the_single_candidate():
    policy = RulePolicy()
    result = policy.decide(_request(), PolicyContext(state=None, legal=_rank_legal()))
    assert result["tool_calls"][0]["tool_name"] == "patroller.rank_for_task"
    assert policy.last.chooser == "RULE"
    assert policy.last.label == "rank"


def test_rule_policy_escalates_at_select_plan_when_multiple_candidates():
    plans = tuple(
        Candidate("inspection.propose_plan", {"catalogue_plan_id": p.plan_id}, p.plan_id, p.description)
        for p in load_catalogue_plans()
    )
    escalate = Candidate("finish", {"status": "NEEDS_REVIEW", "summary": "no plan fits"}, ESCALATE_LABEL,
                          "hand the decision to the operator with the reason")
    legal = LegalSet(SELECT_PLAN, plans + (escalate,))
    policy = RulePolicy()
    result = policy.decide(_request(), PolicyContext(state=None, legal=legal))
    assert result["tool_calls"][0]["tool_name"] == "finish"
    assert policy.last.chooser == "OPERATOR"
    assert policy.last.label == ESCALATE_LABEL


def test_rule_policy_acts_on_rule_choice_create_task_instead_of_escalating():
    """Task A: at EVIDENCE_GATE (2 candidates) a deterministic rule_choice
    now decides -- RulePolicy no longer escalates just because there is
    more than one candidate."""
    legal = _evidence_gate_legal_with_rule_choice("create_task")
    policy = RulePolicy()
    result = policy.decide(_request(), PolicyContext(state=None, legal=legal))
    assert result["tool_calls"][0]["tool_name"] == "task.create"
    assert policy.last.chooser == "RULE"
    assert policy.last.label == "create_task"


def test_rule_policy_acts_on_rule_choice_escalate_without_needing_an_operator_choice():
    """rule_choice == escalate is still a *rule* decision (chooser RULE),
    not an OPERATOR one -- EVIDENCE_GATE is no longer a judgment point the
    operator is asked at."""
    legal = _evidence_gate_legal_with_rule_choice(ESCALATE_LABEL)
    policy = RulePolicy()
    result = policy.decide(_request(), PolicyContext(state=None, legal=legal))
    assert result["tool_calls"][0]["tool_name"] == "finish"
    assert policy.last.chooser == "RULE"
    assert policy.last.label == ESCALATE_LABEL


def test_rule_policy_still_escalates_at_a_multi_candidate_point_with_no_rule_choice():
    """Multiple candidates but no rule_choice (e.g. SELECT_PLAN, where
    legal_set() never computes one) -- RulePolicy's fallback OPERATOR
    escalation is unchanged."""
    legal = _evidence_gate_legal_with_rule_choice(None)
    policy = RulePolicy()
    result = policy.decide(_request(), PolicyContext(state=None, legal=legal))
    assert result["tool_calls"][0]["tool_name"] == "finish"
    assert policy.last.chooser == "OPERATOR"
    assert policy.last.label == ESCALATE_LABEL


def test_rule_policy_full_flow_stops_at_select_plan_with_needs_review(tmp_path):
    """(part of the full-flow bullet) RulePolicy never proposes a plan on its
    own: with 2+ catalogue plans it always escalates."""
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"}, sequence=1, actor_type="OPERATOR"))
        boundary = CoordinatorBoundary(repo, gateway)
        catalogue = load_catalogue_plans()
        policy = bind_context(RulePolicy(), repo, catalogue)

        result = AgentLoop(boundary, policy, max_turns=2, repository=repo, catalogue=catalogue).run(
            RUN_ID, AGENT_RUN_ID, "inspect one station")

        assert result["status"] == "NEEDS_REVIEW"
    finally:
        close(repo)


# --- JevPolicy ----------------------------------------------------------

def test_jev_policy_never_calls_the_judge_for_a_single_candidate():
    policy = JevPolicy(_RaisingJudge())
    result = policy.decide(_request(), PolicyContext(state=None, legal=_rank_legal()))
    assert result["tool_calls"][0]["tool_name"] == "patroller.rank_for_task"
    assert policy.last.chooser == "RULE"


def test_jev_policy_acts_high_confidence():
    """create_task at 0.91 (>= act_at) -> JEV acts."""
    judge = _ScriptedJudge("create_task", confidence=0.91)
    policy = JevPolicy(judge)
    result = policy.decide(_request(), PolicyContext(state=_dummy_state(), legal=_evidence_gate_legal()))
    assert result["tool_calls"][0]["tool_name"] == "task.create"
    assert policy.last.chooser == "JEV"
    assert policy.last.band == "HIGH"
    assert policy.last.confidence == 0.91


def test_jev_policy_mid_confidence_escalates_to_operator():
    """0.62 (review_below <= x < act_at) -> OPERATOR escalate, band MID."""
    judge = _ScriptedJudge("create_task", confidence=0.62)
    policy = JevPolicy(judge)
    result = policy.decide(_request(), PolicyContext(state=_dummy_state(), legal=_evidence_gate_legal()))
    assert result["tool_calls"][0]["tool_name"] == "finish"
    assert policy.last.chooser == "OPERATOR"
    assert policy.last.band == "MID"


def test_jev_policy_low_confidence_escalates_to_operator():
    judge = _ScriptedJudge("create_task", confidence=0.2)
    policy = JevPolicy(judge)
    result = policy.decide(_request(), PolicyContext(state=_dummy_state(), legal=_evidence_gate_legal()))
    assert result["tool_calls"][0]["tool_name"] == "finish"
    assert policy.last.chooser == "OPERATOR"
    assert policy.last.band == "LOW"


def test_jev_policy_escalate_pick_is_honoured_in_any_band():
    """escalate picked at 0.3 (a LOW-confidence answer) is still a JEV
    decision, not a fallback OPERATOR escalate."""
    judge = _ScriptedJudge(ESCALATE_LABEL, confidence=0.3)
    policy = JevPolicy(judge)
    result = policy.decide(_request(), PolicyContext(state=_dummy_state(), legal=_evidence_gate_legal()))
    assert result["tool_calls"][0]["tool_name"] == "finish"
    assert policy.last.chooser == "JEV"
    assert policy.last.label == ESCALATE_LABEL


def test_jev_policy_judge_unavailable_escalates_to_operator_with_error():
    policy = JevPolicy(_FailingJudge())
    result = policy.decide(_request(), PolicyContext(state=_dummy_state(), legal=_evidence_gate_legal()))
    assert result["tool_calls"][0]["tool_name"] == "finish"
    assert policy.last.chooser == "OPERATOR"
    assert policy.last.error is not None


def test_jev_policy_uses_probabilities_when_confidence_is_none():
    judge = _ScriptedJudge("create_task", confidence=None, probabilities={"create_task": 0.85, ESCALATE_LABEL: 0.15})
    policy = JevPolicy(judge)
    result = policy.decide(_request(), PolicyContext(state=_dummy_state(), legal=_evidence_gate_legal()))
    assert result["tool_calls"][0]["tool_name"] == "task.create"
    assert policy.last.confidence == 0.85
    assert policy.last.chooser == "JEV"


def test_jev_policy_passes_plan_descriptions_as_criteria(tmp_path):
    """The criteria dict handed to the judge at SELECT_PLAN carries the
    catalogue plan descriptions from Task 2, not bare labels."""
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"}, sequence=1, actor_type="OPERATOR"))
        catalogue = load_catalogue_plans()
        state = read_decision_state(repo, RUN_ID)
        legal = legal_set(state, catalogue)
        assert legal.decision_point == SELECT_PLAN

        judge = _ScriptedJudge("PLAN_ROUTE_ONLY_2_ZONE", confidence=0.95)
        policy = JevPolicy(judge)
        policy.decide(_request(), PolicyContext(state=state, legal=legal))

        criteria = judge.calls[0]["criteria"]
        by_label = {p.plan_id: p.description for p in catalogue}
        for label, description in by_label.items():
            assert criteria[label] == description
        assert criteria[ESCALATE_LABEL]
    finally:
        close(repo)


def test_jev_policy_decision_is_the_dataclass_shape():
    judge = _ScriptedJudge("create_task", confidence=0.91)
    policy = JevPolicy(judge)
    policy.decide(_request(), PolicyContext(state=_dummy_state(), legal=_evidence_gate_legal()))
    assert isinstance(policy.last, Decision)
    assert policy.last.legal_labels == ("create_task", ESCALATE_LABEL)
    assert policy.last.model == "test-jev"


# --- Full one-station flow with a fake Jev ------------------------------

def test_full_one_station_flow_with_jev_policy_matches_the_rule_event_sequence(tmp_path):
    """The full flow with a fake Jev (route-only at 0.95, create_task at 0.9)
    reproduces the event list asserted in test_one_station_flow.py, with
    operator approvals submitted by the test."""
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"}, sequence=1, actor_type="OPERATOR"))
        boundary = CoordinatorBoundary(repo, gateway)
        catalogue = load_catalogue_plans()

        class _FlowJudge:
            _model = "test-jev"

            def choice(self, state, instruction, *, criteria, name):
                if name == "select_plan":
                    return {"value": "PLAN_ROUTE_ONLY_2_ZONE", "confidence": 0.95, "probabilities": None}
                if name == "evidence_gate":
                    return {"value": "create_task", "confidence": 0.9, "probabilities": None}
                raise AssertionError(f"unexpected decision point {name!r}")

        policy = bind_context(JevPolicy(_FlowJudge()), repo, catalogue)

        result = AgentLoop(boundary, policy, max_turns=2, repository=repo, catalogue=catalogue).run(
            RUN_ID, AGENT_RUN_ID, "inspect one station")
        assert result["error_code"] == "WAITING_FOR_APPROVAL"

        plan = repo.connection.execute("SELECT * FROM plans").fetchone()
        reference = {
            "plan_id": plan["plan_id"], "catalogue_id": plan["catalogue_id"],
            "catalogue_revision": plan["catalogue_revision"], "plan_sha256": plan["plan_sha256"],
            "station_ids": __import__("json").loads(plan["station_ids_json"]),
        }
        gateway.submit(envelope("OPERATOR_DECIDE", {
            "decision": "CONFIRM", "target": {"kind": "PLAN", "plan_reference": reference},
        }, sequence=10, actor_type="OPERATOR", expected=repo.read_state(RUN_ID).to_dict()["state_version"]))

        worker = FakeWorker(gateway)
        worker.preflight(RUN_ID, reference["plan_id"], reference["plan_sha256"])
        worker.dispatch(RUN_ID, reference["plan_id"], "LEFT_ALLEY_MIDDLE", repo.read_state(RUN_ID).to_dict()["state_version"])
        worker.capture_and_perceive(RUN_ID, reference["plan_id"], reference["plan_sha256"])

        result = AgentLoop(boundary, policy, max_turns=4, repository=repo, catalogue=catalogue).run(
            RUN_ID, AGENT_RUN_ID, "inspect one station")
        assert result["error_code"] == "WAITING_FOR_APPROVAL"

        task_id = repo.connection.execute("SELECT task_id FROM tasks").fetchone()[0]
        gateway.submit(envelope("OPERATOR_DECIDE", {
            "decision": "CONFIRM", "target": {"kind": "TASK", "task_id": task_id},
        }, sequence=30, actor_type="OPERATOR", expected=repo.read_state(RUN_ID).to_dict()["state_version"]))

        assert repo.connection.execute("SELECT status FROM runs WHERE run_id = ?", (RUN_ID,)).fetchone()[0] == "COMPLETED"
        assert [event["event_type"] for event in repo.read_events(RUN_ID, 0, 30)] == [
            "RUN_CREATED", "USER_MESSAGE_RECEIVED", "PLAN_PROPOSED", "PLAN_CREATED",
            "AGENT_INTENT_PROPOSED", "PLAN_APPROVED", "PREFLIGHT_REPORTED",
            "DRONE_DISPATCH_REQUESTED", "DETECTION_RECORDED", "TASK_CREATED",
            "PATROLLER_ASSIGNMENT_PROPOSED", "PATROLLER_ASSIGNMENT_CONFIRMED",
        ]
    finally:
        close(repo)
