"""Plan 03 Task 4: observation execution, adaptive count and safe return.

Task 3 (closer-inspection viewpoints) is deferred by operator decision (see
the task report and the plan's controller ruling). These trajectory tests
therefore never exercise a closer/validate-by-approach candidate; the
"closer inspection unavailable" scenario below is the substitute the plan's
ruling specifies, and "uncertain phone confirmed by closer view" /
"false detection rejected" are replaced by the two scenarios the ruling names.

Checkpoint/perception resolution is exercised through the controller's own
injected collaborators (``resolve_checkpoint``, ``perception_capture``,
``perception_provider``) rather than through real semantic-pose-to-plan
geometry or a real detector: that geometry is Task 1/2's own tested
responsibility, and Task 4 only wires the execution loop around it (see the
task report for this scoping decision).
"""
from dataclasses import asdict, replace

import pytest

from sentinel.cube.localization import LocalizationState
from sentinel.cube.recovery import PoseReport, RecoveryController
from sentinel.cube.step_executor import TerminalOutcome
from sentinel.cube.energy import EnergySample
from sentinel.models import Position
from sentinel.room.controller import RoomController, TurnOutcome
from sentinel.room.coverage import CoverageCell
from sentinel.room.goals import GoalLedger, GoalObjective
from sentinel.room.ingest import DetectionEvidence, PerceptionRequest, PerceptionResult, detection_digest
from sentinel.room.models import CandidateKind, GoalKind, GoalStatus, TerminalState
from sentinel.room.site import SemanticFrame
from tests.cube.test_localization import fix
from tests.cube.test_recovery import site as cube_site
from tests.room.test_controller import Judge
from tests.room.test_supervisor import Executor, checkpoint, command, safety

SESSION = "session"


def room_site(*, coverage=False):
    base = cube_site()
    frame = SemanticFrame("free", (1.0, 0.0), (0.0, 1.0), "FRONT", ())
    if not coverage:
        return replace(base, semantic_frame=frame)
    cells = (
        CoverageCell("cell1", ((10, 10), (50, 10), (50, 50), (10, 50)), (0, 200), ("computer",)),
        CoverageCell("cell2", ((150, 150), (190, 150), (190, 190), (150, 190)), (0, 200), ("computer",)),
    )
    visibility = (
        ("left-rear-bottom-toward_targets", ("cell1",)),
        ("right-front-top-toward_targets", ("cell2",)),
    )
    return replace(base, semantic_frame=frame, coverage_cells=cells, pose_visibility=visibility)


def append(store, session_id, kind, payload):
    version = store.session_version(session_id)
    return store.append_event(session_id=session_id, expected_version=version, event_type=kind,
                              payload=payload, correlation_id="fixture", causation_id=None,
                              idempotency_key=f"fixture:{session_id}:{version}:{kind}")


def set_ledger(store, session_id, objectives, *, status=GoalStatus.PENDING, request_text="count and return"):
    ledger = GoalLedger(request_text, objectives, status)
    return append(store, session_id, "GOAL_LEDGER_ACCEPTED", {"goal_revision": 1, "ledger": asdict(ledger)})


def count_objective(objective_id, ordinal, scope, *, terminal="REPORTED", classes=("computer",)):
    return GoalObjective(objective_id, ordinal, GoalKind.COUNT, classes, None, None, scope, terminal,
                        None, None, ())


def closer_objective(objective_id="g1"):
    return GoalObjective(objective_id, 1, GoalKind.FIND_OBSERVE, ("phone",), None, None, "ROOM", "VALIDATED",
                        None, None, ())


def drone(store, session_id, battery=73):
    return append(store, session_id, "DRONE_STATE_RECORDED", {
        "observed_at": "2026-09-23T00:00:00+00:00", "scope": "aircraft:fixture",
        "uncertainty": "last recorded telemetry; present state unknown",
        "state": {"battery_percent": battery, "flight_state": "LANDED"},
    })


