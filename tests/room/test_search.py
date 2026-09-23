"""Task B item 2b: autonomous search ("find the phone").

A FIND_OBSERVE objective with terminal OBSERVED is searched for by flying the
same one-checkpoint OBSERVE candidates the count loop uses, over the site's
declared regions, in the order Jev chooses. The search stops once a target
detection is grounded (confirmed or uncertain), answers where it was seen,
its placement and confidence -- or "not found in observed regions" with the
coverage note -- and returns home before the answer is recorded.

Everything is offline: the controller's injected collaborators, the fixture
supervisor and the stub executor from the count-and-return tests.
"""
from dataclasses import replace

import pytest

from sentinel.cube.localization import LocalizationState
from sentinel.cube.recovery import PoseReport, RecoveryController
from sentinel.cube.step_executor import ExecutorState
from sentinel.models import Position
from sentinel.room.goals import GoalObjective
from sentinel.room.models import GoalKind, TerminalState
from tests.cube.test_localization import fix
from tests.room.test_controller import Judge
from tests.room.test_count_return import (COUNT_GEOMETRY, SESSION, T0, PerceptionRig, grounded_result,
                                          make_controller, room_site, set_ledger)
from tests.room.test_supervisor import safety  # noqa: F401 - pytest fixture

POSES = ("center-middle-middle-toward_targets", "left-rear-bottom-toward_targets",
         "right-front-top-toward_targets", "right-rear-middle-toward_targets")


def search_site():
    """The count tests' site, with four declared regions to search."""
    site = room_site(coverage=True)
    visibility = tuple(sorted((pose_id, ("cell1", "cell2")) for pose_id in POSES))
    return replace(site, pose_visibility=visibility)


def find_objective(objective_id="g1", *, classes=("phone",), scope="ROOM", terminal="OBSERVED"):
    return GoalObjective(objective_id, 1, GoalKind.FIND_OBSERVE, classes, None, None, scope, terminal,
                         None, None, ())


def observe(pose_id, objective_id="g1"):
    return f"OBSERVE:{objective_id}:{pose_id}"


def setup(safety, tmp_path, found_at=None, *, band="low", judge=None):
    """A search session over ``search_site``; the phone is visible only from
    ``found_at`` (never, when ``None``). Every other region sees a computer."""
    supervisor, store, clock, *_ = safety
    supervisor.site = search_site()
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (find_objective(),), request_text="find the phone")
    rig = PerceptionRig(store, SESSION)
    for pose_id in POSES:
        target = ("phone", band, (100, 600)) if pose_id == found_at else ("computer", "high", (100, 600))
        rig.set(pose_id, grounded_result(tmp_path, supervisor.site, pose_id, (target,), captured_at=T0))
    controller, executor = make_controller(safety, judge or Judge(), rig, count_geometry=COUNT_GEOMETRY)
    return controller, executor, rig


def offered(controller):
    return {c.candidate_id for c in controller._legal_candidates(controller.queries.project(SESSION))}


def test_search_offers_every_declared_region_and_jev_chooses_the_order(safety, tmp_path):
    controller, executor, rig = setup(safety, tmp_path)
    legal = offered(controller)
    assert {observe(pose_id) for pose_id in POSES} <= legal
    # A search answers only from its own observations, never from history.
    assert "ANSWER" not in legal and "ANSWER:SEARCH" not in legal


def test_phone_found_in_the_third_region_stops_the_search_answers_and_returns_home(safety, tmp_path):
    order = (POSES[3], POSES[1], POSES[2])  # Jev's own order; the phone is in the third one flown
    judge = Judge(*(observe(pose_id) for pose_id in order), "ANSWER:SEARCH")
    controller, executor, rig = setup(safety, tmp_path, found_at=POSES[2], judge=judge)
    supervisor, store, *_ = safety

    for pose_id in order:
        outcome = controller.step(SESSION)
        assert outcome.accepted and outcome.candidate_id == observe(pose_id)
    # Found (uncertain): no further region is offered, the answer is.
    legal = offered(controller)
    assert not any(candidate_id.startswith("OBSERVE:") for candidate_id in legal)
    assert "ANSWER:SEARCH" in legal

    assert controller.run_until_terminal(SESSION) == TerminalState.COMPLETED
    assert rig.captures == list(order)
    answer = store.events(SESSION)[-1].payload
    assert answer["code"] == "ANSWERED_SEARCH"
    text = answer["answer"]
    assert "phone: seen from the right-front-top region" in text
    assert "support plane" in text and "uncertain (low confidence)" in text
    assert "not found" not in text


