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
import math

import pytest

from sentinel.camera import CameraModel
from sentinel.cube.checkpoint import CheckpointPlan
from sentinel.cube.localization import LocalizationState
from sentinel.cube.recovery import PoseReport, RecoveryController
from sentinel.cube.step_executor import CheckpointOutcome, CommandState, ExecutorState, TerminalOutcome
from sentinel.cube.energy import EnergySample
from sentinel.models import Position
from sentinel.room.controller import RoomController, TurnOutcome
from sentinel.room.coverage import CoverageCell
from sentinel.room.goals import GoalLedger, GoalObjective
from sentinel.room.grounding import CountGeometry
from sentinel.room.ingest import DetectionEvidence, PerceptionRequest, PerceptionResult, detection_digest
from sentinel.room.models import CandidateKind, GoalKind, GoalStatus, TerminalState
from sentinel.room.semantic import resolve_pose_id
from sentinel.room.site import SemanticFrame
from sentinel.room.spatial import SupportDeclaration, SupportKind, semantic_heading_to_compass_deg
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


def make_resolve_checkpoint(supervisor, *, seconds=1, executor=None):
    """Build a checkpoint that actually targets the resolved endpoint/heading
    of the pose the candidate's ID declares -- not merely the tracker's
    current (unmoved) pose -- matching the controller's pose-verification
    guard added in fix round 2 (Q5). The plan still only carries a single
    stationary CAPTURE command (real waypoint navigation stays out of this
    task's "injected collaborators, not real geometry" scope; see the task
    report), so the tracker's own position is unaffected -- only the plan's
    declared ``target``/``terminal_heading_deg`` metadata reflects the
    requested pose.

    Fix round 3 (F3): the controller now *also* requires the checkpoint's
    measured post-dispatch pose (``CheckpointOutcome.pose``) to match the
    resolved pose, not merely the plan's declared metadata -- reproducing
    the round-2 gap this closes (a resolver that only relabels
    target/heading while the reported pose never moves). When ``executor``
    is supplied, this simulates the checkpoint actually reaching its
    declared target by setting the stub's ``reported_pose`` to match, the
    same way real telemetry would report after really flying there."""
    def resolve(candidate, snapshot):
        pose = supervisor.tracker.health
        pose_id = candidate.candidate_id.rsplit(":", 1)[-1]
        resolved = resolve_pose_id(supervisor.site, pose_id)
        target = Position(resolved.x_cm, resolved.y_cm, resolved.z_cm) if resolved else pose.position
        heading = resolved.yaw_deg if resolved else pose.heading_deg
        if executor is not None:
            executor.reported_pose = target
        commands = (command(seconds=seconds),)
        return CheckpointPlan("optional", pose.position, target, pose.heading_deg, heading,
                              commands, sum(c.upper_bound_s for c in commands))
    return resolve


def make_hovering_resolve_checkpoint(supervisor, *, seconds=1):
    """Fix round 2 (Q5) negative fixture: deliberately ignores which pose the
    candidate declares and always hovers in place at the tracker's current
    pose -- this is the exact gap the round-2 review found ("the test
    resolver hovers in place for every pose and still reaches 'coverage
    complete'"). Used only by the test that proves the controller now
    refuses to dispatch (and credit) a checkpoint whose declared endpoint
    doesn't match its candidate's pose."""
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


POSE_A = "left-rear-bottom-toward_targets"
POSE_B = "right-front-top-toward_targets"
T0, T1 = "2026-09-23T00:00:00+00:00", "2026-09-23T00:00:00.500000+00:00"
LEVEL_CAMERA = CameraModel("test-level", 683.0, 683.0, 480.0, 360.0, 0.0)
COUNT_GEOMETRY = CountGeometry(LEVEL_CAMERA, (("computer", SupportDeclaration(SupportKind.FLOOR)),
                                              ("phone", SupportDeclaration(SupportKind.FLOOR))))


def grounded_result(tmp_path, site, pose_id, targets, *, captured_at):
    """A perception result whose detections are real floor objects as seen
    from ``pose_id``'s declared endpoint: each target is
    ``(class_name, confidence_band, (x_cm, y_cm) or None)``; a floor target's
    box is the exact inverse of ``estimate_detection`` for ``LEVEL_CAMERA``,
    so grounding places it back at that room position. ``None`` (for people)
    uses a fixed box -- people are never placed."""
    resolved = resolve_pose_id(site, pose_id)
    angle = math.radians(semantic_heading_to_compass_deg(resolved.yaw_deg))
    detections = []
    for index, (class_name, band, target) in enumerate(targets):
        if target is None:
            bbox = (400.0, 300.0, 60.0, 200.0)
        else:
            dx, dy = target[0] - resolved.x_cm, target[1] - resolved.y_cm
            forward = dx * math.sin(angle) + dy * math.cos(angle)
            lateral = dx * math.cos(angle) - dy * math.sin(angle)
            u = LEVEL_CAMERA.cx + LEVEL_CAMERA.fx * lateral / forward
            v = LEVEL_CAMERA.cy + LEVEL_CAMERA.fy * resolved.z_cm / forward
            bbox = (u - 15.0, v - 40.0, 30.0, 40.0)
        evidence = DetectionEvidence(f"d{index}", class_name, band, None, "VLM_CONFIDENCE_BAND", bbox,
                                     "FULL_FRAME_PIXELS", 960, 720, None, "")
        detections.append(replace(evidence, evidence_sha256=detection_digest(evidence)))
    base = make_result(tmp_path, SESSION, pose_id)
    return replace(base, request=replace(base.request, captured_at=captured_at), detections=tuple(detections))


