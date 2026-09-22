"""One dispatched read per turn; failures and facts replay from the ledger."""
from copy import deepcopy
import builtins

import pytest

from sentinel.room.controller import RoomController, TurnOutcome
from sentinel.room.ingest import ingest_result
from sentinel.room.models import CandidateKind, GoalKind, TerminalState
from sentinel.room.queries import ReadQueries
from tests.room.test_ingest import sample
from tests.room.test_queries import append, drone, goal, room, survey


class Judge:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def choice(self, state, instruction, *, criteria, name):
        self.calls.append((deepcopy(state), dict(criteria)))
        response = self.responses.pop(0)
        if callable(response):
            response = response(state, criteria)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, dict):
            return response
        assert response in criteria
        return {"value": response, "probabilities": {k: .99 if k == response else .001 for k in criteria}}


def test_drone_question_read_then_grounded_answer_without_provider_construction(room, monkeypatch):
    goal(room)
    source = drone(room)
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        assert not name.startswith(("sentinel.flight", "sentinel.survey.providers", "djitellopy"))
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    judge = Judge("READ_DRONE", "ANSWER")
    controller = RoomController(room, judge)
    first = controller.step("s1")
    assert isinstance(first, TurnOutcome) and first.accepted and first.terminal_state is None
    assert controller.run_until_terminal("s1") == TerminalState.COMPLETED
    last = room.events("s1")[-1]
    assert last.event_type == "ROOM_TOOL_OUTCOME"
    fact = judge.calls[1][0]["facts"][0]
    for value in (source.event_id, fact["fact_id"], fact["observed_at"], fact["scope"], fact["uncertainty"]):
        assert value in last.payload["answer"]
    assert "73" in last.payload["answer"]
    assert controller.step("s1").terminal_state == TerminalState.COMPLETED
    assert len(judge.calls) == 2


@pytest.mark.parametrize("kind,read", [(GoalKind.FIND_OBSERVE, "READ_OBSERVATIONS"),
                                      (GoalKind.COUNT, "READ_COUNTS")])
def test_persisted_observation_and_count_question_lifecycles(room, tmp_path, kind, read):
    goal(room, kind)
    legacy = None
    kwargs = {}
    if kind == GoalKind.COUNT:
        legacy, _, _ = survey(tmp_path)
        kwargs = {"survey_store": legacy, "survey_run_id": "run1"}
    else:
        ingest_result(room, sample(tmp_path, snapshot=room.session_version("s1")),
                      current_snapshot_version=room.session_version("s1"))
    try:
        judge = Judge(read, "ANSWER")
        assert RoomController(room, judge, **kwargs).run_until_terminal("s1") == TerminalState.COMPLETED
        assert "computer" in room.events("s1")[-1].payload["answer"]
    finally:
        if legacy:
            legacy.connection.close()


def test_missing_evidence_read_then_clarification_without_fabrication(room):
    goal(room)
    judge = Judge("READ_DRONE", "CLARIFY")
    controller = RoomController(room, judge)
    assert controller.run_until_terminal("s1") == TerminalState.BLOCKED
    assert judge.calls[1][0]["outcomes"][-1]["code"] == "NO_EVIDENCE"
    assert "ANSWER" not in judge.calls[1][1]
    assert "READ_DRONE" not in judge.calls[1][1]
    assert room.events("s1")[-1].payload["facts"] == []


def test_stale_decision_is_audited_and_never_dispatched(room):
    goal(room)
    drone(room)
    def concurrent(state, criteria):
        append(room, "CONCURRENT_CHANGE", {})
        return {"value": "READ_DRONE", "probabilities": {"READ_DRONE": .99}}
    judge = Judge(concurrent, "CLARIFY")
    executions = []
    controller = RoomController(room, judge, executor=lambda c, s: executions.append(c))
    outcome = controller.step("s1")
    assert not outcome.accepted and executions == []
    assert room.events("s1")[-1].payload["code"] == "STALE_DECISION"
    controller.step("s1")
    assert judge.calls[1][0]["outcomes"][-1]["code"] == "STALE_DECISION"


@pytest.mark.parametrize("response,code", [
    ({"value": "TAKEOFF", "probabilities": {"TAKEOFF": .99}}, "ILLEGAL_OR_MALFORMED"),
    ({"value": "READ_DRONE", "confidence": .4, "probabilities": {"READ_DRONE": .99}}, "LOW_CONFIDENCE"),
    (RuntimeError("private provider detail"), "PROVIDER_ERROR"),
])
def test_gate_refusals_and_provider_errors_are_audited_and_in_next_snapshot(room, response, code):
    goal(room)
    judge = Judge(response, "CLARIFY")
    controller = RoomController(room, judge)
    outcome = controller.step("s1")
    assert not outcome.accepted and outcome.terminal_state is None
    events = room.events("s1")[-2:]
    assert [e.event_type for e in events] == ["ROOM_CHOICE_ATTEMPT", "ROOM_TOOL_OUTCOME"]
    assert events[-1].payload["code"] == code
    assert events[-1].payload["refusal_code"] == code
    assert "private provider detail" not in str([e.payload for e in events])
    assert controller.run_until_terminal("s1") == TerminalState.BLOCKED
    assert judge.calls[1][0]["outcomes"][-1]["code"] == code