def test_a_high_confidence_sighting_is_reported_as_such_and_uncertain_never_promoted(safety, tmp_path):
    judge = Judge(observe(POSES[0]), "ANSWER:SEARCH")
    controller, executor, rig = setup(safety, tmp_path, found_at=POSES[0], band="high", judge=judge)
    supervisor, store, *_ = safety
    assert controller.run_until_terminal(SESSION) == TerminalState.COMPLETED
    text = store.events(SESSION)[-1].payload["answer"]
    assert "high confidence" in text and "uncertain" not in text


def test_phone_never_found_answers_not_found_in_observed_regions_with_the_coverage_note(safety, tmp_path):
    judge = Judge(*(observe(pose_id) for pose_id in POSES), "ANSWER:SEARCH")
    controller, executor, rig = setup(safety, tmp_path, found_at=None, judge=judge)
    supervisor, store, *_ = safety

    assert controller.run_until_terminal(SESSION) == TerminalState.COMPLETED
    assert sorted(rig.captures) == sorted(POSES)
    answer = store.events(SESSION)[-1].payload
    assert answer["code"] == "ANSWERED_SEARCH"
    assert "phone: not found in observed regions" in answer["answer"]
    assert "Coverage for the room" in answer["answer"]


def test_the_aircraft_is_back_on_the_ground_before_the_answer_is_recorded(safety, tmp_path):
    judge = Judge(observe(POSES[2]), "ANSWER:SEARCH")
    controller, executor, rig = setup(safety, tmp_path, found_at=POSES[2], judge=judge)
    supervisor, store, *_ = safety
    assert executor.state == ExecutorState.AIRBORNE

    assert controller.run_until_terminal(SESSION) == TerminalState.COMPLETED
    events = store.events(SESSION)
    answer_index = max(i for i, e in enumerate(events)
                       if e.event_type == "ROOM_TOOL_OUTCOME" and e.payload["code"] == "ANSWERED_SEARCH")
    landed = [i for i, e in enumerate(events) if e.event_type == "DRONE_STATE_RECORDED"
              and e.payload.get("state", {}).get("terminal_state") == "LANDED"]
    assert landed and landed[-1] < answer_index
    assert executor.calls[-1] == "LAND" and executor.state != ExecutorState.AIRBORNE


def test_tag_loss_mid_search_is_recovered_by_the_supervisor_then_the_search_resumes(safety, tmp_path):
    judge = Judge(observe(POSES[0]), observe(POSES[1]), observe(POSES[2]), "ANSWER:SEARCH")
    controller, executor, rig = setup(safety, tmp_path, found_at=POSES[2], judge=judge)
    supervisor, store, clock, samples, *_ = safety
    supervisor.recovery = RecoveryController(supervisor.tracker, clock=clock,
        burst_source=lambda action: tuple(PoseReport(str(i), fix(clock, position=Position(100, 100, 100)))
                                         for i in range(3)),
        energy_admits=lambda action: True)
    supervisor.mapped_heading_tag_ids = ((90, (1, 2)),)

    assert controller.step(SESSION).candidate_id == observe(POSES[0])
    # Tags lost: the tracker needs recovery. The supervisor recovers before
    # Jev is ever asked, then the search continues where it left off.
    samples["pose"] = replace(samples["pose"], state=LocalizationState.RECOVERY_REQUIRED, estimator_failures=3)
    calls_before = len(judge.calls)
    recovered = controller.step(SESSION)
    assert recovered.candidate_id == "RECOVER" and recovered.terminal_state is None
    assert len(judge.calls) == calls_before
    assert any(e.event_type == "POSE_REANCHORED" for e in store.events(SESSION))
    samples["pose"] = supervisor.tracker.health

    assert controller.step(SESSION).candidate_id == observe(POSES[1])
    assert controller.run_until_terminal(SESSION) == TerminalState.COMPLETED
    assert rig.captures == [POSES[0], POSES[1], POSES[2]]
    assert "phone: seen from the right-front-top region" in store.events(SESSION)[-1].payload["answer"]


def test_a_region_scoped_search_only_offers_that_region(safety, tmp_path):
    supervisor, store, *_ = safety
    controller, executor, rig = setup(safety, tmp_path)
    set_ledger(store, SESSION, (find_objective(scope="right/front/top"),), request_text="find the phone")
    legal = {candidate_id for candidate_id in offered(controller) if candidate_id.startswith("OBSERVE:")}
    assert legal == {observe("right-front-top-toward_targets")}


@pytest.mark.parametrize("objective", [
    find_objective(terminal="VALIDATED"),
    replace(find_objective(), qualifier="man"),
])
def test_non_search_find_objectives_keep_their_existing_routes(safety, tmp_path, objective):
    supervisor, store, *_ = safety
    controller, executor, rig = setup(safety, tmp_path)
    set_ledger(store, SESSION, (objective,), request_text="find it")
    assert not any(candidate_id.startswith("OBSERVE:") for candidate_id in offered(controller))
