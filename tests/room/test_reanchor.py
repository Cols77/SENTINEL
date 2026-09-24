"""Task B item 2c: Jev sees localization health and may choose a bounded re-anchor.

The voluntary re-anchor is the supervisor's own stationary recovery, run
early through the supervisor's admission. The safety floor is unchanged:
RECOVERY_REQUIRED is still recovered by the supervisor without asking Jev,
and a voluntary attempt that runs out of admissible actions is refused --
it never lands. Offline: the count/search fixtures and the stub executor.
"""
from dataclasses import replace

from sentinel.cube.localization import LocalizationState
from sentinel.cube.recovery import PoseReport, RecoveryController
from sentinel.models import Position
from sentinel.room.models import TerminalState
from tests.cube.test_localization import fix
from tests.room.test_controller import Judge
from tests.room.test_count_return import SESSION, count_objective, make_controller, room_site, set_ledger
from tests.room.test_search import POSES, observe
from tests.room.test_search import setup as search_setup
from tests.room.test_supervisor import safety  # noqa: F401 - pytest fixture


def live_like(safety, *, burst=True):
    """Refresh from the live tracker (as the real stack does) and give the
    supervisor its recovery adapter; ``burst`` False makes every tag burst
    come back empty (the tags stay out of view)."""
    supervisor, store, clock, samples, *_ = safety
    supervisor.refresh = lambda: (supervisor.tracker.health, samples["energy"])
    reports = (lambda action: tuple(PoseReport(str(i), fix(clock, position=Position(100, 100, 100)))
                                    for i in range(3))) if burst else (lambda action: ())
    supervisor.recovery = RecoveryController(supervisor.tracker, clock=clock, burst_source=reports,
                                             energy_admits=lambda action: True)
    supervisor.mapped_heading_tag_ids = ((90, (1, 2)),)


def coast(safety, failures=1):
    supervisor, store, clock, *_ = safety
    for _ in range(failures):
        supervisor.tracker.note_camera(clock.now, estimator_failed=True)
    return supervisor.tracker.health


def count_session(safety, judge):
    supervisor, store, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),))
    return make_controller(safety, judge)


def test_jev_sees_localization_health_without_coordinates(safety):
    judge = Judge("READ_DRONE")
    controller, executor = count_session(safety, judge)
    live_like(safety)
    coast(safety)
    controller.step(SESSION)
    state, criteria = judge.calls[0]
    health = state["localization"]
    assert health["state"] == "COASTING"
    assert set(health) == {"state", "last_tag_fix_age_s", "position_uncertainty_cm",
                           "margin_before_mandatory_recovery_pct", "recent_positioning_events",
                           "heading_offset_from_tags_deg"}
    assert health["recent_positioning_events"] == []
    assert 0 <= health["margin_before_mandatory_recovery_pct"] <= 100
    assert "REANCHOR" in criteria and "COASTING" in criteria["REANCHOR"]


def test_jev_picks_reanchor_while_coasting_and_the_next_snapshot_shows_anchored(safety):
    supervisor, store, clock, *_ = safety

    def next_turn(state, criteria):
        return "ESCALATE"

    judge = Judge("REANCHOR", next_turn)
    controller, executor = count_session(safety, judge)
    live_like(safety)
    before = coast(safety)
    assert before.state == LocalizationState.COASTING

    outcome = controller.step(SESSION)
    assert outcome.accepted and outcome.candidate_id == "REANCHOR"
    events = store.events(SESSION)
    assert events[-1].payload["code"] == "REANCHORED"
    assert {"POSE_REANCHORED", "CANDIDATES_INVALIDATED"} <= {e.event_type for e in events}
    assert "LAND" not in executor.calls

    controller.step(SESSION)
    state, criteria = judge.calls[1]
    assert state["localization"]["state"] == "ANCHORED"
    assert state["localization"]["last_tag_fix_age_s"] == 0
    assert "REANCHOR" not in criteria  # anchored on a fresh fix: nothing to re-anchor
    assert any(o["code"] == "REANCHORED" for o in state["outcomes"])
    events = state["localization"]["recent_positioning_events"]
    assert events[0].startswith("Voluntary re-anchor started")
    assert "position re-anchored near x " in events[-1]


