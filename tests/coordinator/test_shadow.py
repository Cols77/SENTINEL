"""P2 Task 2: ``ShadowLadder`` -- the F executor (RulePolicy) always decides
what is returned; advisor policies run only at judgment points, in shadow,
and are recorded to an audit sink but can never change execution."""
from __future__ import annotations

import hashlib
import json
import threading

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
from sentinel.coordinator.operator_choice import submit_choice
from sentinel.coordinator.policies import Decision, RulePolicy
from sentinel.coordinator.policy import PolicyContext, bind_context
from sentinel.coordinator.shadow import JsonlAuditSink, MemoryAuditSink, ShadowLadder

from .harness import (
    AGENT_RUN_ID,
    ARTIFACT_ID,
    RESULT_ID,
    RUN_ID,
    CoordinatorBoundary,
    FakeWorker,
    close,
    envelope,
    open_harness,
)
from .p3_helpers import LEFT_MID, P3Run


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


def _evidence_gate_legal(rule_choice: str | None = "create_task") -> LegalSet:
    """Defaults to the rule already having decided ``create_task`` (Task A:
    0.99, in-plan), matching the harness's own default perception fixture."""
    create = Candidate("task.create", {"perception_result_id": RESULT_ID, "section_id": "LEFT_ALLEY_MIDDLE",
                                        "evidence_artifact_id": ARTIFACT_ID}, "create_task",
                        "create a patrol task for this detection")
    escalate = Candidate("finish", {"status": "NEEDS_REVIEW", "summary": "review"}, ESCALATE_LABEL,
                          "hand the decision to the operator with the reason")
    return LegalSet(EVIDENCE_GATE, (create, escalate), rule_choice=rule_choice)


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


class _BlockingAdvisor:
    """Remain in flight until the test releases this advisor.

    A fixed sleep is not a reliable timeout fixture because the shadow
    deadline deliberately starts after the executor returns.  On a loaded
    machine the advisor can finish while the executor is running.
    """

    def __init__(self) -> None:
        self.last: Decision | None = None
        self.calls = 0
        self.started = threading.Event()
        self._release = threading.Event()

    def decide(self, request: dict, context: PolicyContext) -> dict:
        self.calls += 1
        self.started.set()
        self._release.wait()
        self.last = Decision(chooser="JEV", label="create_task", confidence=0.99, band="HIGH",
                              legal_labels=context.legal.labels, model="blocking-test", error=None, latency_ms=9999)
        return {"ignored": True}

    def release(self) -> None:
        self._release.set()


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


class _ConcurrencyProbeAdvisor:
    """Its first ``decide()`` call hangs (past any reasonable timeout) until
    released; every call counts how many ``decide()`` invocations were ever
    concurrently in flight on this *same instance*. Used to prove that a
    straggling, timed-out call can never run alongside a later call to the
    same advisor instance."""

    def __init__(self) -> None:
        self.last: Decision | None = None
        self.calls = 0
        self.max_concurrent = 0
        self._active = 0
        self._lock = threading.Lock()
        self._hang = threading.Event()
        self.second_call_finished = threading.Event()

    def release(self) -> None:
        self._hang.set()

    def decide(self, request: dict, context: PolicyContext) -> dict:
        with self._lock:
            self.calls += 1
            call_no = self.calls
            self._active += 1
            self.max_concurrent = max(self.max_concurrent, self._active)
        try:
            if call_no == 1:
                self._hang.wait(timeout=5.0)
            candidate = context.legal.candidates[0]
            self.last = Decision(chooser="JEV", label=candidate.label, confidence=0.9, band="HIGH",
                                  legal_labels=context.legal.labels, model=f"probe-call-{call_no}",
                                  error=None, latency_ms=1)
            return {"ignored": True}
        finally:
            with self._lock:
                self._active -= 1
            if call_no == 2:
                self.second_call_finished.set()