class PerceptionRig:
    """Controller-injected observation collaborators, fully test-controlled.

    ``capture`` stamps each request with the *live* post-checkpoint session
    version (fix round 1, Q4/S1): the real production ``perception_capture``
    collaborator builds its request only after the checkpoint has actually
    dispatched, so its ``snapshot_version`` is never the stale pre-turn value
    a candidate was computed against. A pinned, deliberately stale version is
    still reachable through ``set_stale`` for the negative test that exercises
    ``ingest_result``'s authorization refusal.
    """

    def __init__(self, store, session_id):
        self.store, self.session_id = store, session_id
        self.results = {}
        self.stale = set()
        self.captures = []
        self.provided = []

    def set(self, pose_id, result):
        self.results[pose_id] = result

    def set_stale(self, pose_id, result):
        self.results[pose_id] = result
        self.stale.add(pose_id)

    def capture(self, candidate, checkpoint_outcome, snapshot):
        pose_id = candidate.candidate_id.rsplit(":", 1)[-1]
        self.captures.append(pose_id)
        request = self.results[pose_id].request
        if pose_id in self.stale:
            return request
        return replace(request, snapshot_version=self.store.session_version(self.session_id))

    def provide(self, request):
        self.provided.append(request.observation_id)
        result = self.results[request.observation_id]
        return replace(result, request=request)


def make_controller(safety, judge, rig=None, *, resolve_checkpoint=None, count_geometry=None):
    supervisor, store, clock, *_ = safety
    executor = Executor(store, clock)
    kwargs = {"count_geometry": count_geometry} if count_geometry is not None else {}
    if rig is not None:
        # Fix round 3 (F3): auto-wire the stub executor's reported measured
        # pose to whatever pose each dispatched checkpoint actually declares,
        # so the default (positive-path) fixture keeps crediting coverage
        # under the new measured-pose check. A caller supplying its own
        # ``resolve_checkpoint`` (a negative fixture, or one that never
        # reaches a completed capture) opts out of this wiring.
        kwargs.update(resolve_checkpoint=resolve_checkpoint or make_resolve_checkpoint(supervisor, executor=executor),
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
    # Fix round 3 (F5): a session that never actually took off must not be
    # treated as airborne just because the stub executor's default state
    # says so -- the shared fixture defaults to AIRBORNE purely for the
    # convenience of tests that dispatch checkpoints; this one never does.
    executor.state = ExecutorState.NEW
    assert controller.run_until_terminal(SESSION) == TerminalState.COMPLETED
    assert executor.calls == []


def test_airborne_without_any_checkpoint_still_requests_a_safe_return(safety):
    """Fix round 3 (F5): ``_has_flown`` was detected only through
    ``CHECKPOINT_COMPLETED`` events -- ``supervisor.start`` (takeoff)
    persists no such event, so an airborne session that answers before ever
    dispatching a checkpoint must still be based on the measured executor
    state, not on checkpoint history alone."""
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site()
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (GoalObjective("g1", 1, GoalKind.ANSWER_STATE, (), None, None, "ROOM",
                                              "REPORTED", None, None, ()),))
    drone(store, SESSION)
    controller, executor = make_controller(safety, Judge("READ_DRONE", "ANSWER"))
    assert executor.state == ExecutorState.AIRBORNE

    outcome = controller.run_until_terminal(SESSION)
    assert outcome == TerminalState.COMPLETED
    assert "LAND" in executor.calls
    assert executor.state != ExecutorState.AIRBORNE


# --- 2. ordered corner observations ------------------------------------------

def test_ordered_region_observations_execute_in_original_request_order(safety, tmp_path):
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site()
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (
        count_objective("g1", 1, "left/rear/bottom"),
        count_objective("g2", 2, "right/front/top"),
    ))
    rig = PerceptionRig(store, SESSION)
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
    rig = PerceptionRig(store, SESSION)
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

    # Fix round 1 (Q10): drive the single low-confidence sighting through the
    # real grounded-counting classification (association.py/counting.py) --
    # exactly the "more than one distinct view" bar a genuinely confirmed
    # object must clear -- and assert it lands in the uncertain bucket with
    # confirmed == 0, not merely that the word "confirmed" is absent from a
    # facts dump that never contained it either way.
    from sentinel.room.association import ObjectRevision
    from sentinel.room.counting import _static_counts
    from sentinel.room.queries import ReadQueries
    from sentinel.room.spatial import SupportKind
    snapshot = ReadQueries(store).project(SESSION)
    observation = next(f for f in snapshot.sources if f.kind == CandidateKind.READ_OBSERVATIONS)
    sighting = ObjectRevision("obj:computer:1", "computer", SupportKind.FLOOR, "FLOOR",
                              30.0, 30.0, (0.0, 30.0), (100.0, 100.0, 0.0), 1,
                              observation.observed_at, observation.evidence_ids, observation.evidence_ids)
    coverage = controller._coverage(snapshot)
    counts = _static_counts((sighting,), coverage, ("computer",))
    computer = next(c for c in counts if c.class_name == "computer")
    assert computer.confirmed == 0 and computer.uncertain == 1


# --- 4. closer inspection is unavailable, no closer candidate ---------------