def test_jev_ignores_staleness_and_the_supervisor_forces_recovery_at_the_threshold(safety):
    supervisor, store, clock, *_ = safety
    judge = Judge("READ_DRONE")
    controller, executor = count_session(safety, judge)
    live_like(safety)
    coast(safety)

    first = controller.step(SESSION)
    assert first.candidate_id == "READ_DRONE"
    assert "REANCHOR" in judge.calls[0][1]  # offered, and ignored

    health = coast(safety, failures=2)  # the calibrated estimator-failure limit is reached
    assert health.state == LocalizationState.RECOVERY_REQUIRED
    second = controller.step(SESSION)
    assert second.candidate_id == "RECOVER" and second.terminal_state is None
    assert len(judge.calls) == 1  # never asked: the safety floor is the supervisor's
    assert any(e.event_type == "POSE_REANCHORED" for e in store.events(SESSION))
    assert supervisor.tracker.health.state == LocalizationState.ANCHORED


def test_a_refused_voluntary_reanchor_never_lands_and_is_not_offered_again(safety):
    supervisor, store, clock, *_ = safety
    judge = Judge("REANCHOR", "ESCALATE")
    controller, executor = count_session(safety, judge)
    live_like(safety, burst=False)
    coast(safety)

    outcome = controller.step(SESSION)
    assert outcome.candidate_id == "REANCHOR" and outcome.terminal_state is None
    payload = store.events(SESSION)[-1].payload
    assert payload["code"] == "REANCHOR_REFUSED" and payload["refusal_code"] == "REANCHOR_REFUSED"
    assert "LAND" not in executor.calls
    assert not any(e.event_type == "POSE_REANCHORED" for e in store.events(SESSION))
    snapshot = controller.queries.project(SESSION)
    assert "REANCHOR" not in {c.candidate_id for c in controller._legal_candidates(snapshot)}


def test_reanchor_is_not_offered_while_freshly_anchored_or_without_a_recovery_adapter(safety):
    supervisor, store, clock, *_ = safety
    controller, executor = count_session(safety, Judge())
    snapshot = controller.queries.project(SESSION)
    coast(safety)
    # No recovery adapter: there is nothing to run a re-anchor with.
    assert "REANCHOR" not in {c.candidate_id for c in controller._legal_candidates(snapshot)}
    live_like(safety)
    supervisor.tracker.accept_fix(fix(clock))
    assert supervisor.tracker.health.state == LocalizationState.ANCHORED
    assert "REANCHOR" not in {c.candidate_id for c in controller._legal_candidates(snapshot)}


def test_reanchor_is_offered_while_anchored_on_an_aged_fix(safety):
    supervisor, store, clock, *_ = safety
    controller, executor = count_session(safety, Judge())
    live_like(safety)
    clock.now += supervisor.tracker.profile.max_fix_age_s / 2
    assert supervisor.tracker.health.state == LocalizationState.ANCHORED
    snapshot = controller.queries.project(SESSION)
    assert "REANCHOR" in {c.candidate_id for c in controller._legal_candidates(snapshot)}


def test_a_mid_search_reanchor_is_followed_by_the_search_resuming(safety, tmp_path):
    judge = Judge(observe(POSES[0]), "REANCHOR", observe(POSES[1]), observe(POSES[2]), "ANSWER:SEARCH")
    controller, executor, rig = search_setup(safety, tmp_path, found_at=POSES[2], judge=judge)
    supervisor, store, *_ = safety
    live_like(safety)

    assert controller.step(SESSION).candidate_id == observe(POSES[0])
    coast(safety)
    reanchor = controller.step(SESSION)
    assert reanchor.accepted and reanchor.candidate_id == "REANCHOR"
    assert store.events(SESSION)[-1].payload["code"] == "REANCHORED"
    # The search resumes where it left off: the observed region is not offered again.
    assert controller.step(SESSION).candidate_id == observe(POSES[1])
    assert observe(POSES[0]) not in judge.calls[2][1] and observe(POSES[1]) in judge.calls[2][1]
    assert controller.run_until_terminal(SESSION) == TerminalState.COMPLETED
    assert rig.captures == [POSES[0], POSES[1], POSES[2]]
    assert "phone: seen from the right-front-top region" in store.events(SESSION)[-1].payload["answer"]