def make_resolve_checkpoint(supervisor, *, seconds=1):
    def resolve(candidate, snapshot):
        return checkpoint(supervisor.tracker.health, command(seconds=seconds))
    return resolve


def make_result(tmp_path, session_id, pose_id, *, confidence_band="high", status="SUCCEEDED",
                error_category=None, class_name="computer"):
    frame = tmp_path / f"{pose_id}.png"
    frame.write_bytes(b"frame-bytes-" + pose_id.encode())
    import hashlib
    request = PerceptionRequest("room-perception-v1", session_id, "observe-action", pose_id,
        f"frame-{pose_id}", "observe-run", 0, "a" * 64, str(frame),
        hashlib.sha256(frame.read_bytes()).hexdigest(), "2026-09-23T00:00:00+00:00",
        "pose1", "pinned-model")
    if status == "FAILED":
        return PerceptionResult(request, "FAILED", "fixture", None, "UNVERIFIED",
                                "2026-09-23T00:00:01+00:00", (), error_category or "DETECTOR_ERROR")
    evidence = DetectionEvidence(f"d-{pose_id}", class_name, confidence_band, None,
        "VLM_CONFIDENCE_BAND", (1, 2, 30, 40), "FULL_FRAME_PIXELS", 640, 480, None, "")
    evidence = replace(evidence, evidence_sha256=detection_digest(evidence))
    return PerceptionResult(request, "SUCCEEDED", "fixture", "pinned-model", "VERIFIED",
                            "2026-09-23T00:00:01+00:00", (evidence,))


class PerceptionRig:
    """Controller-injected observation collaborators, fully test-controlled."""

    def __init__(self):
        self.results = {}
        self.captures = []
        self.provided = []

    def set(self, pose_id, result):
        self.results[pose_id] = result

    def capture(self, candidate, checkpoint_outcome, snapshot):
        pose_id = candidate.candidate_id.rsplit(":", 1)[-1]
        self.captures.append(pose_id)
        return self.results[pose_id].request

    def provide(self, request):
        self.provided.append(request.observation_id)
        return self.results[request.observation_id]


def make_controller(safety, judge, rig=None, *, resolve_checkpoint=None):
    supervisor, store, clock, *_ = safety
    executor = Executor(store, clock)
    kwargs = {}
    if rig is not None:
        kwargs = dict(resolve_checkpoint=resolve_checkpoint or make_resolve_checkpoint(supervisor),
                     perception_capture=rig.capture, perception_provider=rig.provide)
    controller = RoomController(store, judge, supervisor=supervisor, flight_executor=executor, **kwargs)
    return controller, executor


# --- 1. read-only question, zero movement -----------------------------------

def test_read_only_question_causes_zero_movement_even_with_observation_enabled(safety):
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site()
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (GoalObjective("g1", 1, GoalKind.ANSWER_STATE, (), None, None, "ROOM",
                                              "REPORTED", None, None, ()),))
    drone(store, SESSION)

    def fail_resolve(candidate, snapshot):
        pytest.fail("no checkpoint should ever be resolved for a read-only question")

    def fail_capture(candidate, outcome, snapshot):
        pytest.fail("no frame should ever be captured")

    def fail_provide(request):
        pytest.fail("no perception provider call is expected")

    controller, executor = make_controller(safety, Judge("READ_DRONE", "ANSWER"))
    controller.resolve_checkpoint = fail_resolve
    controller.perception_capture = fail_capture
    controller.perception_provider = fail_provide
    assert controller.run_until_terminal(SESSION) == TerminalState.COMPLETED
    assert executor.calls == []


# --- 2. ordered corner observations ------------------------------------------