def test_closer_inspection_request_yields_explicit_unavailable_reason(safety):
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site()
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (closer_objective(),))
    controller, executor = make_controller(safety, Judge("VALIDATE:g1:UNAVAILABLE", "ESCALATE"))
    # Fix round 3 (F5): this session never actually takes off -- pin the
    # stub executor's default (AIRBORNE, purely a convenience for tests
    # that do dispatch checkpoints) so _has_flown does not require a safe
    # return for an aircraft that was never airborne to begin with.
    executor.state = ExecutorState.NEW

    # Fix round 1 (Q11): the closer-inspection-unavailable answer is never
    # terminal by itself -- it is a non-terminal, per-objective fact that gets
    # suppressed afterward, and Jev keeps being consulted (CLARIFY/ESCALATE/
    # the unread READ_* candidates remain legal) rather than the session
    # ending in the same turn. With nothing productive left for this
    # single-objective ledger, Jev explicitly escalates next -- never having
    # flown, so no physical return is requested for that escalation either.
    first = controller.step(SESSION)
    assert first.accepted and first.terminal_state is None
    payload = store.events(SESSION)[-1].payload
    assert payload["code"] == "CLOSER_INSPECTION_UNAVAILABLE" and payload["terminal_state"] is None
    assert "closer inspection" in payload["answer"].lower()

    assert controller.run_until_terminal(SESSION) == TerminalState.ESCALATED
    assert executor.calls == []


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
    rig = PerceptionRig(store, SESSION)
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
    rig = PerceptionRig(store, SESSION)
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
    rig = PerceptionRig(store, SESSION)
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
    """Residual F2: once declared coverage is complete, the COMPLETED route
    for a session COUNT objective is the grounded count projected from this
    session's own observations -- never the unrelated legacy READ_COUNTS
    survey report, even when one is wired in and has been read."""
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig(store, SESSION)
    # The same floor computer, seen from both declared poses.
    rig.set(POSE_A, grounded_result(tmp_path, supervisor.site, POSE_A, (("computer", "high", (100, 600)),),
                                    captured_at=T0))
    rig.set(POSE_B, grounded_result(tmp_path, supervisor.site, POSE_B, (("computer", "high", (100, 600)),),
                                    captured_at=T1))
    from tests.room.test_queries import survey
    legacy, _, _ = survey(tmp_path)
    try:
        judge = Judge(f"OBSERVE:g1:{POSE_A}", f"OBSERVE:g1:{POSE_B}", "READ_COUNTS", "ANSWER:COUNTS")
        executor = Executor(store, clock)
        controller = RoomController(store, judge, supervisor=supervisor, flight_executor=executor,
                                    survey_store=legacy, survey_run_id="run1",
                                    resolve_checkpoint=make_resolve_checkpoint(supervisor, executor=executor),
                                    perception_capture=rig.capture, perception_provider=rig.provide,
                                    count_geometry=COUNT_GEOMETRY)
        assert controller.run_until_terminal(SESSION) == TerminalState.COMPLETED
        final_criteria = judge.calls[-1][1]
        assert not any(key.startswith("OBSERVE:") for key in final_criteria)
        assert "ANSWER" not in final_criteria  # the legacy-report answer is never offered
        payload = store.events(SESSION)[-1].payload
        assert payload["code"] == "ANSWERED_COUNT" and payload["terminal_state"] == "COMPLETED"
        answer = payload["answer"]
        assert answer.splitlines()[0] == "Objective g1 (room-wide count):"
        assert "computer: 1 confirmed; 0 uncertain" in answer
        assert "Coverage for the room is complete" in answer
        assert "legacy" not in answer and "survey_run" not in answer and "2 confirmed" not in answer
        projection, = payload["count_projection"]
        assert projection["objective_id"] == "g1" and projection["scope"] == "ROOM"
        assert len(projection["frame_ids"]) == 2
        # Fix round 1 (Q1): a terminal answer after this session has actually
        # flown must request a supervisor-owned safe return/landing before
        # the session is reported COMPLETED -- never leave the aircraft
        # hovering just because Jev picked a terminal answer.
        assert "LAND" in executor.calls
        assert executor.state != ExecutorState.AIRBORNE
    finally:
        legacy.connection.close()


# --- 9. incomplete coverage still returns safely -----------------------------

def test_incomplete_coverage_returns_safely_never_claims_completion(safety, tmp_path):
    supervisor, store, clock, samples, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig(store, SESSION)
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
    rig = PerceptionRig(store, SESSION)
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
    # Fix round 1 (Q9): an SDK-uncertain LAND completion (completed_at=None)
    # must never be projected as a confirmed safe landing -- the executor's
    # reported state name ("LANDED") alone is not confirmation. Tightened
    # from accepting either label: only ABORTED is correct here.
    assert outcome.terminal_state == TerminalState.ABORTED


# --- 11. a stale Jev decision on OBSERVE is audited, never dispatched -------

def test_stale_observation_decision_is_never_dispatched(safety, tmp_path):
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig(store, SESSION)
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
    rig = PerceptionRig(store, SESSION)

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


# ============================================================================
# Fix round 1 (2026-09-23): S1/Q4, Q2, Q3, Q6, Q7, Q8.
# ============================================================================

# --- 13. a stale/unauthorized perception result is typed, never credited ----

def test_stale_perception_result_is_refused_and_coverage_not_credited(safety, tmp_path):
    """S1/Q4: ingest_result's IngestOutcome.authorizes_current_state must be
    checked. A result whose request still names a stale snapshot_version
    (this session has moved on by the time ingestion runs) must never be
    reported OBSERVE_SUCCEEDED, and must never credit coverage."""
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig(store, SESSION)
    rig.set_stale("left-rear-bottom-toward_targets",
                  make_result(tmp_path, SESSION, "left-rear-bottom-toward_targets"))
    judge = Judge("OBSERVE:g1:left-rear-bottom-toward_targets")
    controller, executor = make_controller(safety, judge, rig)

    outcome = controller.step(SESSION)
    assert not outcome.accepted
    payload = store.events(SESSION)[-1].payload
    assert payload["code"] == "STALE_RESULT" and payload["refusal_code"] == "STALE_RESULT"
    # The checkpoint itself really did fly -- only the stale perception
    # result is refused, not the flight.
    assert "CAPTURE" in executor.calls

    from sentinel.room.queries import ReadQueries
    snapshot = ReadQueries(store).project(SESSION)
    assert "cell1:computer" in controller._coverage(snapshot).uncovered


# --- 14. the plain legacy answer is gated by incomplete declared coverage ---