def test_tool_failure_suppresses_read_until_evidence_changes(room):
    goal(room)
    calls = []
    def fail(candidate, snapshot):
        calls.append(candidate.candidate_id)
        raise OSError("private tool detail")
    judge = Judge("READ_DRONE", "CLARIFY")
    controller = RoomController(room, judge, executor=fail)
    assert not controller.step("s1").accepted
    assert calls == ["READ_DRONE"]
    snapshot = ReadQueries(room).project("s1")
    assert snapshot.outcomes[-1].code == "TOOL_FAILURE"
    assert controller.run_until_terminal("s1") == TerminalState.BLOCKED
    assert "READ_DRONE" not in judge.calls[1][1]


def test_explicit_escalation_and_repeated_illegal_choice_terminate_deterministically(room):
    goal(room)
    illegal = {"value": "TAKEOFF", "probabilities": {"TAKEOFF": .99}}
    judge = Judge(illegal, illegal)
    assert RoomController(room, judge).run_until_terminal("s1") == TerminalState.ESCALATED
    assert len(judge.calls) == 2
    assert room.events("s1")[-1].payload["code"] == "NO_PROGRESS"


def test_explicit_escalation_is_an_accepted_terminal_choice(room):
    goal(room)
    outcome = RoomController(room, Judge("ESCALATE")).step("s1")
    assert outcome.accepted and outcome.terminal_state == TerminalState.ESCALATED


def test_pending_ingestion_precedes_snapshot_and_terminal_has_no_extra_judge_calls(room):
    goal(room)
    invoked = []
    def ingest(session):
        invoked.append(session)
        if len(invoked) == 1:
            drone(room, 64)
    judge = Judge("READ_DRONE", "ANSWER")
    assert RoomController(room, judge, ingest_pending=ingest).run_until_terminal("s1") == TerminalState.COMPLETED
    assert judge.calls[0][0]["state_version"] == 2
    assert "64" in room.events("s1")[-1].payload["answer"]


def test_parser_rejection_is_mandatory_clarification_without_judge(room):
    append(room, "GOAL_COMPILE_REJECTED", {"status": "CLARIFICATION_REQUIRED", "reason": "AMBIGUOUS"})
    judge = Judge()
    result = RoomController(room, judge).step("s1")
    assert result.terminal_state == TerminalState.BLOCKED and judge.calls == []


def test_external_event_after_attempt_before_dispatch_is_stale(room, monkeypatch):
    goal(room)
    original = room.append_events
    def append_then_concurrent(**kwargs):
        result = original(**kwargs)
        if kwargs["events"][0]["event_type"] == "ROOM_CHOICE_ATTEMPT":
            append(room, "CONCURRENT_CHANGE", {})
        return result
    monkeypatch.setattr(room, "append_events", append_then_concurrent)
    calls = []
    result = RoomController(room, Judge("READ_DRONE"), executor=lambda c, s: calls.append(c)).step("s1")
    assert not result.accepted and calls == []
    assert room.events("s1")[-1].payload["code"] == "STALE_DECISION"


def test_changing_illegal_labels_cannot_evade_no_progress_detection(room):
    goal(room)
    judge = Judge({"value": "ILLEGAL_ONE", "probabilities": {"ILLEGAL_ONE": .99}},
                  {"value": "ILLEGAL_TWO", "probabilities": {"ILLEGAL_TWO": .99}})
    assert RoomController(room, judge).run_until_terminal("s1") == TerminalState.ESCALATED
    assert len(judge.calls) == 2


def test_no_arbitrary_call_cap_when_relevant_evidence_keeps_changing(room):
    goal(room)
    turns = []
    def ingest(session):
        if len(turns) < 15:
            turns.append(len(turns))
            drone(room, 90 - len(turns))
    judge = Judge(*(["READ_DRONE"] * 15), "ANSWER")
    assert RoomController(room, judge, ingest_pending=ingest).run_until_terminal("s1") == TerminalState.COMPLETED
    assert len(judge.calls) == 16
    assert "75" in room.events("s1")[-1].payload["answer"]


