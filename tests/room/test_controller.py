"""One dispatched read per turn; failures and facts replay from the ledger."""
from copy import deepcopy
import builtins
from dataclasses import replace
import sqlite3

import pytest

from sentinel.room.controller import RoomController, TurnOutcome
from sentinel.room.ingest import ingest_result
from sentinel.room.models import CandidateKind, GoalKind, TerminalState
from sentinel.room.queries import ReadQueries
from sentinel.cube.localization import LocalizationState
from sentinel.cube.step_executor import ExecutorState, TerminalOutcome
from tests.room.test_ingest import sample
from tests.room.test_queries import append, drone, goal, room, survey
from tests.room.test_supervisor import Executor, safety


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


@pytest.mark.parametrize("initial", ["lost", "refresh_failure"])
def test_permanent_ledger_failure_returns_local_terminal_on_two_steps_and_controller_restart(safety, monkeypatch, initial):
    supervisor, store, clock, samples, refreshes, _ = safety
    if initial == "lost":
        samples["pose"] = replace(samples["pose"], state=LocalizationState.LOST)
    else:
        supervisor.refresh = lambda: (_ for _ in ()).throw(RuntimeError("telemetry unavailable"))
    monkeypatch.setattr(store, "append_events", lambda **kwargs: (_ for _ in ()).throw(
        sqlite3.OperationalError("disk I/O error private-path/private-secret")))
    emergencies = []

    def emergency(executor):
        emergencies.append(True)
        executor.state = ExecutorState.TERMINAL
        return TerminalOutcome("EMERGENCY_ABORTED", "AUDIT_PERSISTENCE_FAILED", None, None, clock())

    supervisor.emergency = emergency
    executor = Executor(store, clock)
    judge = Judge()
    controller = RoomController(store, judge, supervisor=supervisor, flight_executor=executor)
    before = store.events("session")
    first = controller.step("session")
    assert first.terminal_state == TerminalState.ABORTED and not first.accepted
    assert first.event_ids == () and first.terminal_source == "LOCAL_SUPERVISOR"
    assert first.terminal_reason == "AUDIT_PERSISTENCE_FAILED"
    assert first.persistence_error == "AUDIT_PERSISTENCE_FAILED"
    assert "private-secret" not in repr(first)
    refresh_count = len(refreshes)
    second = controller.step("session")
    assert second.terminal_state == TerminalState.ABORTED and not second.accepted
    # A new controller in the same process consumes the supervisor cache even
    # if the failed database can no longer serve a projection.
    restarted = RoomController(store, judge, supervisor=supervisor, flight_executor=executor)
    monkeypatch.setattr(restarted.queries, "project", lambda *args: pytest.fail("terminal cache must precede ledger reads"))
    assert restarted.run_until_terminal("session") == TerminalState.ABORTED
    assert emergencies == [True] and judge.calls == [] and executor.calls == []
    assert len(refreshes) == refresh_count and store.events("session") == before


def test_run_until_terminal_stops_on_first_unpersisted_preemption(safety, monkeypatch):
    supervisor, store, clock, samples, *_ = safety
    samples["pose"] = replace(samples["pose"], state=LocalizationState.LOST)
    monkeypatch.setattr(store, "append_events", lambda **kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("disk I/O error")))
    calls = []
    supervisor.emergency = lambda executor: TerminalOutcome("EMERGENCY_ABORTED", "AUDIT_PERSISTENCE_FAILED", None, None, clock())
    controller = RoomController(store, Judge(), supervisor=supervisor, flight_executor=Executor(store, clock))
    original = controller.step

    def once(session_id):
        calls.append(True)
        assert len(calls) == 1, "terminal execution must not loop on an absent ledger event"
        return original(session_id)

    monkeypatch.setattr(controller, "step", once)
    assert controller.run_until_terminal("session") == TerminalState.ABORTED