def test_plain_answer_is_gated_while_declared_coverage_is_incomplete(safety, tmp_path):
    """Q2: build_candidates's plain ANSWER (driven only by can_answer's
    legacy READ_COUNTS check) must not be offered once observation is
    enabled, the site declares coverage cells, a COUNT objective is pending
    and coverage is not yet complete -- otherwise the count can be reported
    as mission success without the declared coverage ever being flown."""
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    from tests.room.test_queries import survey
    legacy, _, _ = survey(tmp_path)
    try:
        rig = PerceptionRig(store, SESSION)
        judge = Judge("READ_COUNTS")
        executor = Executor(store, clock)
        controller = RoomController(store, judge, supervisor=supervisor, flight_executor=executor,
                                    survey_store=legacy, survey_run_id="run1",
                                    resolve_checkpoint=make_resolve_checkpoint(supervisor),
                                    perception_capture=rig.capture, perception_provider=rig.provide)
        first = controller.step(SESSION)
        assert first.accepted and first.candidate_id == "READ_COUNTS"
        snapshot = controller.queries.project(SESSION)
        assert not controller._coverage(snapshot).complete
        legal_ids = {c.candidate_id for c in controller._legal_candidates(snapshot)}
        assert "ANSWER" not in legal_ids
    finally:
        legacy.connection.close()


# --- 15. the explicit incomplete answer reports incompleteness and returns --

def test_incomplete_answer_reports_incomplete_and_requests_safe_return(safety, tmp_path):
    """Q3: the ANSWER:<objective>:INCOMPLETE candidate must be handled
    distinctly from a plain ANSWER -- a typed incomplete code, a coverage
    caveat naming what is still uncovered, never TerminalState.COMPLETED,
    and (Q1) a requested safe return since this session did fly."""
    supervisor, store, clock, *_ = safety
    base = cube_site()
    frame = SemanticFrame("free", (1.0, 0.0), (0.0, 1.0), "FRONT", ())
    cells = (
        CoverageCell("cell1", ((10, 10), (50, 10), (50, 50), (10, 50)), (0, 200), ("computer",)),
        CoverageCell("cell2", ((150, 150), (190, 150), (190, 190), (150, 190)), (0, 200), ("computer",)),
    )
    # cell2 has no declared view at all, so no OBSERVE candidate can ever
    # resolve it and the room objective is stuck incomplete once cell1 is done.
    visibility = (("left-rear-bottom-toward_targets", ("cell1",)),)
    supervisor.site = replace(base, semantic_frame=frame, coverage_cells=cells, pose_visibility=visibility)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig(store, SESSION)
    rig.set("left-rear-bottom-toward_targets", make_result(tmp_path, SESSION, "left-rear-bottom-toward_targets"))
    judge = Judge("OBSERVE:g1:left-rear-bottom-toward_targets", "ANSWER:g1:INCOMPLETE")
    controller, executor = make_controller(safety, judge, rig)

    first = controller.step(SESSION)
    assert first.accepted and store.events(SESSION)[-1].payload["code"] == "OBSERVE_SUCCEEDED"

    outcome = controller.step(SESSION)
    assert outcome.accepted
    assert outcome.terminal_state == TerminalState.INCOMPLETE_RETURNED
    payload = store.events(SESSION)[-1].payload
    assert payload["code"] == "ANSWERED_INCOMPLETE"
    assert "cell2" in payload["answer"]
    assert "LAND" in executor.calls
    assert executor.state != ExecutorState.AIRBORNE


# --- 16. the perception wait is admitted together with its checkpoint ------

def test_observation_admission_reserves_the_perception_wait_too(safety, tmp_path):
    """Q6: the checkpoint and the airborne perception wait that follows it
    must be admitted as one action -- otherwise a checkpoint admitted alone
    could be preempted before its own provider wait ever runs."""
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig(store, SESSION)
    rig.set("left-rear-bottom-toward_targets", make_result(tmp_path, SESSION, "left-rear-bottom-toward_targets"))
    judge = Judge("OBSERVE:g1:left-rear-bottom-toward_targets")
    controller, executor = make_controller(safety, judge, rig)

    seen = []
    original_prepare = supervisor.prepare

    def spy_prepare(*, snapshot_version, checkpoint=None, provider_wait_s=0, **kwargs):
        seen.append((checkpoint is not None, provider_wait_s))
        return original_prepare(snapshot_version=snapshot_version, checkpoint=checkpoint,
                                provider_wait_s=provider_wait_s, **kwargs)

    supervisor.prepare = spy_prepare
    outcome = controller.step(SESSION)
    assert outcome.accepted
    checkpoint_calls = [call for call in seen if call[0]]
    assert checkpoint_calls and checkpoint_calls[0][1] == controller.provider_wait_s


# --- 17. a dispatch exception is a typed refusal, never a stale decision ---

def test_dispatch_exception_during_observation_is_typed_not_stale_decision(safety, tmp_path):
    """Q7: an exception raised by SafetySupervisor.dispatch after admission
    already appended a SAFETY_ASSESSED event must be recorded through the
    live-version commit path (a distinct typed code), never fall into the
    shared tail's fixed-CAS append, which would misreport it as
    STALE_DECISION even though nothing about the Jev decision went stale."""
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig(store, SESSION)
    rig.set("left-rear-bottom-toward_targets", make_result(tmp_path, SESSION, "left-rear-bottom-toward_targets"))
    judge = Judge("OBSERVE:g1:left-rear-bottom-toward_targets")
    controller, executor = make_controller(safety, judge, rig)

    def failing_dispatch(outcome, flight_executor):
        raise RuntimeError("private transport detail")

    supervisor.dispatch = failing_dispatch
    outcome = controller.step(SESSION)
    assert not outcome.accepted
    payload = store.events(SESSION)[-1].payload
    assert payload["code"] == "CHECKPOINT_FAILED" and payload["refusal_code"] == "CHECKPOINT_FAILED"
    assert "private transport detail" not in str(payload)


# --- 18. REAL mode never offers an OBSERVE candidate -----------------------