class _CreateTaskAdvisor:
    """Always confidently picks ``create_task`` -- the opposite of what
    RulePolicy does at a DUPLICATE-detection EVIDENCE_GATE turn (it
    escalates, since ``rule_choice`` is ``None`` there) -- proves the
    ladder's returned result is the executor's regardless of what an
    advisor 'wants'."""

    def __init__(self) -> None:
        self.last: Decision | None = None
        self.calls = 0

    def decide(self, request: dict, context: PolicyContext) -> dict:
        self.calls += 1
        candidate = context.legal.by_label("create_task")
        self.last = Decision(chooser="JEV", label=candidate.label, confidence=0.97, band="HIGH",
                              legal_labels=context.legal.labels, model="duplicate-test", error=None, latency_ms=1)
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
        "timeout": lambda: _BlockingAdvisor(),
    }
    for name, make_advisor in scenarios.items():
        sink = MemoryAuditSink()
        advisor = make_advisor()

        def _wrap(executor, _advisor=advisor, _sink=sink):
            return ShadowLadder(executor, [("bad", _advisor)], _sink, advisor_timeout_s=0.05)

        try:
            events, result = _run_select_plan_only(tmp_path / name, _wrap)
            if isinstance(advisor, _BlockingAdvisor):
                assert advisor.started.wait(timeout=1.0), "timeout advisor was not invoked"
            assert events == baseline_events, name
            assert result == baseline_result, name
            # the ladder actually ran the advisor and swallowed whatever it did
            assert advisor.calls == 1, name
            assert sink.records[0]["advisors"]["bad"]["error"] is not None or name == "opposite", name
        finally:
            if isinstance(advisor, _BlockingAdvisor):
                advisor.release()


def test_timed_out_advisor_call_never_races_a_later_call_on_the_same_instance():
    """Regression test: advisor instances are constructed once and reused
    across multiple judgment points in the same run (e.g. SELECT_PLAN then
    EVIDENCE_GATE). If a first call times out, its worker thread keeps
    running in the background -- it must never run *concurrently* with a
    later ``decide()`` call on that same advisor instance, or the
    straggler's eventual write to the shared ``.last`` attribute can land
    after (and clobber) the later call's own write, corrupting that later
    call's audit record with stale data."""
    advisor = _ConcurrencyProbeAdvisor()
    sink = MemoryAuditSink()
    ladder = ShadowLadder(RulePolicy(), [("x", advisor)], sink, advisor_timeout_s=0.05)

    # Call 1 (SELECT_PLAN): the advisor hangs well past the timeout, so the
    # ladder gives up and moves on -- but the thread keeps running.
    ladder.decide(_request(), PolicyContext(state=_dummy_state(1), legal=_select_plan_legal()))
    assert sink.records[0]["advisors"]["x"]["error"] is not None

    # Call 2 (EVIDENCE_GATE), same advisor instance, issued immediately while
    # call 1's advisor.decide() is still running in the background.
    ladder.decide(_request(), PolicyContext(state=_dummy_state(2), legal=_evidence_gate_legal()))

    advisor.release()
    assert advisor.second_call_finished.wait(timeout=5.0), "call 2 never completed"
    assert advisor.max_concurrent == 1, \
        "the same advisor instance ran two decide() calls concurrently -- .last is no longer safe to read"


def test_non_interference_advisor_cannot_change_a_duplicate_detection_gate(tmp_path):
    """P3a's DUPLICATE-detection judgment at EVIDENCE_GATE (``rule_choice`` is
    ``None`` because ``facts["duplicates_open_task"]`` holds) had no
    non-interference test of its own -- it leaned on P2b's plain EVIDENCE_GATE
    proof (no duplicate involved) and on the near-tie ASSIGN test. Modeled on
    ``test_non_interference_advisor_cannot_change_a_near_tie_assignment`` in
    ``test_assignment_choice.py``: an advisor that confidently picks
    ``create_task`` at a duplicate turn must still run and be recorded, while
    the executor -- which escalates, since a duplicate is a real judgment
    point -- is the only chooser of what actually executes."""
    run = P3Run(tmp_path, "evidence-gate-duplicate-shadow")
    try:
        run.approve_route_plan()
        first = run.ingest("a", LEFT_MID, 0.90)
        run.ingest("b", LEFT_MID, 0.92)   # same station, still pending
        run.create_task(first)            # opens a task at LEFT_MID -- "b" now duplicates it

        boundary = CoordinatorBoundary(run.repo, run.gateway)
        catalogue = load_catalogue_plans()
        executor = RulePolicy()
        advisor = _CreateTaskAdvisor()
        sink = MemoryAuditSink()
        ladder = ShadowLadder(executor, [("optimistic", advisor)], sink)
        policy = bind_context(ladder, run.repo, catalogue)

        result = AgentLoop(boundary, policy, max_turns=2, repository=run.repo, catalogue=catalogue).run(
            run.run_id, AGENT_RUN_ID, "route-only survey of the alley stations")

        assert result["status"] == "NEEDS_REVIEW"    # the executor's own decision, unaffected by the advisor
        assert advisor.calls == 1                    # the advisor actually ran

        judgment = next(r for r in sink.records if r["record"] == "judgment" and r["decision_point"] == EVIDENCE_GATE)
        assert judgment["rule_choice"] is None                                       # a real judgment point
        assert judgment["executor"] == {"chooser": "OPERATOR", "label": "escalate"}  # execution was the executor's choice
        assert judgment["advisors"]["optimistic"]["label"] == "create_task"          # audit captured the advisor's differing pick
    finally:
        run.close()


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
    assert select_plan_record["rule_choice"] is None  # SELECT_PLAN: no rule computes one
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
    # Task A: the rule already decided create_task (fixture is 0.99, in-plan)
    # -- RulePolicy acts on it directly, chooser RULE, not OPERATOR.
    assert evidence_gate_record["rule_choice"] == "create_task"
    assert evidence_gate_record["executor"] == {"chooser": "RULE", "label": "create_task"}

    rule_record = sink.records[2]
    assert rule_record == {
        "record": "rule", "run_id": RUN_ID, "state_version": 3,
        "decision_point": RANK, "label": "rank",
    }
    assert "advisors" not in rule_record