@pytest.mark.parametrize("state,completed_at,expected", [
    ("EMERGENCY_ABORTED", 10, TerminalState.ABORTED),
    ("LANDED", None, TerminalState.ABORTED),
    ("LANDED", 10, TerminalState.SAFE_LANDED),
    ("NOT_AIRBORNE", 10, TerminalState.ABORTED),
])
def test_refresh_failure_consumes_returned_terminal_without_inventing_landing(safety, monkeypatch, state, completed_at, expected):
    supervisor, store, clock, *_ = safety
    supervisor.refresh = lambda: (_ for _ in ()).throw(RuntimeError("telemetry unavailable"))
    supervisor.last_persistence_error = "private database path"
    terminal = TerminalOutcome(state, "AUDIT_PERSISTENCE_FAILED", None, None, completed_at)
    monkeypatch.setattr(supervisor, "refresh_failure", lambda executor: terminal)
    controller = RoomController(store, Judge(), supervisor=supervisor, flight_executor=Executor(store, clock))
    outcome = controller.step("session")
    assert outcome.terminal_state == expected and not outcome.accepted and outcome.event_ids == ()
    assert outcome.terminal_source == "LOCAL_SUPERVISOR"


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


@pytest.mark.parametrize("response", ["READ_DRONE", RuntimeError("private provider detail")])
def test_choice_attempt_records_nonnegative_monotonic_latency(room, response):
    goal(room)
    RoomController(room, Judge(response)).step("s1")
    attempt = next(e for e in room.events("s1") if e.event_type == "ROOM_CHOICE_ATTEMPT")
    assert type(attempt.payload.get("latency_ms")) in (int, float)
    assert attempt.payload["latency_ms"] >= 0


def test_unexpected_pending_ingestion_failure_is_sanitized_and_terminal_after_restart(room, tmp_path):
    from sentinel.room.store import RoomStore
    goal(room)
    judge = Judge()
    def fail(session_id):
        raise OSError("token=private-ingest-secret")
    first = RoomController(room, judge, ingest_pending=fail).step("s1")
    assert not first.accepted and first.terminal_state is None
    event = room.events("s1")[-1]
    assert event.event_type == "ROOM_TOOL_OUTCOME"
    assert event.payload["candidate_id"] == "INGEST_PENDING"
    assert event.payload["refusal_code"] == "SOURCE_FAILURE"
    assert event.payload["error_category"] == "INGEST_ERROR"
    assert "private-ingest-secret" not in str(event.payload)
    room.close()
    reopened = RoomStore.open(tmp_path / "room.sqlite3")
    try:
        second = RoomController(reopened, judge, ingest_pending=fail).step("s1")
        assert not second.accepted and second.terminal_state == TerminalState.ESCALATED
        assert reopened.events("s1")[-1].payload["code"] == "NO_PROGRESS"
        assert judge.calls == []
    finally:
        reopened.close()


def test_controller_ingests_valid_record_after_poison_and_completes_answer(room, tmp_path):
    from dataclasses import asdict
    from sentinel.room.ingest import canonical_line, poll_inbox
    goal(room, GoalKind.FIND_OBSERVE)
    result = sample(tmp_path, snapshot=room.session_version("s1"))
    inbox = tmp_path / "inbox.jsonl"
    inbox.write_bytes(b'{"token":"secret",\n' + canonical_line(asdict(result)))
    judge = Judge("READ_OBSERVATIONS", "ANSWER")
    controller = RoomController(room, judge, ingest_pending=lambda session: poll_inbox(
        room, inbox, current_snapshot_version=room.session_version(session)))
    assert controller.run_until_terminal("s1") == TerminalState.COMPLETED
    assert "computer" in room.events("s1")[-1].payload["answer"]
    assert room.connection.execute("SELECT COUNT(*) FROM room_inbox_rejections").fetchone()[0] == 1
    assert "secret" not in str([e.payload for e in room.events("s1")])


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


@pytest.mark.parametrize("kind,read", [(GoalKind.COUNT, "READ_COUNTS"),
                                      (GoalKind.FIND_OBSERVE, "READ_OBSERVATIONS")])
def test_current_view_cannot_be_completed_from_all_run_or_historical_evidence(room, tmp_path, kind, read):
    goal(room, kind, scope="CURRENT_VIEW")
    legacy = None
    kwargs = {}
    if kind == GoalKind.COUNT:
        legacy, _, _ = survey(tmp_path)
        kwargs = {"survey_store": legacy, "survey_run_id": "run1"}
    else:
        version = room.session_version("s1")
        ingest_result(room, sample(tmp_path, snapshot=version), current_snapshot_version=version)
    try:
        judge = Judge(read, "CLARIFY")
        controller = RoomController(room, judge, **kwargs)
        assert controller.step("s1").accepted
        assert controller.run_until_terminal("s1") == TerminalState.BLOCKED
        assert judge.calls[1][0]["facts"]
        assert "ANSWER" not in judge.calls[1][1]
        assert not any(e.payload.get("code") == "ANSWERED" for e in room.events("s1"))
    finally:
        if legacy:
            legacy.connection.close()