def test_real_mode_never_offers_observation_candidate(safety, tmp_path):
    """Q8: Plan 03 Task 4 enables OBSERVE for replay/simulation only; real
    mode stays disabled until Plan 06 promotes matching profiles. A REAL-mode
    session holding the base ADAPTIVE_FLIGHT grant and observation
    collaborators must never be offered or fly an OBSERVE checkpoint."""
    from sentinel.cube.energy import ExecutionMode
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    supervisor.mode = ExecutionMode.REAL
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig(store, SESSION)
    controller, executor = make_controller(safety, Judge(), rig)

    snapshot = controller.queries.project(SESSION)
    legal_ids = {c.candidate_id for c in controller._legal_candidates(snapshot)}
    assert not any(candidate_id.startswith("OBSERVE:") for candidate_id in legal_ids)
    assert not controller._observation_enabled


# ============================================================================
# Fix round 2 (2026-09-23): Q5.
# ============================================================================

# --- 19. a resolver that ignores the declared pose is refused, uncredited --

def test_observation_resolver_ignoring_the_declared_pose_is_refused_and_uncredited(safety, tmp_path):
    """Q5 (fix round 2): coverage must never be credited off the pose_id
    embedded in the candidate ID alone. ``make_hovering_resolve_checkpoint``
    reproduces exactly the round-2 review's own reproduction ("the test
    resolver hovers in place for every pose and still reaches 'coverage
    complete'") -- its plan's endpoint/heading never actually match the
    declared pose (``resolve_semantic_pose(site, pose)``). The controller
    must refuse to dispatch it (never call the executor or capture a frame)
    and must never credit that pose's coverage cells."""
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig(store, SESSION)
    rig.set("left-rear-bottom-toward_targets", make_result(tmp_path, SESSION, "left-rear-bottom-toward_targets"))
    judge = Judge("OBSERVE:g1:left-rear-bottom-toward_targets")
    controller, executor = make_controller(safety, judge, rig,
                                           resolve_checkpoint=make_hovering_resolve_checkpoint(supervisor))

    outcome = controller.step(SESSION)
    assert not outcome.accepted
    payload = store.events(SESSION)[-1].payload
    assert payload["code"] == "TOOL_FAILURE" and payload["refusal_code"] == "TOOL_FAILURE"
    assert executor.calls == []
    assert rig.captures == []

    from sentinel.room.queries import ReadQueries
    snapshot = ReadQueries(store).project(SESSION)
    assert "cell1:computer" in controller._coverage(snapshot).uncovered


# --- 20. a recovery/re-dispatch outcome without a completed capture is -----
# --- refused and uncredited, even when it otherwise looks successful -------

def test_observation_outcome_without_a_completed_capture_is_refused_and_uncredited(safety, tmp_path):
    """Q5 (fix round 2): SafetySupervisor._dispatch's mid-plan re-dispatch
    (its remaining-commands loop: ``if refreshed.transition !=
    current.transition: return self.dispatch(refreshed, executor)``) can
    return a fresh CheckpointOutcome -- e.g. a successful RECOVER's own
    vacuous ``CheckpointOutcome("COMPLETED", (), (), ..., True)`` -- that
    reports COMPLETED/completion_known and, in principle, could carry a
    non-empty observation_ids without this plan's own CAPTURE command ever
    having run (the original checkpoint's commands, and any observation IDs
    they already produced, are discarded by that recursive re-dispatch).
    Simulate exactly that shape directly on ``supervisor.dispatch`` -- status
    COMPLETED, completion_known True, non-empty observation_ids, but a
    command_states record showing only a MOVE, never a completed CAPTURE --
    and assert the controller refuses to credit it as an observation."""
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig(store, SESSION)
    rig.set("left-rear-bottom-toward_targets", make_result(tmp_path, SESSION, "left-rear-bottom-toward_targets"))
    judge = Judge("OBSERVE:g1:left-rear-bottom-toward_targets")
    controller, executor = make_controller(safety, judge, rig)

    move_only = CheckpointOutcome("COMPLETED",
        (CommandState(command(kind="MOVE"), "COMPLETED", 0.0, 1.0),), ("observation",),
        None, supervisor.tracker.health.position, True)
    supervisor.dispatch = lambda outcome, flight_executor: move_only

    outcome = controller.step(SESSION)
    assert not outcome.accepted
    payload = store.events(SESSION)[-1].payload
    assert payload["code"] == "TOOL_FAILURE" and payload["refusal_code"] == "TOOL_FAILURE"
    assert rig.captures == []

    from sentinel.room.queries import ReadQueries
    snapshot = ReadQueries(store).project(SESSION)
    assert "cell1:computer" in controller._coverage(snapshot).uncovered


# ============================================================================
# Fix round 3 (2026-09-23): F1, F3, F4, F9.
# ============================================================================

# --- 21. a non-terminal return leg is retried until the aircraft lands -----

def test_finalize_terminal_retries_a_nonterminal_return_leg_until_landed(safety):
    """F1: a return leg that comes back as a non-terminal CheckpointOutcome
    (SDK completion unknown, or a mid-route re-dispatch such as a
    RECOVER episode that resolves without landing) must never be reported
    as a terminal answer while the aircraft is still airborne --
    _finalize_terminal must keep requesting the safe return until it
    actually reaches a TerminalOutcome."""
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site()
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (GoalObjective("g1", 1, GoalKind.ANSWER_STATE, (), None, None, "ROOM",
                                              "REPORTED", None, None, ()),))
    drone(store, SESSION)
    controller, executor = make_controller(safety, Judge("READ_DRONE", "ANSWER"))
    assert executor.state == ExecutorState.AIRBORNE

    landed = TerminalOutcome("LANDED", "REQUESTED", Position(100, 100, 0), 75, clock())
    stuck = CheckpointOutcome("COMPLETED", (), (), None, Position(50, 50, 100), False)
    calls = []

    def dispatch(outcome, flight_executor):
        calls.append(outcome.transition)
        return stuck if len(calls) == 1 else landed

    supervisor.dispatch = dispatch

    first = controller.step(SESSION)
    assert first.accepted
    outcome = controller.step(SESSION)
    assert outcome.accepted
    assert outcome.terminal_state == TerminalState.COMPLETED
    assert len(calls) >= 2