# --- Task A: full flow -- RulePolicy completes without an operator choice
# at EVIDENCE_GATE, advisors are still consulted there and recorded --------

def test_full_one_station_flow_with_shadow_ladder_reaches_task_creation_without_an_evidence_gate_choice(tmp_path):
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"}, sequence=1, actor_type="OPERATOR"))
        boundary = CoordinatorBoundary(repo, gateway)
        catalogue = load_catalogue_plans()
        sink = MemoryAuditSink()
        jev = _CountingAdvisor("create_task")
        llm = _CountingAdvisor("create_task")
        ladder = ShadowLadder(RulePolicy(), [("jev", jev), ("llm", llm)], sink, advisor_timeout_s=1.0)
        policy = bind_context(ladder, repo, catalogue)

        # SELECT_PLAN still escalates -- 2 catalogue plans, no rule_choice.
        result = AgentLoop(boundary, policy, max_turns=2, repository=repo, catalogue=catalogue).run(
            RUN_ID, AGENT_RUN_ID, "inspect one station")
        assert result["status"] == "NEEDS_REVIEW"

        submit_choice(repo, gateway, RUN_ID, "PLAN_ROUTE_ONLY_2_ZONE", catalogue=catalogue, sink=sink)
        plan = repo.connection.execute("SELECT * FROM plans").fetchone()
        reference = {
            "plan_id": plan["plan_id"], "catalogue_id": plan["catalogue_id"],
            "catalogue_revision": plan["catalogue_revision"], "plan_sha256": plan["plan_sha256"],
            "station_ids": json.loads(plan["station_ids_json"]),
        }
        gateway.submit(envelope("OPERATOR_DECIDE", {
            "decision": "CONFIRM", "target": {"kind": "PLAN", "plan_reference": reference},
        }, sequence=10, actor_type="OPERATOR", expected=repo.read_state(RUN_ID).to_dict()["state_version"]))

        worker = FakeWorker(gateway)
        worker.preflight(RUN_ID, reference["plan_id"], reference["plan_sha256"])
        worker.dispatch(RUN_ID, reference["plan_id"], "LEFT_ALLEY_MIDDLE", repo.read_state(RUN_ID).to_dict()["state_version"])
        worker.capture_and_perceive(RUN_ID, reference["plan_id"], reference["plan_sha256"])  # 0.99, in-plan

        # EVIDENCE_GATE, RANK, ASSIGN all act automatically -- the rule
        # decides create_task on its own; no operator choice is needed.
        result = AgentLoop(boundary, policy, max_turns=4, repository=repo, catalogue=catalogue).run(
            RUN_ID, AGENT_RUN_ID, "inspect one station")
        assert result["error_code"] == "WAITING_FOR_APPROVAL"

        task = repo.connection.execute("SELECT task_id, status FROM tasks").fetchone()
        assert task is not None and task["status"] == "ASSIGNMENT_PROPOSED"

        operator_choice_records = [r for r in sink.records if r["record"] == "operator_choice"]
        assert [r["decision_point"] for r in operator_choice_records] == [SELECT_PLAN]  # never EVIDENCE_GATE

        judgment_records = [r for r in sink.records if r["record"] == "judgment"]
        evidence_gate_records = [r for r in judgment_records if r["decision_point"] == EVIDENCE_GATE]
        assert len(evidence_gate_records) == 1  # advisors are still called at EVIDENCE_GATE, exactly once
        assert evidence_gate_records[0]["rule_choice"] == "create_task"
        assert evidence_gate_records[0]["executor"] == {"chooser": "RULE", "label": "create_task"}
        assert evidence_gate_records[0]["advisors"]["jev"]["label"] == "create_task"
        assert evidence_gate_records[0]["advisors"]["llm"]["label"] == "create_task"
        assert evidence_gate_records[0]["operator_choice"] is None
    finally:
        close(repo)


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