@pytest.mark.parametrize("failure", ["malformed_json", "malformed_entry", "malformed_count", "closed_source"])
def test_optional_source_projection_failure_is_audited_without_losing_drone_facts(room, tmp_path, failure):
    goal(room)
    drone(room)
    legacy, _, report = survey(tmp_path)
    if failure != "closed_source":
        malformed = {"malformed_json": "{", "malformed_entry": "[null]",
                     "malformed_count": '[{"class":"person","confirmed":-2,"uncertain":0,"method":"SINGLE_VIEW_LOWER_BOUND"}]'}[failure]
        with legacy.connection:
            legacy.connection.execute("UPDATE reports SET class_counts_json = ? WHERE report_id = ?", (malformed, report))
    else:
        legacy.connection.close()
    try:
        judge = Judge("READ_DRONE", "ANSWER")
        controller = RoomController(room, judge, survey_store=legacy, survey_run_id="run1")
        assert controller.run_until_terminal("s1") == TerminalState.COMPLETED
        failures = [e for e in room.events("s1") if e.payload.get("code") == "SOURCE_FAILURE"]
        assert len(failures) == 1
        assert failures[0].payload["kind"] == "READ_COUNTS"
        assert failures[0].payload["refusal_code"] == "SOURCE_FAILURE"
        assert judge.calls[0][0]["outcomes"][-1]["code"] == "SOURCE_FAILURE"
        assert "READ_COUNTS" not in judge.calls[0][1]
        assert "73" in room.events("s1")[-1].payload["answer"]
    finally:
        if failure != "closed_source":
            legacy.connection.close()


@pytest.mark.parametrize("malformed_state", [["invalid state"], None, 7])
def test_malformed_drone_source_and_unavailable_count_source_can_clarify(room, tmp_path, malformed_state):
    goal(room)
    append(room, "DRONE_STATE_RECORDED", {"state": malformed_state, "observed_at": "now"})
    legacy, _, _ = survey(tmp_path)
    legacy.connection.close()
    judge = Judge("CLARIFY")
    controller = RoomController(room, judge, survey_store=legacy, survey_run_id="run1")
    assert controller.run_until_terminal("s1") == TerminalState.BLOCKED
    failures = [o for o in judge.calls[0][0]["outcomes"] if o["code"] == "SOURCE_FAILURE"]
    assert {o["kind"] for o in failures} == {"READ_DRONE", "READ_COUNTS"}
    assert len([e for e in room.events("s1") if e.payload.get("code") == "SOURCE_FAILURE"]) == 2


def test_repaired_optional_source_changes_hash_and_becomes_readable_again(room, tmp_path):
    goal(room, GoalKind.COUNT)
    legacy, _, report = survey(tmp_path)
    original = legacy.connection.execute("SELECT class_counts_json FROM reports WHERE report_id = ?", (report,)).fetchone()[0]
    with legacy.connection:
        legacy.connection.execute("UPDATE reports SET class_counts_json = ? WHERE report_id = ?", ("null", report))
    try:
        judge = Judge("READ_DRONE", "READ_COUNTS", "ANSWER")
        controller = RoomController(room, judge, survey_store=legacy, survey_run_id="run1")
        controller.step("s1")
        assert "READ_COUNTS" not in judge.calls[0][1]
        with legacy.connection:
            legacy.connection.execute("UPDATE reports SET class_counts_json = ? WHERE report_id = ?", (original, report))
        assert controller.run_until_terminal("s1") == TerminalState.COMPLETED
        assert "READ_COUNTS" in judge.calls[1][1]
    finally:
        legacy.connection.close()


def test_unavailable_observation_table_preserves_drone_read_and_audits_failure(room):
    import sqlite3
    goal(room)
    drone(room)
    def authorize(action, table, column, database, trigger):
        if action == sqlite3.SQLITE_READ and table == "room_detection_evidence":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    room.connection.set_authorizer(authorize)
    judge = Judge("READ_DRONE", "ANSWER")
    try:
        assert RoomController(room, judge).run_until_terminal("s1") == TerminalState.COMPLETED
        failure, = [o for o in judge.calls[0][0]["outcomes"] if o["code"] == "SOURCE_FAILURE"]
        assert failure["kind"] == "READ_OBSERVATIONS"
        assert "READ_OBSERVATIONS" not in judge.calls[0][1]
    finally:
        room.connection.set_authorizer(None)


