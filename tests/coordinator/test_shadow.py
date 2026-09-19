"""P2 Task 2: ``ShadowLadder`` -- the F executor (RulePolicy) always decides
what is returned; advisor policies run only at judgment points, in shadow,
and are recorded to an audit sink but can never change execution."""
from __future__ import annotations

import hashlib
import json
import time

from sentinel.coordinator.agent_loop import AgentLoop
from sentinel.coordinator.decision_state import DecisionState
from sentinel.coordinator.legal_tools import (
    ESCALATE_LABEL,
    EVIDENCE_GATE,
    RANK,
    SELECT_PLAN,
    Candidate,
    LegalSet,
    load_catalogue_plans,
)
from sentinel.coordinator.policies import Decision, RulePolicy
from sentinel.coordinator.policy import PolicyContext, bind_context
from sentinel.coordinator.shadow import JsonlAuditSink, MemoryAuditSink, ShadowLadder

from .harness import (
    AGENT_RUN_ID,
    ARTIFACT_ID,
    RESULT_ID,
    RUN_ID,
    CoordinatorBoundary,
    close,
    envelope,
    open_harness,
)


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
    specific field values are not under test."""
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


def _evidence_gate_legal() -> LegalSet:
    create = Candidate("task.create", {"perception_result_id": RESULT_ID, "section_id": "LEFT_ALLEY_MIDDLE",
                                        "evidence_artifact_id": ARTIFACT_ID}, "create_task",
                        "create a patrol task for this detection")
    escalate = Candidate("finish", {"status": "NEEDS_REVIEW", "summary": "review"}, ESCALATE_LABEL,
                          "hand the decision to the operator with the reason")
    return LegalSet(EVIDENCE_GATE, (create, escalate))


_TASK_ID = "77777777-7777-4777-8777-777777777777"


def _rank_legal() -> LegalSet:
    return LegalSet(RANK, (Candidate("patroller.rank_for_task", {"task_id": _TASK_ID}, "rank",
                                      "rank patrollers for the task"),))


# --- advisor test doubles ---------------------------------------------------

class _OppositeAdvisor:
    """Always picks the legal set's last candidate, so its pick differs from
    whatever the executor (RulePolicy) would pick -- proves the ladder's
    returned result is unaffected regardless of what an advisor 'wants'."""

    def __init__(self) -> None:
        self.last: Decision | None = None
        self.calls = 0

    def decide(self, request: dict, context: PolicyContext) -> dict:
        self.calls += 1
        candidate = context.legal.candidates[-1]
        self.last = Decision(chooser="JEV", label=candidate.label, confidence=0.99, band="HIGH",
                              legal_labels=context.legal.labels, model="opposite-test", error=None, latency_ms=1)
        return {"ignored": True}


class _RaisingAdvisor:
    def __init__(self) -> None:
        self.last: Decision | None = None
        self.calls = 0

    def decide(self, request: dict, context: PolicyContext) -> dict:
        self.calls += 1
        raise RuntimeError("advisor exploded")


class _SleepyAdvisor:
    def __init__(self, sleep_s: float) -> None:
        self.last: Decision | None = None
        self.calls = 0
        self.sleep_s = sleep_s

    def decide(self, request: dict, context: PolicyContext) -> dict:
        self.calls += 1
        time.sleep(self.sleep_s)
        self.last = Decision(chooser="JEV", label="create_task", confidence=0.99, band="HIGH",
                              legal_labels=context.legal.labels, model="sleepy-test", error=None, latency_ms=9999)
        return {"ignored": True}


class _CountingAdvisor:
    def __init__(self, label: str, confidence: float = 0.9, model: str = "advisor-test") -> None:
        self.last: Decision | None = None
        self.calls = 0
        self.label = label
        self.confidence = confidence
        self.model = model

    def decide(self, request: dict, context: PolicyContext) -> dict:
        self.calls += 1
        label = self.label if self.label in context.legal.labels else context.legal.candidates[0].label
        candidate = context.legal.by_label(label)
        band = "HIGH" if self.confidence >= 0.8 else "MID"
        self.last = Decision(chooser="JEV", label=candidate.label, confidence=self.confidence, band=band,
                              legal_labels=context.legal.labels, model=self.model, error=None, latency_ms=5)
        return {"ignored": True}


# --- (a) non-interference: drive the real one-station flow -----------------

def _run_select_plan_only(tmp_path, wrap):
    """wrap(executor) -> ContextualPolicy used as the AgentLoop policy, or
    None to run the bare executor with no shadow ladder at all."""
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"}, sequence=1, actor_type="OPERATOR"))
        boundary = CoordinatorBoundary(repo, gateway)
        catalogue = load_catalogue_plans()
        executor = RulePolicy()
        contextual = wrap(executor) if wrap is not None else executor
        policy = bind_context(contextual, repo, catalogue)
        result = AgentLoop(boundary, policy, max_turns=2, repository=repo, catalogue=catalogue).run(
            RUN_ID, AGENT_RUN_ID, "inspect one station")
        events = [event["event_type"] for event in repo.read_events(RUN_ID, 0, 30)]
        return events, result
    finally:
        close(repo)


def test_non_interference_opposite_raising_and_timeout_advisors(tmp_path):
    baseline_events, baseline_result = _run_select_plan_only(tmp_path / "baseline", None)
    assert baseline_result["status"] == "NEEDS_REVIEW"  # RulePolicy always escalates at SELECT_PLAN (2 plans)

    scenarios = {
        "opposite": lambda: _OppositeAdvisor(),
        "raising": lambda: _RaisingAdvisor(),
        "sleepy": lambda: _SleepyAdvisor(sleep_s=0.3),
    }
    for name, make_advisor in scenarios.items():
        sink = MemoryAuditSink()
        advisor = make_advisor()

        def _wrap(executor, _advisor=advisor, _sink=sink):
            return ShadowLadder(executor, [("bad", _advisor)], _sink, advisor_timeout_s=0.05)

        events, result = _run_select_plan_only(tmp_path / name, _wrap)
        assert events == baseline_events, name
        assert result == baseline_result, name
        # the ladder actually ran the advisor and swallowed whatever it did
        assert advisor.calls == 1, name
        assert sink.records[0]["advisors"]["bad"]["error"] is not None or name == "opposite", name


# --- (b)/(c) advisors called only at judgment points, audit shape ----------

def test_advisors_called_only_at_judgment_points_and_audit_records():
    sink = MemoryAuditSink()
    jev = _CountingAdvisor("PLAN_ROUTE_ONLY_2_ZONE")
    llm = _CountingAdvisor("create_task")
    ladder = ShadowLadder(RulePolicy(), [("jev", jev), ("llm", llm)], sink, advisor_timeout_s=1.0)

    request = _request()
    ladder.decide(request, PolicyContext(state=_dummy_state(1), legal=_select_plan_legal()))
    ladder.decide(request, PolicyContext(state=_dummy_state(2), legal=_evidence_gate_legal()))
    ladder.decide(request, PolicyContext(state=_dummy_state(3), legal=_rank_legal()))

    assert jev.calls == 2  # SELECT_PLAN and EVIDENCE_GATE only, not RANK
    assert llm.calls == 2

    kinds = [record["record"] for record in sink.records]
    assert kinds == ["judgment", "judgment", "rule"]

    select_plan_record = sink.records[0]
    assert select_plan_record["decision_point"] == SELECT_PLAN
    assert select_plan_record["operator_choice"] is None
    assert set(select_plan_record["advisors"]) == {"jev", "llm"}
    assert select_plan_record["advisors"]["jev"]["label"] == "PLAN_ROUTE_ONLY_2_ZONE"
    assert select_plan_record["advisors"]["jev"]["error"] is None
    assert select_plan_record["advisors"]["jev"]["band"] == "HIGH"
    # RulePolicy sees 3 candidates (2 plans + escalate) so it escalates
    assert select_plan_record["executor"] == {"chooser": "OPERATOR", "label": ESCALATE_LABEL}
    assert set(select_plan_record["legal_labels"]) == set(_select_plan_legal().labels)

    evidence_gate_record = sink.records[1]
    assert evidence_gate_record["decision_point"] == EVIDENCE_GATE
    assert evidence_gate_record["advisors"]["llm"]["label"] == "create_task"

    rule_record = sink.records[2]
    assert rule_record == {
        "record": "rule", "run_id": RUN_ID, "state_version": 3,
        "decision_point": RANK, "label": "rank",
    }
    assert "advisors" not in rule_record


# --- (d) packet_sha256 --------------------------------------------------

def test_packet_sha256_is_sha256_of_canonical_json_of_to_packet():
    sink = MemoryAuditSink()
    ladder = ShadowLadder(RulePolicy(), [], sink)
    state = _dummy_state(state_version=9)

    ladder.decide(_request(), PolicyContext(state=state, legal=_evidence_gate_legal()))

    record = sink.records[0]
    expected = hashlib.sha256(
        json.dumps(state.to_packet(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert record["packet_sha256"] == expected


# --- (e) JsonlAuditSink append-only, never truncates ------------------------

def test_jsonl_audit_sink_appends_valid_json_lines_and_never_truncates(tmp_path):
    path = tmp_path / "audit" / "run.jsonl"
    sink_a = JsonlAuditSink(path)
    sink_a.write({"record": "rule", "n": 1})

    sink_b = JsonlAuditSink(path)  # reopening the same path must not truncate
    sink_b.write({"record": "rule", "n": 2})
    sink_a.write({"record": "rule", "n": 3})

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    assert [entry["n"] for entry in parsed] == [1, 2, 3]