def test_ordered_region_observations_execute_in_original_request_order(safety, tmp_path):
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site()
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (
        count_objective("g1", 1, "left/rear/bottom"),
        count_objective("g2", 2, "right/front/top"),
    ))
    rig = PerceptionRig()
    rig.set("left-rear-bottom-toward_targets", make_result(tmp_path, SESSION, "left-rear-bottom-toward_targets"))
    rig.set("right-front-top-toward_targets", make_result(tmp_path, SESSION, "right-front-top-toward_targets"))
    judge = Judge("OBSERVE:g1:left-rear-bottom-toward_targets", "OBSERVE:g2:right-front-top-toward_targets")
    controller, executor = make_controller(safety, judge, rig)

    first = controller.step(SESSION)
    assert first.accepted and first.candidate_id == "OBSERVE:g1:left-rear-bottom-toward_targets"
    second = controller.step(SESSION)
    assert second.accepted and second.candidate_id == "OBSERVE:g2:right-front-top-toward_targets"
    assert rig.captures == ["left-rear-bottom-toward_targets", "right-front-top-toward_targets"]
    assert executor.calls == ["CAPTURE", "CAPTURE"]
    assert store.events(SESSION)[-1].payload["code"] == "OBSERVE_SUCCEEDED"


# --- 3. uncertain detection stays uncertain, never promoted ------------------

def test_uncertain_detection_stays_uncertain_after_observation(safety, tmp_path):
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig()
    rig.set("left-rear-bottom-toward_targets",
           make_result(tmp_path, SESSION, "left-rear-bottom-toward_targets", confidence_band="low"))
    judge = Judge("OBSERVE:g1:left-rear-bottom-toward_targets", "READ_OBSERVATIONS")
    controller, executor = make_controller(safety, judge, rig)

    first = controller.step(SESSION)
    assert first.accepted and store.events(SESSION)[-1].payload["code"] == "OBSERVE_SUCCEEDED"
    second = controller.step(SESSION)
    assert second.accepted
    facts = store.events(SESSION)[-1].payload["facts"]
    assert facts and dict(tuple(map(tuple, facts[0]["values"])))["confidence_band"] == "low"
    assert "confirmed" not in str(facts)


# --- 4. closer inspection is unavailable, no closer candidate ---------------

def test_closer_inspection_request_yields_explicit_unavailable_reason(safety):
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site()
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (closer_objective(),))
    controller, executor = make_controller(safety, Judge("VALIDATE:g1:UNAVAILABLE"))

    outcome = controller.step(SESSION)
    assert outcome.accepted and outcome.terminal_state == TerminalState.BLOCKED
    assert executor.calls == []
    payload = store.events(SESSION)[-1].payload
    assert payload["code"] == "CLOSER_INSPECTION_UNAVAILABLE"
    assert "closer inspection" in payload["answer"].lower()


def test_closer_inspection_never_offers_an_executable_closer_candidate(safety):
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site()
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (closer_objective(),))
    judge = Judge()

    def fail_choice(state, criteria):
        assert not any(key.startswith(("VALIDATE_DETECTION:", "REACQUIRE", "MAINTAIN_OBSERVATION"))
                      for key in criteria if key != "VALIDATE:g1:UNAVAILABLE")
        return "VALIDATE:g1:UNAVAILABLE"

    judge.responses.append(fail_choice)
    controller, executor = make_controller(safety, judge)
    controller.step(SESSION)


# --- 5. temporary tag loss/coasting is not preempted -------------------------

def test_observation_proceeds_under_coasting_localization(safety, tmp_path):
    supervisor, store, clock, samples, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    samples["pose"] = replace(samples["pose"], state=LocalizationState.COASTING)
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig()
    rig.set("left-rear-bottom-toward_targets", make_result(tmp_path, SESSION, "left-rear-bottom-toward_targets"))
    judge = Judge("OBSERVE:g1:left-rear-bottom-toward_targets")
    controller, executor = make_controller(safety, judge, rig)

    outcome = controller.step(SESSION)
    assert outcome.accepted
    assert store.events(SESSION)[-1].payload["code"] == "OBSERVE_SUCCEEDED"


# --- 6. mandatory recovery precedes the next step, bypassing Jev ------------