def test_finalize_terminal_escalates_after_exhausting_return_attempts(safety):
    """F1: when a return leg never reaches a TerminalOutcome even after
    ``_MAX_RETURN_ATTEMPTS`` retries, _finalize_terminal must escalate
    through a forced emergency landing rather than loop forever or, worse,
    report a terminal answer while the aircraft's own physical state is
    still provably in motion."""
    from sentinel.room.controller import _MAX_RETURN_ATTEMPTS

    supervisor, store, clock, *_ = safety
    supervisor.site = room_site()
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (GoalObjective("g1", 1, GoalKind.ANSWER_STATE, (), None, None, "ROOM",
                                              "REPORTED", None, None, ()),))
    drone(store, SESSION)
    controller, executor = make_controller(safety, Judge("READ_DRONE", "ANSWER"))

    stuck = CheckpointOutcome("COMPLETED", (), (), None, Position(50, 50, 100), False)
    calls = []

    def dispatch(outcome, flight_executor):
        calls.append(outcome.transition)
        return stuck

    supervisor.dispatch = dispatch

    first = controller.step(SESSION)
    assert first.accepted
    outcome = controller.step(SESSION)
    # The ANSWER candidate itself was legally chosen and processed through
    # _finalize_terminal (never a refusal, unlike the mandatory-preemption
    # path's TurnOutcome); the aircraft's failure to actually land is
    # reported through terminal_state instead.
    assert outcome.accepted
    assert outcome.terminal_state == TerminalState.ABORTED
    assert len(calls) == _MAX_RETURN_ATTEMPTS
    # The bound-exhaustion escalation (refresh_failure) always forces the
    # supervisor terminal, guaranteeing the aircraft was actually landed or
    # emergency-stopped rather than left mid-return with nothing tracking it.
    assert supervisor._terminal is True


# --- 22. a measured pose that never actually reached the declared pose -----

def test_measured_pose_mismatch_after_dispatch_is_refused_and_uncredited(safety, tmp_path):
    """F3: a checkpoint plan that declares the correct target/heading
    (``_checkpoint_targets_declared_pose``) must still be refused if the
    aircraft's own measured post-dispatch pose does not actually match the
    resolved pose -- reproducing exactly the round-2 gap this closes: a
    resolver that only relabels target/heading metadata while the reported
    pose stays wherever the aircraft already was."""
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig(store, SESSION)
    rig.set("left-rear-bottom-toward_targets", make_result(tmp_path, SESSION, "left-rear-bottom-toward_targets"))
    judge = Judge("OBSERVE:g1:left-rear-bottom-toward_targets")
    # No executor wiring here (unlike make_controller's default auto-wiring):
    # the plan's declared target/heading correctly name the resolved pose,
    # but the measured post-dispatch pose the stub executor reports never
    # actually moves from its default.
    controller, executor = make_controller(safety, judge, rig,
                                           resolve_checkpoint=make_resolve_checkpoint(supervisor))

    outcome = controller.step(SESSION)
    assert not outcome.accepted
    payload = store.events(SESSION)[-1].payload
    assert payload["code"] == "TOOL_FAILURE" and payload["refusal_code"] == "TOOL_FAILURE"
    assert "CAPTURE" in executor.calls  # the checkpoint really did dispatch
    assert rig.captures == []  # but perception was never reached

    from sentinel.room.queries import ReadQueries
    snapshot = ReadQueries(store).project(SESSION)
    assert "cell1:computer" in controller._coverage(snapshot).uncovered


# --- 23. a perception wait preempted after a completed capture is recorded -

def test_perception_preempted_after_checkpoint_completes_is_recorded_not_dropped(safety, tmp_path):
    """F4: a perception wait preempted by a mandatory safety transition
    after the checkpoint's own CAPTURE already completed must never
    silently drop that capture -- a durable, non-terminal OBSERVE outcome
    referencing the issued perception request must be recorded before the
    mandatory preemption is reported."""
    supervisor, store, clock, samples, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig(store, SESSION)
    rig.set("left-rear-bottom-toward_targets", make_result(tmp_path, SESSION, "left-rear-bottom-toward_targets"))
    original_capture = rig.capture

    def capture(candidate, checkpoint_outcome, snapshot):
        request = original_capture(candidate, checkpoint_outcome, snapshot)
        # Force the *next* admission (the perception wait) to detect
        # insufficient energy and preempt, deterministically, before the
        # perception provider is ever called (SafetySupervisor.run_wait's
        # own initial prepare() catches this synchronously).
        samples["energy"] = EnergySample(22, clock())
        return request

    rig.capture = capture
    judge = Judge("OBSERVE:g1:left-rear-bottom-toward_targets")
    controller, executor = make_controller(safety, judge, rig)

    outcome = controller.step(SESSION)
    assert not outcome.accepted
    assert "CAPTURE" in executor.calls
    # Some mandatory safety response fired (RETURN_NOW/LAND_CONTINGENCY or a
    # RECOVER that falls through to emergency); which one depends on assess()
    # admitting a bare provider wait with no attached checkpoint. Either way
    # the aircraft is no longer nominally airborne once it fires.
    assert executor.state != ExecutorState.AIRBORNE
    assert rig.provided == []  # the perception provider was never reached

    outcomes = [e.payload for e in store.events(SESSION) if e.event_type == "ROOM_TOOL_OUTCOME"]
    preempted = [p for p in outcomes if p.get("code") == "PERCEPTION_PREEMPTED"]
    assert len(preempted) == 1
    assert preempted[0]["refusal_code"] == "PERCEPTION_PREEMPTED"
    assert preempted[0]["perception_request"]["observation_id"] == "left-rear-bottom-toward_targets"

    from sentinel.room.queries import ReadQueries
    snapshot = ReadQueries(store).project(SESSION)
    assert "cell1:computer" in controller._coverage(snapshot).uncovered


