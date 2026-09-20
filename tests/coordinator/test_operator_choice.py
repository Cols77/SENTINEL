"""P2 Task 3: the operator choice path -- pending_decision withholds every
advisor field by construction, and submit_choice recomputes the legal set
fresh, rejects anything illegal, and submits through the same
CommandGateway every other mutation goes through."""
from __future__ import annotations

import json

import pytest

from sentinel.coordinator.agent_loop import AgentLoop
from sentinel.coordinator.decision_state import DecisionState
from sentinel.coordinator.errors import CoordinatorError
from sentinel.coordinator.legal_tools import EVIDENCE_GATE, SELECT_PLAN, load_catalogue_plans
from sentinel.coordinator.operator_choice import pending_decision, submit_choice
from sentinel.coordinator.policies import RulePolicy
from sentinel.coordinator.policy import bind_context
from sentinel.coordinator.shadow import MemoryAuditSink

from .harness import AGENT_RUN_ID, RUN_ID, CoordinatorBoundary, FakeWorker, close, envelope, open_harness


def _plan_reference(repo):
    plan = repo.connection.execute("SELECT * FROM plans").fetchone()
    return {
        "plan_id": plan["plan_id"], "catalogue_id": plan["catalogue_id"],
        "catalogue_revision": plan["catalogue_revision"],
        "plan_sha256": plan["plan_sha256"],
        "station_ids": json.loads(plan["station_ids_json"]),
    }


def _create_run(repo, gateway):
    gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"}, sequence=1, actor_type="OPERATOR"))


# --- (a) SELECT_PLAN acting choice --------------------------------------

def test_submit_choice_at_select_plan_produces_plan_events_as_operator(tmp_path):
    repo, gateway = open_harness(tmp_path)
    try:
        _create_run(repo, gateway)
        catalogue = load_catalogue_plans()
        sink = MemoryAuditSink()

        result = submit_choice(repo, gateway, RUN_ID, "PLAN_ROUTE_ONLY_2_ZONE",
                                catalogue=catalogue, sink=sink)
        assert result["status"] == "COMPLETED"

        events = repo.read_events(RUN_ID, 0, 30)
        by_type = {event["event_type"]: event for event in events}
        assert "PLAN_PROPOSED" in by_type
        assert "AGENT_INTENT_PROPOSED" in by_type
        assert by_type["PLAN_PROPOSED"]["actor_type"] == "OPERATOR"
        assert by_type["AGENT_INTENT_PROPOSED"]["actor_type"] == "OPERATOR"

        assert repo.read_state(RUN_ID).to_dict()["run_status"] == "WAITING_FOR_APPROVAL"
        assert sink.records[-1] == {
            "record": "operator_choice", "run_id": RUN_ID, "state_version": 2,
            "decision_point": SELECT_PLAN, "label": "PLAN_ROUTE_ONLY_2_ZONE", "actor_id": "OPERATOR_1",
        }
    finally:
        close(repo)


# --- (b) illegal label -------------------------------------------------

def test_submit_choice_rejects_a_label_outside_the_legal_set(tmp_path):
    repo, gateway = open_harness(tmp_path)
    try:
        _create_run(repo, gateway)
        catalogue = load_catalogue_plans()
        sink = MemoryAuditSink()

        with pytest.raises(CoordinatorError) as caught:
            submit_choice(repo, gateway, RUN_ID, "NOT_A_REAL_LABEL", catalogue=catalogue, sink=sink)
        assert caught.value.code == "INVALID_OPERATOR_CHOICE"
        assert sink.records == []
    finally:
        close(repo)


# --- (c) RULE point never accepts an operator choice --------------------

def test_submit_choice_rejects_any_label_at_a_rule_point(tmp_path):
    repo, gateway = open_harness(tmp_path)
    try:
        _create_run(repo, gateway)
        catalogue = load_catalogue_plans()
        sink = MemoryAuditSink()

        # Move the run to WAIT_APPROVAL -- a RULE point, not a judgment point.
        submit_choice(repo, gateway, RUN_ID, "PLAN_ROUTE_ONLY_2_ZONE", catalogue=catalogue, sink=sink)
        assert repo.read_state(RUN_ID).to_dict()["run_status"] == "WAITING_FOR_APPROVAL"

        with pytest.raises(CoordinatorError) as caught:
            submit_choice(repo, gateway, RUN_ID, "wait", catalogue=catalogue, sink=sink)
        assert caught.value.code == "INVALID_OPERATOR_CHOICE"
    finally:
        close(repo)


# --- (d) idempotency ------------------------------------------------------