def test_new_perception_evidence_without_event_version_change_invalidates_choice(room, tmp_path):
    goal(room, GoalKind.FIND_OBSERVE)
    def concurrent(state, criteria):
        ingest_result(room, sample(tmp_path, snapshot=state["state_version"]),
                      current_snapshot_version=state["state_version"])
        return {"value": "READ_OBSERVATIONS", "probabilities": {"READ_OBSERVATIONS": .99}}
    dispatched = []
    result = RoomController(room, Judge(concurrent), executor=lambda c, s: dispatched.append(c)).step("s1")
    assert not result.accepted and dispatched == []
    assert room.events("s1")[-1].payload["code"] == "STALE_DECISION"


def test_executor_cannot_invent_or_modify_persisted_facts(room):
    from dataclasses import replace
    goal(room)
    drone(room)
    def fabricate(candidate, snapshot):
        fact = snapshot.sources[0]
        return (replace(fact, values=(("battery_percent", 999),)),)
    result = RoomController(room, Judge("READ_DRONE"), executor=fabricate).step("s1")
    assert not result.accepted
    assert room.events("s1")[-1].payload["code"] == "TOOL_FAILURE"
    assert ReadQueries(room).project("s1").facts == ()


def test_answer_is_rejected_if_evidence_changes_during_jev_choice(room):
    goal(room)
    drone(room)
    def change(state, criteria):
        drone(room, 41)
        return {"value": "ANSWER", "probabilities": {"ANSWER": .99}}
    controller = RoomController(room, Judge("READ_DRONE", change, "READ_DRONE", "ANSWER"))
    controller.step("s1")
    stale = controller.step("s1")
    assert not stale.accepted and stale.terminal_state is None
    assert "answer" not in room.events("s1")[-1].payload
    assert controller.run_until_terminal("s1") == TerminalState.COMPLETED
    assert "41" in room.events("s1")[-1].payload["answer"]


def test_refusals_persist_across_controller_restart_and_allow_an_alternative_read(room):
    goal(room)
    def fail(candidate, snapshot):
        raise OSError("read failed")
    RoomController(room, Judge("READ_DRONE"), executor=fail).step("s1")
    judge = Judge("READ_OBSERVATIONS", "CLARIFY")
    controller = RoomController(room, judge)
    assert controller.run_until_terminal("s1") == TerminalState.BLOCKED
    assert "READ_DRONE" not in judge.calls[0][1]
    assert judge.calls[0][0]["outcomes"][-1]["code"] == "TOOL_FAILURE"


def test_physical_candidate_from_injected_builder_cannot_be_dispatched(room):
    from dataclasses import replace
    from sentinel.room.candidates import build_candidates
    goal(room, GoalKind.FOLLOW)
    def inject(snapshot):
        safe = build_candidates(snapshot)
        return (*safe, replace(safe[0], kind=CandidateKind.FOLLOW_STEP, candidate_id="FOLLOW_STEP"))
    judge = Judge("CLARIFY")
    result = RoomController(room, judge, candidate_builder=inject).step("s1")
    assert result.terminal_state == TerminalState.BLOCKED
    assert "FOLLOW_STEP" not in judge.calls[0][1]


def test_repeated_provider_failure_terminates_without_retry_cap(room):
    goal(room)
    judge = Judge(RuntimeError("one"), OSError("two"))
    assert RoomController(room, judge).run_until_terminal("s1") == TerminalState.PROVIDER_FAILURE
    assert len(judge.calls) == 2


def test_refused_terminal_choices_force_mandatory_transition_without_extra_call(room):
    goal(room)
    responses = [{"value": kind, "probabilities": {kind: .99}, "confidence": .1}
                 for kind in ("READ_DRONE", "READ_OBSERVATIONS", "READ_COUNTS", "CLARIFY", "ESCALATE")]
    judge = Judge(*responses)
    assert RoomController(room, judge).run_until_terminal("s1") == TerminalState.BLOCKED
    assert len(judge.calls) == 5
    assert room.events("s1")[-1].payload["code"] == "NO_LEGAL_CANDIDATES"


def test_source_revalidation_failure_is_a_typed_tool_outcome(room):
    goal(room)
    class FailingRevalidation(ReadQueries):
        def evidence_is_current(self, snapshot, kind):
            raise OSError("private read failure")
    judge = Judge("READ_DRONE", "CLARIFY")
    controller = RoomController(room, judge, queries=FailingRevalidation(room))
    result = controller.step("s1")
    assert not result.accepted and result.terminal_state is None
    assert room.events("s1")[-1].payload["code"] == "TOOL_FAILURE"
    assert len(result.event_ids) == 2
    assert controller.run_until_terminal("s1") == TerminalState.BLOCKED
    assert judge.calls[1][0]["outcomes"][-1]["code"] == "TOOL_FAILURE"
