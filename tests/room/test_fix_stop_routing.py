"""Fix-stop routing (one transit stop, then retry the zone) and the arrival
heading retry (one corrective re-run before refusing coverage credit).

The controller tests drive ``RoomController._execute_observation`` against a
scripted supervisor (admission and dispatch decided per checkpoint id) on the
real CanningSt site, so every stop choice is the real site's; the integration
test pins the stop choice against the real supervisor from the pad.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import threading
import time

import pytest

from sentinel.cube.checkpoint import CheckpointPlan
from sentinel.cube.localization import LocalizationState
from sentinel.cube.step_executor import CheckpointOutcome, CommandState, ExecutorState
from sentinel.models import Position
from sentinel.room import wiring
from sentinel.room.controller import _ZONE_REFUSAL_DETAIL, RoomController
from sentinel.room.models import CandidateKind, CapabilityProfile
from sentinel.room.semantic import resolve_pose_id
from sentinel.room.store import RoomStore
from sentinel.room.wiring import CapturedRequest
from tests.room.test_canningst_site import ROOT, SITE_PATH, _airborne_supervisor, canningst_site
from tests.room.test_controller import Judge
from tests.room.test_count_return import make_result
from tests.room.test_supervisor import command

SESSION = "session"
PAD = Position(250, 190, 70)
CENTER_FRONT, CENTER_REAR = "center-front-middle-toward_targets", "center-rear-middle-toward_targets"
LEFT_FRONT, LEFT_REAR = "left-front-middle-toward_targets", "left-rear-middle-toward_targets"
OFFSETS = (0, -30, 30)


def _binding():
    from sentinel.room import live_session
    profiles = ROOT / "fixtures" / "room_agent" / "v1" / "profiles"
    return live_session.load_flight_binding(
        site_path=SITE_PATH, drift_profile_path=profiles / "canningst-drift.json",
        energy_profile_path=profiles / "canningst-energy.json",
        command_limits_path=profiles / "canningst-command-limits.json",
        profile_paths=(profiles / "canningst-flight-profile.json",))


# ---------------------------------------------------------------------------
# wiring.make_transit_checkpoint
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("localise", [False, True])
@pytest.mark.parametrize("start_heading", [90.0, 97.0])
def test_a_transit_flies_to_the_pose_without_capturing_and_ends_at_its_yaw(localise, start_heading):
    binding = _binding()
    limits = replace(binding.limits, localise=localise)
    pose = SimpleNamespace(position=Position(250, 190, 40), heading_deg=start_heading)
    transit = wiring.make_transit_checkpoint(site=binding.site, profile=binding.calibration_profiles[0],
                                             pose_source=lambda: pose, limits=limits)
    plan = transit(LEFT_FRONT)
    resolved = resolve_pose_id(binding.site, LEFT_FRONT)
    kinds = [c.kind for c in plan.commands]
    assert "CAPTURE" not in kinds
    assert not any(c.kind == "SETTLE" and dict(c.arguments).get("after") == "look" for c in plan.commands)
    assert plan.checkpoint_id.endswith(f":{LEFT_FRONT}") and plan.checkpoint_id.startswith("TRANSIT:")
    assert plan.checkpoint_id.rsplit(":", 1)[-1] == LEFT_FRONT
    assert plan.target == Position(resolved.x_cm, resolved.y_cm, resolved.z_cm)
    assert plan.terminal_heading_deg == resolved.yaw_deg
    assert kinds.count("MOVE") == 3  # x, y and the climb from the pad
    assert kinds[-1] == "READ_TELEMETRY" and "TRIM_ALLOWANCE" in kinds
    assert ("LOCALISE" in kinds) == localise
    assert plan.worst_case_s == pytest.approx(sum(c.upper_bound_s for c in plan.commands))


# ---------------------------------------------------------------------------
# Controller: a scripted supervisor on the real CanningSt site
# ---------------------------------------------------------------------------

class ScriptedSupervisor:
    """Admits and dispatches by checkpoint id. ``admit(plan, sup)`` returns a
    ``(transition, reason)``; ``after_dispatch(plan, sup)`` may move the
    tracker and returns the outcome pose (None: the plan's target)."""

    clock_domain_id = "test-clock"

    def __init__(self, site, *, admit, after_dispatch=None, heading_deg=90.0, state=LocalizationState.ANCHORED):
        self.site = site
        self.clock = time.monotonic
        self.limits = SimpleNamespace(canonical_heading_deg=90.0, dead_band_cm=0)
        self.tracker = SimpleNamespace(
            health=SimpleNamespace(position=PAD, heading_deg=heading_deg, state=state, measured_at=None,
                                   source="mapped_joint_tags", xy_uncertainty_cm=5.0),
            profile=SimpleNamespace(max_xy_uncertainty_cm=30.0))
        self.admit, self.after_dispatch = admit, after_dispatch or (lambda plan, sup: None)
        self.prepared, self.dispatched, self.alignments = [], [], []
        self.last_preemption = None
        self._observations = 0

    def move(self, **changes):
        self.tracker.health = SimpleNamespace(**{**vars(self.tracker.health), **changes})

    def prepare(self, *, snapshot_version, checkpoint=None, provider_wait_s=0, replan=None, zone_admission=False,
                **_):
        self.prepared.append(checkpoint.checkpoint_id)
        transition, reason = self.admit(checkpoint, self)
        return SimpleNamespace(transition=transition, reason=reason,
                               request=SimpleNamespace(pose=self.tracker.health, checkpoint=checkpoint,
                                                       snapshot_version=snapshot_version),
                               pose_forecast=SimpleNamespace(reasons=("xy_uncertainty",)))

    def dispatch(self, admission, executor):
        plan = admission.request.checkpoint
        self.dispatched.append(plan.checkpoint_id)
        observations = []
        for c in plan.commands:
            if c.kind == "CAPTURE":
                self._observations += 1
                observations.append(f"obs-{self._observations}")
        pose = self.after_dispatch(plan, self)
        states = tuple(CommandState(c, "COMPLETED", 0.0, 1.0) for c in plan.commands)
        return CheckpointOutcome("COMPLETED", states, tuple(observations), None,
                                 plan.target if pose is None else pose, True)

    def heading_alignment(self, max_error_deg=None):
        self.alignments.append(max_error_deg)
        delta = abs((90.0 - self.tracker.health.heading_deg + 180) % 360 - 180)
        if round(delta) < 1 or delta > (10.0 if max_error_deg is None else max_error_deg):
            return None
        rotate = command("ROTATE", degrees=delta)
        health = self.tracker.health
        return CheckpointPlan("heading-alignment", health.position, health.position, health.heading_deg, 90.0,
                              (rotate,), rotate.upper_bound_s)

    def run_wait(self, operation, *, snapshot_version, timeout_s, executor):
        return SimpleNamespace(status="COMPLETED", value=operation(threading.Event()))

    def refresh_failure(self, executor):
        raise AssertionError("no refresh failure is expected")


def _moves(plan, sup):
    """Default flight physics: a plan ends at its target, facing its terminal
    heading (a heading alignment ends facing the tags)."""
    sup.move(position=plan.target, heading_deg=plan.terminal_heading_deg)


def _rig(tmp_path, supervisor, *, transit=True):
    store = RoomStore.open(tmp_path / "room.sqlite3")
    store.create_session(SESSION, "count", CapabilityProfile(()))
    site = supervisor.site
    captured = []

    def sweep_leg(index, offset):
        def resolve(candidate, snapshot):
            resolved = resolve_pose_id(site, candidate.candidate_id.rsplit(":", 1)[-1])
            health = supervisor.tracker.health
            heading = resolved.yaw_deg if index == len(OFFSETS) - 1 else resolved.yaw_deg + offset
            capture = command("CAPTURE", index=0, look_deg=offset)
            return CheckpointPlan(candidate.candidate_id, health.position,
                                  Position(resolved.x_cm, resolved.y_cm, resolved.z_cm), health.heading_deg,
                                  heading, (capture,), capture.upper_bound_s)
        return resolve

    def resolve_full(candidate, snapshot):
        resolved = resolve_pose_id(site, candidate.candidate_id.rsplit(":", 1)[-1])
        health = supervisor.tracker.health
        captures = tuple(command("CAPTURE", index=i, look_deg=o) for i, o in enumerate(OFFSETS))
        return CheckpointPlan(candidate.candidate_id, health.position,
                              Position(resolved.x_cm, resolved.y_cm, resolved.z_cm), health.heading_deg,
                              resolved.yaw_deg, captures, sum(c.upper_bound_s for c in captures))

    def resolve_transit(pose_id):
        resolved = resolve_pose_id(site, pose_id)
        health = supervisor.tracker.health
        settle = command("SETTLE", after="move")
        return CheckpointPlan(f"TRANSIT:{pose_id}", health.position,
                              Position(resolved.x_cm, resolved.y_cm, resolved.z_cm), health.heading_deg,
                              resolved.yaw_deg, (settle,), settle.upper_bound_s)

    results = {}

    def capture(candidate, outcome, snapshot):
        version = store.session_version(SESSION)
        items = []
        for observation_id in outcome.observation_ids:
            results[observation_id] = make_result(tmp_path, SESSION, observation_id)
            captured.append(observation_id)
            items.append(CapturedRequest(replace(results[observation_id].request, snapshot_version=version), 90.0))
        return tuple(items)

    def provide(request):
        return replace(results[request.observation_id], request=request)

    controller = RoomController(
        store, Judge(), supervisor=supervisor, flight_executor=SimpleNamespace(state=ExecutorState.AIRBORNE),
        resolve_checkpoint=resolve_full, resolve_sweep_checkpoints=tuple(sweep_leg(i, o) for i, o in enumerate(OFFSETS)),
        resolve_transit_checkpoint=resolve_transit if transit else None,
        perception_capture=capture, perception_provider=provide, provider_wait_s=1)
    return controller, store, captured


def _observe(controller, store, pose_id):
    candidate = SimpleNamespace(candidate_id=f"OBSERVE:g1:{pose_id}", kind=CandidateKind.OBSERVE,
                                relevant_state_sha256="0" * 64)
    snapshot = SimpleNamespace(session_id=SESSION, pose_revision=0)
    return controller._execute_observation(SESSION, candidate, snapshot, store.session_version(SESSION), "corr")


def _looks(sup, pose_id):
    return [c for c in sup.dispatched if c == f"OBSERVE:g1:{pose_id}"]


def _refused_from_the_pad(target):
    """Look 1 of ``target`` is refused on drift unless the tracker sits at a
    declared center pose (or already at the target: looks 2 and 3); every
    other checkpoint is admitted."""
    site = canningst_site()
    reachable = {Position(r.x_cm, r.y_cm, r.z_cm)
                 for r in (resolve_pose_id(site, p) for p in (CENTER_FRONT, CENTER_REAR, target))}

    def admit(plan, sup):
        if plan.checkpoint_id == f"OBSERVE:g1:{target}" and sup.tracker.health.position not in reachable:
            return "REFUSE", "LOCALIZATION_RECOVERY_REQUIRED"
        return "CONTINUE", "ADMITTED"
    return admit


# --- Feature A: fix-stop routing ---------------------------------------------

@pytest.mark.parametrize("heading_deg", [90.0, 95.0])
def test_a_zone_refused_on_drift_flies_one_fix_stop_then_is_observed(tmp_path, heading_deg):
    sup = ScriptedSupervisor(canningst_site(), admit=_refused_from_the_pad(LEFT_FRONT), after_dispatch=_moves,
                             heading_deg=heading_deg)
    controller, store, captured = _rig(tmp_path, sup)

    outcome = _observe(controller, store, LEFT_FRONT)

    assert outcome["code"] == "OBSERVE_SUCCEEDED", outcome
    transits = [c for c in sup.dispatched if c.startswith("TRANSIT:")]
    # Nearest stop to the zone first: center-front (declared before left-rear, same distance).
    assert transits == [f"TRANSIT:{CENTER_FRONT}"]
    aligned = ["heading-alignment"] if heading_deg != 90.0 else []
    assert sup.dispatched == [*aligned, f"TRANSIT:{CENTER_FRONT}", *[f"OBSERVE:g1:{LEFT_FRONT}"] * 3]
    assert sup.alignments == [None]  # the existing align retry ran first, once
    assert captured == ["obs-1", "obs-2", "obs-3"]
    assert f"re-anchored via {CENTER_FRONT} on the way" in outcome["detail"]


def test_a_zone_directly_admitted_flies_no_transit(tmp_path):
    sup = ScriptedSupervisor(canningst_site(), admit=lambda plan, sup: ("CONTINUE", "ADMITTED"),
                             after_dispatch=_moves)
    controller, store, _ = _rig(tmp_path, sup)
    outcome = _observe(controller, store, LEFT_FRONT)
    assert outcome["code"] == "OBSERVE_SUCCEEDED" and "detail" not in outcome
    assert not any(c.startswith("TRANSIT:") for c in sup.prepared)


def test_no_admitted_fix_stop_keeps_the_zone_refusal_and_flies_nothing(tmp_path):
    def admit(plan, sup):
        if plan.checkpoint_id.startswith("TRANSIT:"):
            return "RECOVER", "LOCALIZATION_RECOVERY_REQUIRED"
        return "REFUSE", "LOCALIZATION_RECOVERY_REQUIRED"
    sup = ScriptedSupervisor(canningst_site(), admit=admit, after_dispatch=_moves)
    controller, store, _ = _rig(tmp_path, sup)

    outcome = _observe(controller, store, LEFT_FRONT)

    assert outcome["code"] == "ZONE_REFUSED"
    assert outcome["detail"].startswith(_ZONE_REFUSAL_DETAIL["LOCALIZATION_RECOVERY_REQUIRED"] + " (look 1)")
    assert "re-anchored" not in outcome["detail"]
    assert sup.dispatched == []
    # At most two stops are asked for, nearest first.
    assert [c for c in sup.prepared if c.startswith("TRANSIT:")] == [f"TRANSIT:{CENTER_FRONT}",
                                                                      f"TRANSIT:{LEFT_REAR}"]


def test_the_second_candidate_stop_is_flown_when_the_first_is_not_admitted(tmp_path):
    def admit(plan, sup):
        if plan.checkpoint_id == f"TRANSIT:{CENTER_FRONT}":
            return "RETURN_NOW", "PROJECTED_TERMINATION_PATH_INVALID"
        if plan.checkpoint_id.startswith("OBSERVE:") and sup.tracker.health.position == PAD:
            return "REFUSE", "LOCALIZATION_RECOVERY_REQUIRED"
        return "CONTINUE", "ADMITTED"
    sup = ScriptedSupervisor(canningst_site(), admit=admit, after_dispatch=_moves)
    controller, store, _ = _rig(tmp_path, sup)
    outcome = _observe(controller, store, LEFT_FRONT)
    assert outcome["code"] == "OBSERVE_SUCCEEDED"
    assert [c for c in sup.dispatched if c.startswith("TRANSIT:")] == [f"TRANSIT:{LEFT_REAR}"]
    assert f"re-anchored via {LEFT_REAR}" in outcome["detail"]


def test_a_zone_still_refused_after_the_fix_stop_is_refused_without_a_second_transit(tmp_path):
    def admit(plan, sup):
        if plan.checkpoint_id.startswith("TRANSIT:"):
            return "CONTINUE", "ADMITTED"
        return "REFUSE", "LOCALIZATION_RECOVERY_REQUIRED"
    sup = ScriptedSupervisor(canningst_site(), admit=admit, after_dispatch=_moves)
    controller, store, _ = _rig(tmp_path, sup)

    outcome = _observe(controller, store, LEFT_FRONT)

    assert outcome["code"] == "ZONE_REFUSED"
    assert outcome["detail"].startswith(_ZONE_REFUSAL_DETAIL["LOCALIZATION_RECOVERY_REQUIRED"] + " (look 1)")
    assert f"re-anchored via {CENTER_FRONT} on the way" in outcome["detail"]
    assert sup.dispatched == [f"TRANSIT:{CENTER_FRONT}"]
    assert sum(c.startswith("TRANSIT:") for c in sup.prepared) == 1


@pytest.mark.parametrize("state", [LocalizationState.RECOVERY_REQUIRED, LocalizationState.LOST])
def test_no_transit_without_an_anchored_or_coasting_pose(tmp_path, state):
    sup = ScriptedSupervisor(canningst_site(), admit=_refused_from_the_pad(LEFT_FRONT), after_dispatch=_moves,
                             state=state)
    controller, store, _ = _rig(tmp_path, sup)
    outcome = _observe(controller, store, LEFT_FRONT)
    assert outcome["code"] == "ZONE_REFUSED"
    assert not any(c.startswith("TRANSIT:") for c in sup.prepared)


def test_without_a_transit_resolver_the_refusal_is_unchanged(tmp_path):
    sup = ScriptedSupervisor(canningst_site(), admit=_refused_from_the_pad(LEFT_FRONT), after_dispatch=_moves)
    controller, store, _ = _rig(tmp_path, sup, transit=False)
    outcome = _observe(controller, store, LEFT_FRONT)
    assert outcome["code"] == "ZONE_REFUSED" and sup.dispatched == []


def test_an_incomplete_transit_leg_keeps_the_zone_refusal(tmp_path):
    sup = ScriptedSupervisor(canningst_site(), admit=_refused_from_the_pad(LEFT_FRONT), after_dispatch=_moves)
    original = sup.dispatch

    def dispatch(admission, executor):
        result = original(admission, executor)
        if admission.request.checkpoint.checkpoint_id.startswith("TRANSIT:"):
            sup.move(position=PAD)
            return replace(result, status="RECOVERY_PENDING", completion_known=False)
        return result
    sup.dispatch = dispatch
    controller, store, _ = _rig(tmp_path, sup)
    outcome = _observe(controller, store, LEFT_FRONT)
    assert outcome["code"] == "ZONE_REFUSED"
    assert f"the fix stop at {CENTER_FRONT} ended RECOVERY_PENDING" in outcome["detail"]
    assert sup.dispatched == [f"TRANSIT:{CENTER_FRONT}"]


# --- Feature B: arrival heading retry ----------------------------------------

def _arrives(*, heading_after_last_look, off_cm=0.0):
    """Every look arrives at its target; the last look of each sweep ends at
    ``heading_after_last_look(attempt)`` and ``off_cm`` off in x."""
    attempts = []

    def after(plan, sup):
        if plan.checkpoint_id == "heading-alignment":
            sup.move(heading_deg=90.0)
            return None
        sup.move(position=plan.target, heading_deg=plan.terminal_heading_deg)
        last = plan.terminal_heading_deg == 90.0 and plan.checkpoint_id.startswith("OBSERVE:")
        if last and len([c for c in sup.dispatched if c.startswith("OBSERVE:")]) % 3 == 0:
            attempts.append(plan)
            sup.move(heading_deg=heading_after_last_look(len(attempts)))
            return Position(plan.target.x_cm + off_cm, plan.target.y_cm, plan.target.z_cm)
        return None
    return after


def test_a_heading_only_arrival_miss_is_realigned_and_every_look_retaken(tmp_path):
    sup = ScriptedSupervisor(canningst_site(), admit=lambda plan, sup: ("CONTINUE", "ADMITTED"),
                             after_dispatch=_arrives(heading_after_last_look=lambda n: 102.0 if n == 1 else 92.0))
    controller, store, captured = _rig(tmp_path, sup)

    outcome = _observe(controller, store, CENTER_FRONT)

    assert outcome["code"] == "OBSERVE_SUCCEEDED", outcome
    assert sup.alignments == [30.0]
    assert sup.dispatched == [*[f"OBSERVE:g1:{CENTER_FRONT}"] * 3, "heading-alignment",
                              *[f"OBSERVE:g1:{CENTER_FRONT}"] * 3]
    # Only the retaken frames are perceived and credited, never the misaimed ones.
    assert captured == ["obs-4", "obs-5", "obs-6"]
    assert "looks retaken" in outcome["detail"] and "heading 12 deg" in outcome["detail"]


def test_a_second_heading_miss_is_a_tool_failure_with_the_arrival_miss(tmp_path):
    sup = ScriptedSupervisor(canningst_site(), admit=lambda plan, sup: ("CONTINUE", "ADMITTED"),
                             after_dispatch=_arrives(heading_after_last_look=lambda n: 102.0))
    controller, store, captured = _rig(tmp_path, sup)

    outcome = _observe(controller, store, CENTER_FRONT)

    assert outcome["code"] == "TOOL_FAILURE"
    assert outcome["detail"].startswith("the drone arrived 0 cm (height 0 cm, heading 12 deg) away from the pose")
    assert sup.alignments == [30.0]
    assert len(_looks(sup, CENTER_FRONT)) == 6 and sup.dispatched.count("heading-alignment") == 1
    assert captured == []


@pytest.mark.parametrize("heading", [90.0, 102.0])
def test_a_position_miss_is_an_immediate_tool_failure(tmp_path, heading):
    sup = ScriptedSupervisor(canningst_site(), admit=lambda plan, sup: ("CONTINUE", "ADMITTED"),
                             after_dispatch=_arrives(heading_after_last_look=lambda n: heading, off_cm=50.0))
    controller, store, captured = _rig(tmp_path, sup)

    outcome = _observe(controller, store, CENTER_FRONT)

    assert outcome["code"] == "TOOL_FAILURE"
    assert outcome["detail"].startswith("the drone arrived 50 cm")
    assert sup.alignments == [] and len(_looks(sup, CENTER_FRONT)) == 3 and captured == []


def test_a_heading_miss_the_turn_cannot_correct_is_a_tool_failure(tmp_path):
    """Beyond the corrective turn's bound there is no alignment plan: the miss stands."""
    sup = ScriptedSupervisor(canningst_site(), admit=lambda plan, sup: ("CONTINUE", "ADMITTED"),
                             after_dispatch=_arrives(heading_after_last_look=lambda n: 135.0))
    controller, store, captured = _rig(tmp_path, sup)
    outcome = _observe(controller, store, CENTER_FRONT)
    assert outcome["code"] == "TOOL_FAILURE" and "heading 45 deg" in outcome["detail"]
    assert sup.alignments == [30.0] and len(_looks(sup, CENTER_FRONT)) == 3


# ---------------------------------------------------------------------------
# Integration: the real CanningSt site and supervisor, from the pad anchor
# ---------------------------------------------------------------------------

def test_from_the_pad_left_front_is_refused_and_a_transit_to_center_front_is_admitted(tmp_path):
    supervisor, store, link, binding = _airborne_supervisor(tmp_path)
    try:
        source = lambda: supervisor.tracker.health
        profile = binding.calibration_profiles[0]
        sweep = wiring.make_chained_sweep(site=binding.site, profile=profile, pose_source=source,
                                          limits=binding.limits, sweep_offsets_deg=OFFSETS)
        transit = wiring.make_transit_checkpoint(site=binding.site, profile=profile, pose_source=source,
                                                 limits=binding.limits)
        look_1 = sweep[0](SimpleNamespace(candidate_id=f"OBSERVE:g1:{LEFT_FRONT}"), None)
        refused = supervisor.prepare(snapshot_version=store.session_version("run-0001"), checkpoint=look_1,
                                     zone_admission=True)
        assert refused.transition == "REFUSE" and refused.reason == "LOCALIZATION_RECOVERY_REQUIRED"
        assert "xy_uncertainty" in refused.pose_forecast.reasons

        # The controller's stop choice for this zone, against the real site.
        controller = RoomController(store, Judge(), supervisor=supervisor,
                                    flight_executor=SimpleNamespace(state=ExecutorState.AIRBORNE))
        stops = controller._transit_stops(resolve_pose_id(binding.site, LEFT_FRONT))
        assert stops[0] == CENTER_FRONT and len(stops) == 2

        admitted = supervisor.prepare(snapshot_version=store.session_version("run-0001"),
                                      checkpoint=transit(CENTER_FRONT), zone_admission=True)
        assert admitted.transition == "CONTINUE", (admitted.transition, admitted.reason)
    finally:
        store.close()
        link.close()


def test_the_arrival_realignment_corrects_past_the_canonical_tolerance_only_when_asked(tmp_path):
    """A 12 deg arrival error is past the 10 deg canonical tolerance, so the
    align-to-tags retry offers no turn for it; the arrival retry's bound does."""
    supervisor, store, link, binding = _airborne_supervisor(tmp_path)
    try:
        supervisor.tracker._health = replace(supervisor.tracker.health, heading_deg=102.0)
        assert supervisor.heading_alignment() is None
        plan = supervisor.heading_alignment(max_error_deg=30.0)
        assert plan is not None and [c.kind for c in plan.commands] == ["ROTATE"]
        assert dict(plan.commands[0].arguments)["direction"] == "cw"
        assert dict(plan.commands[0].arguments)["degrees"] == pytest.approx(12.0)
        supervisor.tracker._health = replace(supervisor.tracker.health, heading_deg=135.0)
        assert supervisor.heading_alignment(max_error_deg=30.0) is None
    finally:
        store.close()
        link.close()