# --- 24. a stale decision caught after admission (not only at the Jev choice)

def test_stale_decision_caught_after_room_choice_attempt_is_typed_correctly(safety, tmp_path):
    """F9: a concurrent write that races the checkpoint's own admission --
    not just one that preempts the Jev choice itself (see test 11) -- must
    also be reported STALE_DECISION, never as a physical CHECKPOINT_FAILED."""
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig(store, SESSION)
    rig.set("left-rear-bottom-toward_targets", make_result(tmp_path, SESSION, "left-rear-bottom-toward_targets"))
    judge = Judge("OBSERVE:g1:left-rear-bottom-toward_targets")

    real_resolve = make_resolve_checkpoint(supervisor)

    def racing_resolve(candidate, snapshot):
        plan = real_resolve(candidate, snapshot)
        append(store, SESSION, "CONCURRENT_CHANGE", {})
        return plan

    controller, executor = make_controller(safety, judge, rig, resolve_checkpoint=racing_resolve)
    outcome = controller.step(SESSION)
    assert not outcome.accepted
    payload = store.events(SESSION)[-1].payload
    assert payload["code"] == "STALE_DECISION" and payload["refusal_code"] == "STALE_DECISION"
    assert executor.calls == []
    assert rig.captures == []


# ============================================================================
# Plan 03 residual task R (2026-09-23): NB1.
# ============================================================================

# --- 25. a version conflict after partial motion is never a stale decision -

def test_version_conflict_after_partial_motion_is_checkpoint_failed_not_stale(safety, tmp_path):
    """NB1: ``SafetySupervisor._dispatch`` re-prepares each command after the
    first against the live version and then audits it with a CAS append; a
    concurrent write between those two points surfaces as a plain
    ``VersionConflict`` *after* an earlier command of the same checkpoint
    already moved the aircraft. That must be recorded as CHECKPOINT_FAILED
    (motion happened), never STALE_DECISION ("nothing was dispatched")."""
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig(store, SESSION)
    rig.set("left-rear-bottom-toward_targets", make_result(tmp_path, SESSION, "left-rear-bottom-toward_targets"))
    judge = Judge("OBSERVE:g1:left-rear-bottom-toward_targets")
    controller, executor = make_controller(safety, judge, rig)
    single = controller.resolve_checkpoint

    def two_command_resolve(candidate, snapshot):
        plan = single(candidate, snapshot)
        commands = (command("MOVE", direction="right", distance_cm=20), command())
        return replace(plan, commands=commands, worst_case_s=sum(c.upper_bound_s for c in commands))

    controller.resolve_checkpoint = two_command_resolve
    original_prepare = supervisor.prepare

    def racing_prepare(**kwargs):
        decision = original_prepare(**kwargs)
        if "remaining_commands" in kwargs:
            # The first command has already executed and been recorded; a
            # concurrent writer now races the second command's CAS audit.
            append(store, SESSION, "CONCURRENT_CHANGE", {})
        return decision

    supervisor.prepare = racing_prepare
    outcome = controller.step(SESSION)
    assert not outcome.accepted
    assert executor.calls == ["MOVE"]
    payload = store.events(SESSION)[-1].payload
    assert payload["code"] == "CHECKPOINT_FAILED" and payload["refusal_code"] == "CHECKPOINT_FAILED"
    assert rig.captures == []


# ============================================================================
# Plan 03 residual task R (2026-09-23): F2 -- grounded counting wired in.
# ============================================================================

def _outcomes(store):
    return [e.payload for e in store.events(SESSION) if e.event_type == "ROOM_TOOL_OUTCOME"]


# --- 26. each successful observation persists replayable count evidence ----

def test_observe_success_persists_grounded_count_evidence(safety, tmp_path):
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig(store, SESSION)
    rig.set(POSE_A, grounded_result(tmp_path, supervisor.site, POSE_A,
                                    (("computer", "high", (100, 600)), ("person", "high", None)), captured_at=T0))
    controller, executor = make_controller(safety, Judge(f"OBSERVE:g1:{POSE_A}"), rig,
                                           count_geometry=COUNT_GEOMETRY)
    assert controller.step(SESSION).accepted
    payload = store.events(SESSION)[-1].payload
    assert payload["code"] == "OBSERVE_SUCCEEDED"
    evidence = payload["count_evidence"]
    assert evidence["pose_id"] == POSE_A and evidence["captured_at"] == T0
    estimate, = evidence["estimates"]
    assert estimate["class_name"] == "computer"
    assert abs(estimate["x_cm"] - 100) < 1e-6 and abs(estimate["y_cm"] - 600) < 1e-6
    assert (evidence["people_confirmed"], evidence["people_uncertain"]) == (1, 0)
    # The projected snapshot restores it as typed, immutable evidence.
    outcome = controller.queries.project(SESSION).outcomes[-1]
    assert outcome.count_evidence is not None and outcome.count_evidence.estimates[0].class_name == "computer"


# --- 27. region-scope objectives answer with viewpoint-scoped counts -------