def test_submit_choice_is_idempotent_for_the_same_decision(tmp_path, monkeypatch):
    """submit_choice's own idempotency guarantee is the deterministic key
    ``operator-choice-{run_id}-{state_version}-{label}`` plus
    ``expected_state_version`` -- exactly the shape CommandGateway already
    proves idempotent (test_idempotency.py). Once the first call succeeds
    the run has genuinely moved on (a new plan now exists, so the legal set
    at the *current* state no longer offers that label at all) -- so the
    scenario this guards is two submissions racing against the *same*
    decision (e.g. a doubled click before either has landed), both reading
    the same live state. That is reproduced here by pinning
    ``read_decision_state`` to the state as it was at submission time."""
    repo, gateway = open_harness(tmp_path)
    try:
        _create_run(repo, gateway)
        catalogue = load_catalogue_plans()
        sink = MemoryAuditSink()

        import sentinel.coordinator.operator_choice as operator_choice_module

        fixed_state = operator_choice_module.read_decision_state(repo, RUN_ID)
        monkeypatch.setattr(operator_choice_module, "read_decision_state", lambda *_a, **_kw: fixed_state)

        result_1 = submit_choice(repo, gateway, RUN_ID, "PLAN_ROUTE_ONLY_2_ZONE", catalogue=catalogue, sink=sink)
        result_2 = submit_choice(repo, gateway, RUN_ID, "PLAN_ROUTE_ONLY_2_ZONE", catalogue=catalogue, sink=sink)

        assert result_1 == result_2
        events = repo.read_events(RUN_ID, 0, 30)
        assert [event["event_type"] for event in events].count("PLAN_CREATED") == 1
        assert len(sink.records) == 2  # each call still records its own audit line
    finally:
        close(repo)


# --- (e) pending_decision withholds advisor output ------------------------

def test_pending_decision_carries_no_advisor_output(tmp_path):
    repo, gateway = open_harness(tmp_path)
    try:
        _create_run(repo, gateway)
        catalogue = load_catalogue_plans()

        pending = pending_decision(repo, RUN_ID, catalogue)
        assert pending is not None
        assert pending["decision_point"] == SELECT_PLAN
        blob = json.dumps(pending)
        for forbidden in ("advisors", "confidence", "suggested"):
            assert forbidden not in blob
        assert set(pending) == {"decision_point", "options"}
        for option in pending["options"]:
            assert set(option) == {"label", "description"}
    finally:
        close(repo)


def test_pending_decision_is_none_outside_a_judgment_point(tmp_path):
    repo, gateway = open_harness(tmp_path)
    try:
        _create_run(repo, gateway)
        catalogue = load_catalogue_plans()
        sink = MemoryAuditSink()
        submit_choice(repo, gateway, RUN_ID, "PLAN_ROUTE_ONLY_2_ZONE", catalogue=catalogue, sink=sink)

        assert pending_decision(repo, RUN_ID, catalogue) is None  # WAIT_APPROVAL now, not a judgment point
    finally:
        close(repo)


# --- (Task A) EVIDENCE_GATE is no longer a judgment point -----------------

def test_submit_choice_rejects_a_label_at_evidence_gate(tmp_path):
    """EVIDENCE_GATE is now decided by a deterministic rule (Task A), not
    the operator -- pending_decision no longer surfaces it, and a choice
    submitted there is rejected exactly like any other non-judgment point."""
    repo, gateway = open_harness(tmp_path)
    try:
        _create_run(repo, gateway)
        catalogue = load_catalogue_plans()
        sink = MemoryAuditSink()

        submit_choice(repo, gateway, RUN_ID, "PLAN_ROUTE_ONLY_2_ZONE", catalogue=catalogue, sink=sink)
        reference = _plan_reference(repo)
        gateway.submit(envelope("OPERATOR_DECIDE", {
            "decision": "CONFIRM", "target": {"kind": "PLAN", "plan_reference": reference},
        }, sequence=10, actor_type="OPERATOR", expected=repo.read_state(RUN_ID).to_dict()["state_version"]))

        worker = FakeWorker(gateway)
        worker.preflight(RUN_ID, reference["plan_id"], reference["plan_sha256"])
        worker.dispatch(RUN_ID, reference["plan_id"], "LEFT_ALLEY_MIDDLE",
                         repo.read_state(RUN_ID).to_dict()["state_version"])
        worker.capture_and_perceive(RUN_ID, reference["plan_id"], reference["plan_sha256"])

        assert pending_decision(repo, RUN_ID, catalogue) is None  # EVIDENCE_GATE: not a judgment point any more

        with pytest.raises(CoordinatorError) as caught:
            submit_choice(repo, gateway, RUN_ID, "create_task", catalogue=catalogue, sink=sink)
        assert caught.value.code == "INVALID_OPERATOR_CHOICE"
    finally:
        close(repo)