@pytest.mark.parametrize("labels", [("ILLEGAL", "ILLEGAL"), ("ILLEGAL_ONE", "ILLEGAL_TWO")])
def test_stale_illegal_choices_with_only_unrelated_events_terminate_on_repeated_refusal(room, labels):
    goal(room)
    def choose(label):
        def concurrent(state, criteria):
            append(room, "UNRELATED_AUDIT_EVENT", {"message": "no evidence changed"})
            return {"value": label, "probabilities": {label: .99}}
        return concurrent
    judge = Judge(*(choose(label) for label in labels))
    controller = RoomController(room, judge)
    first = controller.step("s1")
    second = controller.step("s1")
    assert not first.accepted and first.terminal_state is None
    assert not second.accepted and second.terminal_state == TerminalState.ESCALATED
    assert len(judge.calls) == 2
    refusals = [e.payload for e in room.events("s1") if e.event_type == "ROOM_TOOL_OUTCOME"]
    assert [o["code"] for o in refusals] == ["STALE_DECISION", "NO_PROGRESS"]
    assert len({o["relevant_state_sha256"] for o in refusals}) == 1
    assert all(o["candidate_id"] == "JEV" and o["refusal_code"] == "STALE_DECISION" for o in refusals)
    assert controller.run_until_terminal("s1") == TerminalState.ESCALATED
    assert len(judge.calls) == 2


def _gate_candidates():
    from sentinel.room.models import RoomCandidate
    make = lambda cid, kind: RoomCandidate(cid, kind, 1, "h", cid, "b")
    return {c.candidate_id: c for c in (
        make("OBSERVE:g1:c1:center", CandidateKind.OBSERVE), make("OBSERVE:g1:c1:left", CandidateKind.OBSERVE),
        make("OBSERVE:g1:c1:right", CandidateKind.OBSERVE), make("ESCALATE", CandidateKind.ESCALATE),
        make("READ_OBSERVATIONS", CandidateKind.READ_OBSERVATIONS))}


def test_equivalent_regions_splitting_the_probability_still_accept_the_top_observe():
    # Live 2026-09-24, turn 1: center 0.44, right 0.21, left 0.20 (0.85 on observing), scalar 0.36.
    from sentinel.jev_choice import assess_choice
    from sentinel.room.controller import observe_kind_gate
    candidates = _gate_candidates()
    answer = {"value": "OBSERVE:g1:c1:center", "confidence": 0.36, "probabilities": {
        "OBSERVE:g1:c1:center": 0.44, "OBSERVE:g1:c1:left": 0.20, "OBSERVE:g1:c1:right": 0.21,
        "ESCALATE": 0.04, "READ_OBSERVATIONS": 0.11}}
    assessment = assess_choice(answer, candidates, escalation_label="ESCALATE", act_at=0.5, min_margin=0.15)
    assert assessment.outcome == "LOW_CONFIDENCE"
    gated = observe_kind_gate(assessment, candidates, 0.5, 0.15)
    assert (gated.outcome, gated.final_label, gated.confidence_source) == (
        "ACCEPTED", "OBSERVE:g1:c1:center", "KIND_MASS")
    assert gated.confidence == pytest.approx(0.85)


def test_the_kind_gate_never_accepts_when_observing_itself_is_uncertain():
    from sentinel.jev_choice import assess_choice
    from sentinel.room.controller import observe_kind_gate
    candidates = _gate_candidates()
    answer = {"value": "OBSERVE:g1:c1:center", "probabilities": {
        "OBSERVE:g1:c1:center": 0.30, "OBSERVE:g1:c1:left": 0.10, "OBSERVE:g1:c1:right": 0.05,
        "ESCALATE": 0.29, "READ_OBSERVATIONS": 0.26}}
    assessment = assess_choice(answer, candidates, escalation_label="ESCALATE", act_at=0.5, min_margin=0.15)
    assert observe_kind_gate(assessment, candidates, 0.5, 0.15) == assessment  # 0.45 on observing: refused