def test_region_scope_objectives_answer_with_viewpoint_scoped_grounded_counts(safety, tmp_path):
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "left/rear/bottom"),
                                count_objective("g2", 2, "right/front/top")))
    rig = PerceptionRig(store, SESSION)
    rig.set(POSE_A, grounded_result(tmp_path, supervisor.site, POSE_A, (("computer", "high", (100, 600)),),
                                    captured_at=T0))
    rig.set(POSE_B, grounded_result(tmp_path, supervisor.site, POSE_B, (), captured_at=T1))

    def answer(state, criteria):
        # Both requested regions observed: neither region is offered again,
        # and the grounded count answer is legal.
        assert not any(key.startswith("OBSERVE:") for key in criteria)
        assert "ANSWER" not in criteria
        return "ANSWER:COUNTS"

    judge = Judge(f"OBSERVE:g1:{POSE_A}", f"OBSERVE:g2:{POSE_B}", answer)
    controller, executor = make_controller(safety, judge, rig, count_geometry=COUNT_GEOMETRY)
    assert controller.run_until_terminal(SESSION) == TerminalState.COMPLETED
    payload = store.events(SESSION)[-1].payload
    assert payload["code"] == "ANSWERED_COUNT"
    first, second = payload["answer"].split("\n\n")
    assert first.splitlines()[0] == f"Objective g1 (count from declared viewpoint {POSE_A}):"
    assert "computer: 0 confirmed; 1 uncertain" in first and "this viewpoint" in first
    assert second.splitlines()[0] == f"Objective g2 (count from declared viewpoint {POSE_B}):"
    assert "computer: 0 confirmed; 0 uncertain" in second and "does not mean none are present" not in second
    assert "the room" not in payload["answer"]
    assert [p["scope"] for p in payload["count_projection"]] == ["VIEWPOINT", "VIEWPOINT"]
    assert "LAND" in executor.calls


# --- 28. a region that could not be observed gets an explicit incomplete ---

def test_region_scope_objective_that_cannot_be_observed_answers_incomplete(safety, tmp_path):
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site()
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "left/rear/bottom"),))
    rig = PerceptionRig(store, SESSION)

    def refuse(candidate, snapshot):
        return None

    def incomplete(state, criteria):
        assert f"OBSERVE:g1:{POSE_A}" not in criteria and "ANSWER:COUNTS" not in criteria
        return "ANSWER:g1:INCOMPLETE"

    judge = Judge(f"OBSERVE:g1:{POSE_A}", incomplete)
    controller, executor = make_controller(safety, judge, rig, resolve_checkpoint=refuse,
                                           count_geometry=COUNT_GEOMETRY)
    assert controller.run_until_terminal(SESSION) == TerminalState.INCOMPLETE_RETURNED
    payload = store.events(SESSION)[-1].payload
    assert payload["code"] == "ANSWERED_INCOMPLETE"
    assert payload["answer"].startswith("Objective g1 cannot be completed")
    assert f"Objective g1 (count from declared viewpoint {POSE_A}):" in payload["answer"]
    assert "computer: 0 confirmed; 0 uncertain" in payload["answer"]


# --- 29. uncertain detections stay uncertain in the rendered answer --------

def test_low_confidence_detections_stay_uncertain_in_the_terminal_answer(safety, tmp_path):
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig(store, SESSION)
    rig.set(POSE_A, grounded_result(tmp_path, supervisor.site, POSE_A, (("computer", "low", (100, 600)),),
                                    captured_at=T0))
    rig.set(POSE_B, grounded_result(tmp_path, supervisor.site, POSE_B, (("computer", "low", (100, 600)),),
                                    captured_at=T1))
    judge = Judge(f"OBSERVE:g1:{POSE_A}", f"OBSERVE:g1:{POSE_B}", "ANSWER:COUNTS")
    controller, executor = make_controller(safety, judge, rig, count_geometry=COUNT_GEOMETRY)
    assert controller.run_until_terminal(SESSION) == TerminalState.COMPLETED
    assert "computer: 0 confirmed; 1 uncertain" in store.events(SESSION)[-1].payload["answer"]


# --- 30. without count geometry, detections are reported unplaced ----------

def test_detections_without_count_geometry_are_reported_unplaced_not_zero(safety, tmp_path):
    supervisor, store, clock, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig(store, SESSION)
    rig.set(POSE_A, grounded_result(tmp_path, supervisor.site, POSE_A, (("computer", "high", (100, 600)),),
                                    captured_at=T0))
    rig.set(POSE_B, grounded_result(tmp_path, supervisor.site, POSE_B, (), captured_at=T1))
    judge = Judge(f"OBSERVE:g1:{POSE_A}", f"OBSERVE:g1:{POSE_B}", "ANSWER:COUNTS")
    controller, executor = make_controller(safety, judge, rig)
    assert controller.run_until_terminal(SESSION) == TerminalState.COMPLETED
    answer = store.events(SESSION)[-1].payload["answer"]
    assert ("computer: 0 confirmed; 0 uncertain; 1 detection could not be placed in room coordinates "
            "and is not counted") in answer


# --- 31. the room-wide incomplete answer carries the grounded counts too ---

def test_room_incomplete_answer_carries_grounded_counts_and_the_uncovered_cells(safety, tmp_path):
    supervisor, store, clock, *_ = safety
    base = cube_site()
    frame = SemanticFrame("free", (1.0, 0.0), (0.0, 1.0), "FRONT", ())
    cells = (
        CoverageCell("cell1", ((10, 10), (50, 10), (50, 50), (10, 50)), (0, 200), ("computer",)),
        CoverageCell("cell2", ((150, 150), (190, 150), (190, 190), (150, 190)), (0, 200), ("computer",)),
    )
    supervisor.site = replace(base, semantic_frame=frame, coverage_cells=cells,
                              pose_visibility=((POSE_A, ("cell1",)),))
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    rig = PerceptionRig(store, SESSION)
    rig.set(POSE_A, grounded_result(tmp_path, supervisor.site, POSE_A, (("computer", "high", (100, 600)),),
                                    captured_at=T0))

    def incomplete(state, criteria):
        assert "ANSWER:COUNTS" not in criteria
        return "ANSWER:g1:INCOMPLETE"

    judge = Judge(f"OBSERVE:g1:{POSE_A}", incomplete)
    controller, executor = make_controller(safety, judge, rig, count_geometry=COUNT_GEOMETRY)
    assert controller.run_until_terminal(SESSION) == TerminalState.INCOMPLETE_RETURNED
    payload = store.events(SESSION)[-1].payload
    assert "Objective g1 (room-wide count):" in payload["answer"]
    assert "computer: 0 confirmed; 1 uncertain" in payload["answer"]
    assert "cell2:computer not yet resolved" in payload["answer"]
    assert payload["count_projection"][0]["coverage"]["complete"] is False