# --- (g) end-to-end: RulePolicy loop + operator choice at SELECT_PLAN only,
# EVIDENCE_GATE decided by the rule (Task A) -------------------------------

def test_end_to_end_rule_loop_reaches_completed_with_operator_choice_only_at_select_plan(tmp_path):
    """The operator is asked to choose only at SELECT_PLAN. At EVIDENCE_GATE
    RulePolicy now acts on legal.rule_choice on its own (0.99, in-plan ->
    create_task) -- no submit_choice happens there, and none is needed."""
    repo, gateway = open_harness(tmp_path)
    try:
        _create_run(repo, gateway)
        boundary = CoordinatorBoundary(repo, gateway)
        catalogue = load_catalogue_plans()
        sink = MemoryAuditSink()
        policy = bind_context(RulePolicy(), repo, catalogue)

        result = AgentLoop(boundary, policy, max_turns=2, repository=repo, catalogue=catalogue).run(
            RUN_ID, AGENT_RUN_ID, "inspect one station")
        assert result["status"] == "NEEDS_REVIEW"  # RulePolicy escalates: SELECT_PLAN has 2 candidates

        submit_choice(repo, gateway, RUN_ID, "PLAN_ROUTE_ONLY_2_ZONE", catalogue=catalogue, sink=sink)
        reference = _plan_reference(repo)
        gateway.submit(envelope("OPERATOR_DECIDE", {
            "decision": "CONFIRM", "target": {"kind": "PLAN", "plan_reference": reference},
        }, sequence=10, actor_type="OPERATOR", expected=repo.read_state(RUN_ID).to_dict()["state_version"]))

        worker = FakeWorker(gateway)
        worker.preflight(RUN_ID, reference["plan_id"], reference["plan_sha256"])
        worker.dispatch(RUN_ID, reference["plan_id"], "LEFT_ALLEY_MIDDLE", repo.read_state(RUN_ID).to_dict()["state_version"])
        worker.capture_and_perceive(RUN_ID, reference["plan_id"], reference["plan_sha256"])

        assert pending_decision(repo, RUN_ID, catalogue) is None  # EVIDENCE_GATE is rule-decided, not asked

        # EVIDENCE_GATE, RANK, ASSIGN all act automatically now.
        result = AgentLoop(boundary, policy, max_turns=4, repository=repo, catalogue=catalogue).run(
            RUN_ID, AGENT_RUN_ID, "inspect one station")
        assert result["error_code"] == "WAITING_FOR_APPROVAL"

        task_id = repo.connection.execute("SELECT task_id FROM tasks").fetchone()[0]
        gateway.submit(envelope("OPERATOR_DECIDE", {
            "decision": "CONFIRM", "target": {"kind": "TASK", "task_id": task_id},
        }, sequence=30, actor_type="OPERATOR", expected=repo.read_state(RUN_ID).to_dict()["state_version"]))

        assert repo.connection.execute("SELECT status FROM runs WHERE run_id = ?", (RUN_ID,)).fetchone()[0] == "COMPLETED"

        events = repo.read_events(RUN_ID, 0, 30)
        assert [event["event_type"] for event in events] == [
            "RUN_CREATED", "USER_MESSAGE_RECEIVED", "PLAN_PROPOSED", "PLAN_CREATED",
            "AGENT_INTENT_PROPOSED", "PLAN_APPROVED", "PREFLIGHT_REPORTED",
            "DRONE_DISPATCH_REQUESTED", "DETECTION_RECORDED", "TASK_CREATED",
            "PATROLLER_ASSIGNMENT_PROPOSED", "PATROLLER_ASSIGNMENT_CONFIRMED",
        ]
        by_type = {event["event_type"]: event for event in events}
        for event_type in ("PLAN_PROPOSED", "PLAN_CREATED", "AGENT_INTENT_PROPOSED"):
            assert by_type[event_type]["actor_type"] == "OPERATOR", event_type
        # TASK_CREATED is now the rule's own decision (through AgentLoop's
        # normal policy path), not an operator choice.
        assert by_type["TASK_CREATED"]["actor_type"] == "AGENT"
        assert by_type["PATROLLER_ASSIGNMENT_PROPOSED"]["actor_type"] == "AGENT"

        # Only one operator_choice record -- SELECT_PLAN. EVIDENCE_GATE is
        # never one, even though it still writes its own audit trail
        # elsewhere (ShadowLadder, exercised in test_shadow.py).
        assert [r["decision_point"] for r in sink.records if r["record"] == "operator_choice"] == [SELECT_PLAN]
    finally:
        close(repo)