def test_mandatory_recovery_bypasses_jev_then_a_later_step_resumes(safety, tmp_path):
    supervisor, store, clock, samples, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    samples["pose"] = replace(samples["pose"], state=LocalizationState.RECOVERY_REQUIRED, estimator_failures=3)
    supervisor.recovery = RecoveryController(supervisor.tracker, clock=clock,
        burst_source=lambda action: tuple(PoseReport(str(i), fix(clock, position=Position(100, 100, 100)))
                                         for i in range(3)),
        energy_admits=lambda action: True)
    supervisor.mapped_heading_tag_ids = ((90, (1, 2)),)
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig()
    rig.set("left-rear-bottom-toward_targets", make_result(tmp_path, SESSION, "left-rear-bottom-toward_targets"))
    judge = Judge("OBSERVE:g1:left-rear-bottom-toward_targets", "OBSERVE:g1:left-rear-bottom-toward_targets")
    controller, executor = make_controller(safety, judge, rig)

    first = controller.step(SESSION)
    assert not first.accepted and first.candidate_id == "RECOVER"
    assert first.terminal_state is None
    assert rig.captures == []
    # Mandatory recovery is caught by step()'s own top-level admission gate,
    # before Jev is ever consulted -- not merely before the checkpoint runs.
    assert judge.calls == []
    # The plain fixture's refresh() replays a fixed sample rather than the
    # live tracker; reflect the recovery episode's real outcome the way a
    # true telemetry stream would before the next turn observes it.
    samples["pose"] = supervisor.tracker.health

    second = controller.step(SESSION)
    assert second.accepted
    assert store.events(SESSION)[-1].payload["code"] == "OBSERVE_SUCCEEDED"
    assert rig.captures == ["left-rear-bottom-toward_targets"]


# --- 7. battery forces a return before an unaffordable view -----------------

def test_battery_forces_return_before_an_unaffordable_observation(safety, tmp_path):
    supervisor, store, clock, samples, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    samples["energy"] = EnergySample(22, clock())
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig()
    rig.set("left-rear-bottom-toward_targets", make_result(tmp_path, SESSION, "left-rear-bottom-toward_targets"))
    judge = Judge("OBSERVE:g1:left-rear-bottom-toward_targets")
    controller, executor = make_controller(safety, judge, rig,
                                           resolve_checkpoint=make_resolve_checkpoint(supervisor, seconds=100))

    outcome = controller.step(SESSION)
    assert not outcome.accepted
    assert outcome.terminal_state == TerminalState.SAFE_LANDED
    assert rig.captures == []
    assert "CAPTURE" not in executor.calls
    assert "LAND" in executor.calls
    assert len(judge.calls) == 1


# --- 8. completed coverage answers, then the observation stops offering -----

def test_completed_coverage_answers_and_stops_offering_observation(safety, tmp_path):
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig()
    rig.set("left-rear-bottom-toward_targets", make_result(tmp_path, SESSION, "left-rear-bottom-toward_targets"))
    rig.set("right-front-top-toward_targets", make_result(tmp_path, SESSION, "right-front-top-toward_targets"))
    from tests.room.test_queries import survey
    legacy, _, _ = survey(tmp_path)
    try:
        judge = Judge("OBSERVE:g1:left-rear-bottom-toward_targets", "OBSERVE:g1:right-front-top-toward_targets",
                      "READ_COUNTS", "ANSWER")
        executor = Executor(store, clock)
        controller = RoomController(store, judge, supervisor=supervisor, flight_executor=executor,
                                    survey_store=legacy, survey_run_id="run1",
                                    resolve_checkpoint=make_resolve_checkpoint(supervisor),
                                    perception_capture=rig.capture, perception_provider=rig.provide)
        assert controller.run_until_terminal(SESSION) == TerminalState.COMPLETED
        final_criteria = judge.calls[-1][1]
        assert not any(key.startswith("OBSERVE:") for key in final_criteria)
    finally:
        legacy.connection.close()


# --- 9. incomplete coverage still returns safely -----------------------------

def test_incomplete_coverage_returns_safely_never_claims_completion(safety, tmp_path):
    supervisor, store, clock, samples, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig()
    rig.set("left-rear-bottom-toward_targets", make_result(tmp_path, SESSION, "left-rear-bottom-toward_targets"))
    judge = Judge("OBSERVE:g1:left-rear-bottom-toward_targets", "OBSERVE:g1:right-front-top-toward_targets")
    controller, executor = make_controller(safety, judge, rig)

    first = controller.step(SESSION)
    assert first.accepted
    samples["energy"] = EnergySample(22, clock())
    second = controller.step(SESSION)
    assert not second.accepted
    assert second.terminal_state in (TerminalState.SAFE_LANDED, TerminalState.ABORTED)
    assert second.terminal_state != TerminalState.COMPLETED

    from sentinel.room.queries import ReadQueries
    snapshot = ReadQueries(store).project(SESSION)
    coverage = controller._coverage(snapshot)
    assert not coverage.complete
    assert "cell2:computer" in coverage.uncovered


# --- 10. a failed return is never reported as success ------------------------

def test_failed_return_never_reports_success(safety, tmp_path):
    supervisor, store, clock, samples, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    samples["energy"] = EnergySample(22, clock())
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig()
    rig.set("left-rear-bottom-toward_targets", make_result(tmp_path, SESSION, "left-rear-bottom-toward_targets"))
    judge = Judge("OBSERVE:g1:left-rear-bottom-toward_targets")
    controller, executor = make_controller(safety, judge, rig,
                                           resolve_checkpoint=make_resolve_checkpoint(supervisor, seconds=100))

    def uncertain_land(*, upper_bound_s=None, deadline=None):
        executor.calls.append("LAND")
        return TerminalOutcome("LANDED", "REQUESTED", Position(100, 100, 0), 75, None)

    executor.land = uncertain_land
    outcome = controller.step(SESSION)
    assert not outcome.accepted
    # An SDK-uncertain LAND completion (completed_at=None) is never reported
    # as mission success, regardless of which non-COMPLETED terminal label
    # the projector settles on.
    assert outcome.terminal_state != TerminalState.COMPLETED
    assert outcome.terminal_state in (TerminalState.ABORTED, TerminalState.SAFE_LANDED)


# --- 11. a stale Jev decision on OBSERVE is audited, never dispatched -------

def test_stale_observation_decision_is_never_dispatched(safety, tmp_path):
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig()
    rig.set("left-rear-bottom-toward_targets", make_result(tmp_path, SESSION, "left-rear-bottom-toward_targets"))

    def concurrent(state, criteria):
        append(store, SESSION, "CONCURRENT_CHANGE", {})
        return {"value": "OBSERVE:g1:left-rear-bottom-toward_targets",
               "probabilities": {"OBSERVE:g1:left-rear-bottom-toward_targets": .99}}

    judge = Judge(concurrent)
    controller, executor = make_controller(safety, judge, rig)
    outcome = controller.step(SESSION)
    assert not outcome.accepted and executor.calls == [] and rig.captures == []
    assert store.events(SESSION)[-1].payload["code"] == "STALE_DECISION"


# --- 12. a typed tool error is followed by a different legal action --------

def test_typed_tool_error_then_a_different_legal_action_succeeds(safety, tmp_path):
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    drone(store, SESSION)
    rig = PerceptionRig()

    def failing_resolve(candidate, snapshot):
        return None

    judge = Judge("OBSERVE:g1:left-rear-bottom-toward_targets", "READ_DRONE")
    controller, executor = make_controller(safety, judge, rig, resolve_checkpoint=failing_resolve)

    first = controller.step(SESSION)
    assert not first.accepted
    assert store.events(SESSION)[-1].payload["code"] == "TOOL_FAILURE"
    assert executor.calls == []

    second = controller.step(SESSION)
    assert second.accepted
    assert store.events(SESSION)[-1].payload["code"] == "READ_SUCCEEDED"
