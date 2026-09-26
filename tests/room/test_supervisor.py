"""Offline safety admission, durable preemption and measured-state projection."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
from threading import Event
from types import SimpleNamespace
import sqlite3

import pytest

from sentinel.cube.checkpoint import CheckpointPlan, CommandLimits, PlannedCommand
from sentinel.cube.energy import EnergyProfile, EnergySample, ExecutionMode, ReserveGuard
from sentinel.cube.localization import LocalizationState
from sentinel.cube.step_executor import CheckpointOutcome, CommandState, ExecutorState, TerminalOutcome
from sentinel.models import Position, Telemetry
from sentinel.room.models import CapabilityGrant, CapabilityProfile, canonical_grant_sha256
from sentinel.room.queries import ReadQueries
from sentinel.room.site import ContingencyKind, ContingencyTrigger, Volume3D
from sentinel.room.store import RoomStore, VersionConflict
from sentinel.room.supervisor import SafetySupervisor, SupervisorRequest, calibration_hashes
from tests.cube.test_localization import tracker
from tests.cube.test_recovery import assurance, site


def checkpoint(pose, *commands):
    return CheckpointPlan("optional", pose.position, pose.position, pose.heading_deg, pose.heading_deg,
                          commands, sum(c.upper_bound_s for c in commands))


def command(kind="CAPTURE", seconds=1, **args):
    return PlannedCommand.from_mapping(kind, args, upper_bound_s=seconds, interruptible=kind != "MOVE")


class Executor:
    def __init__(self, store, clock):
        self.store, self.clock = store, clock
        self.state = ExecutorState.AIRBORNE
        self.calls = []
        # Room controller fix round 3 (F3): the reported post-checkpoint
        # measured position, mutable per-test/per-dispatch. Defaults to the
        # tracker's canonical fixture position so every existing test that
        # never touches this keeps its prior behavior; a caller that wants
        # to simulate a checkpoint actually reaching a specific pose (see
        # ``make_resolve_checkpoint`` in test_count_return.py) sets this
        # before dispatch.
        self.reported_pose = Position(100, 100, 100)

    def execute(self, plan):
        assert self.store.events("session")[-1].event_type == "SAFETY_ASSESSED"
        self.calls.extend(c.kind for c in plan.commands)
        # Room controller fix round 2 (Q5): report a real per-command record,
        # not a hardcoded empty tuple -- the controller now checks
        # command_states for a completed CAPTURE belonging to the dispatched
        # plan, so this stub must actually reflect which commands it ran.
        states = tuple(CommandState(c, "COMPLETED", 0.0, 1.0) for c in plan.commands)
        return CheckpointOutcome("COMPLETED", states, ("observation",), Telemetry(76, 100),
                                 self.reported_pose, True)

    def return_home(self, plan):
        self.state = ExecutorState.RETURNING
        return self.execute(plan)

    def apply_reanchor(self, pose, *, persist):
        persist()

    def land(self, *, upper_bound_s=None, deadline=None):
        assert self.store.events("session")[-1].event_type == "SAFETY_ASSESSED"
        self.calls.append("LAND")
        self.state = ExecutorState.TERMINAL
        return TerminalOutcome("LANDED", "REQUESTED", Position(100, 100, 0), 75, self.clock())


@pytest.fixture
def safety(tmp_path):
    value, clock = tracker(max_camera_age_s=2000, max_fix_age_s=2000,
                           max_translation_cm=2000, max_xy_uncertainty_cm=2000,
                           max_heading_uncertainty_deg=2000)
    room_site = site()
    profile = EnergyProfile("energy", room_site.cube_site_sha256, "aircraft", .01, 1, 20, 2,
                            "SIMULATION_ONLY")
    limits = CommandLimits(trim_rounds=0, localise=False)
    hashes = calibration_hashes(profile, value.profile, limits, room_site)
    grant = CapabilityGrant("ADAPTIVE_FLIGHT", "SIMULATION_ONLY", room_site.cube_site_sha256,
                            "aircraft", hashes, ("simulation",), "2099-01-01T00:00:00Z", "")
    grant = replace(grant, grant_sha256=canonical_grant_sha256(grant))
    capabilities = CapabilityProfile((grant,))
    store = RoomStore.open(tmp_path / "room.sqlite3")
    store.create_session("session", "read drone state", capabilities)
    samples = {"pose": value.health, "energy": EnergySample(80, clock())}
    refreshes = []

    def refresh():
        refreshes.append(clock())
        return samples["pose"], samples["energy"]

    emergencies = []

    def emergency(executor):
        assert store.events("session")[-1].event_type == "SAFETY_ASSESSED"
        emergencies.append(True)
        executor.state = ExecutorState.TERMINAL
        return TerminalOutcome("EMERGENCY_ABORTED", "NAVIGATION_UNAVAILABLE", None, None, clock())

    supervisor = SafetySupervisor(store=store, session_id="session", site=room_site,
        tracker=value, reserve=ReserveGuard(profile, clock), limits=limits,
        aircraft_id="aircraft", mode=ExecutionMode.SIMULATION, clock=clock,
        refresh=refresh, emergency=emergency,
        wall_clock=lambda: datetime(2026, 9, 23, tzinfo=timezone.utc))
    for trigger in ContingencyTrigger:
        store.append_contingency_assurance(assurance(assurance_id=trigger.value,
            contingency_id=trigger.value, clock_domain_id=supervisor.clock_domain_id),
            expected_version=store.session_version("session"))
    yield supervisor, store, clock, samples, refreshes, emergencies
    store.close()


def request(safety, plan=None, wait=0):
    supervisor, store, _, samples, *_ = safety
    return SupervisorRequest(store.session_version("session"), plan, wait,
                             samples["pose"], samples["energy"])


def real_executor(safety, *, measured=None, client=None):
    from sentinel.cube.checkpoint import CommandOutcome
    from sentinel.cube.step_executor import HomeState, StepExecutor
    from sentinel.dry_run import DryRunClient
    from sentinel.envelope import FlightEnvelope
    from sentinel.runner import FlightTestRunner
    supervisor, store, clock, samples, *_ = safety
    measured = measured or HomeState(Position(100, 100, 100), 90, "home-route", clock())
    runner = FlightTestRunner(client or DryRunClient(), "dry-run", retain_video_stream=True,
        envelope=FlightEnvelope(0, 200, 0, 200, 20, 200, position=measured.pose))
    commands = []

    def sink(node, cancel):
        commands.append(node)
        if node.kind == "READ_TELEMETRY":
            return Telemetry(80, round(runner.envelope.position.z_cm))
        return CommandOutcome()

    executor = StepExecutor(runner, clock=clock, measure_home=lambda definition: measured, command_sink=sink)

    def refresh():
        supervisor.tracker._health = replace(supervisor.tracker.health, position=runner.envelope.position)
        return supervisor.tracker.health, samples["energy"]

    supervisor.refresh = refresh

    def emergency(active):
        assert store.events("session")[-1].event_type == "SAFETY_ASSESSED"
        safety[-1].append(True)
        active.runner.trigger_emergency()
        return active.land()

    supervisor.emergency = emergency
    return executor, commands


def test_site_executor_supervisor_start_returns_to_measured_airborne_home(safety):
    from sentinel.cube.step_executor import HomeState
    supervisor, store, clock, samples, *_ = safety
    measured = HomeState(Position(100, 100, 120), 90, "home-route", clock())
    executor, commands = real_executor(safety, measured=measured)
    try:
        assert supervisor.start(executor) == measured
        current = supervisor.prepare(snapshot_version=store.session_version("session"))
        assert current.transition == "CONTINUE"
        plan = current.termination_plan
        assert plan.target == supervisor.site.home.nominal_pose == Position(100, 100, 0)
        assert plan.terminal_heading_deg == measured.heading_deg
        assert not any(c.kind == "MOVE" for c in plan.commands)
        result = supervisor.dispatch(replace(current, transition="RETURN_NOW", invalidates_snapshot=True), executor)
        assert result.state == "LANDED"
        assert not any(c.kind == "MOVE" for c in commands)
        assert executor.runner.client.commands.count("takeoff") == executor.runner.client.commands.count("land") == 1
    finally:
        executor.close()


def test_return_plan_restores_the_measured_home_heading(safety):
    from sentinel.cube.step_executor import HomeState
    supervisor, store, clock, samples, *_ = safety
    measured = HomeState(Position(100, 100, 120), 95, "home-route", clock())
    executor, commands = real_executor(safety, measured=measured)
    try:
        executor.start(supervisor.site.home)
        supervisor._bind_home(executor)
        pose, _ = supervisor.refresh()
        plan = supervisor._termination(pose, supervisor.site)
        assert plan.terminal_heading_deg == measured.heading_deg
        assert plan.checkpoint_id == measured.route_id
        assert not any(c.kind == "MOVE" for c in plan.commands)
        rotation = plan.commands[-2]
        assert rotation.kind == "ROTATE"
        assert dict(rotation.arguments) == {"degrees": 5, "direction": "ccw"}
        assert plan.commands[-1].kind == "LAND"
        assert plan.target == supervisor.site.home.nominal_pose
    finally:
        executor.close()


def test_recovery_reanchors_real_executor_before_a_corrected_return(safety):
    from sentinel.cube.recovery import PoseReport, RecoveryController
    from tests.cube.test_localization import fix
    supervisor, store, clock, samples, *_ = safety
    executor, commands = real_executor(safety)
    try:
        supervisor.start(executor)
        supervisor.tracker._health = replace(supervisor.tracker.health,
            state=LocalizationState.RECOVERY_REQUIRED, estimator_failures=3)
        supervisor.recovery = RecoveryController(supervisor.tracker, clock=clock,
            burst_source=lambda action: tuple(PoseReport(str(i), fix(clock,
                position=Position(120, 100, 100))) for i in range(3)), energy_admits=lambda action: True)
        supervisor.mapped_heading_tag_ids = ((90, (1, 2)),)
        revision = executor.pose_revision
        current = supervisor.prepare(snapshot_version=store.session_version("session"))
        assert current.transition == "RECOVER"
        result = supervisor.dispatch(current, executor)
        assert result.status == "COMPLETED"
        assert executor.runner.envelope.position == supervisor.tracker.health.position == Position(120, 100, 100)
        assert executor.pose_revision > revision
        current = supervisor.prepare(snapshot_version=store.session_version("session"))
        assert current.transition == "CONTINUE"
        result = supervisor.dispatch(replace(current, transition="RETURN_NOW", invalidates_snapshot=True), executor)
        assert result.state == "LANDED"
        assert any(c.kind == "MOVE" and dict(c.arguments)["direction"] == "left" for c in commands)
        assert not any("STALE_RETURN_PLAN" in str(e.payload) for e in store.events("session"))
    finally:
        executor.close()


def test_failed_executor_reanchor_sync_after_durable_append_is_terminal(safety):
    from sentinel.cube.recovery import PoseReport, RecoveryController
    from tests.cube.test_localization import fix
    supervisor, store, clock, samples, _, emergencies = safety
    executor, commands = real_executor(safety)
    try:
        supervisor.start(executor)
        supervisor.tracker._health = replace(supervisor.tracker.health,
            state=LocalizationState.RECOVERY_REQUIRED, estimator_failures=3)
        supervisor.recovery = RecoveryController(supervisor.tracker, clock=clock,
            burst_source=lambda action: tuple(PoseReport(str(i), fix(clock,
                position=Position(120, 100, 100))) for i in range(3)), energy_admits=lambda action: True)
        supervisor.mapped_heading_tag_ids = ((90, (1, 2)),)

        def failed_sync(pose):
            assert any(e.event_type == "POSE_REANCHORED" for e in store.events("session"))
            raise RuntimeError("secret internal synchronization failure")

        executor._commit_pose_locked = failed_sync
        current = supervisor.prepare(snapshot_version=store.session_version("session"))
        result = supervisor.dispatch(current, executor)
        assert result.state == "EMERGENCY_ABORTED" and emergencies == [True]
        assert supervisor.tracker.health.state == LocalizationState.LOST
        assert executor.state == ExecutorState.TERMINAL
        assert not commands
        events = store.events("session")
        assert len([e for e in events if e.event_type == "POSE_REANCHORED"]) == 1
        assert any(e.payload.get("reason") == "REANCHOR_SYNC_FAILED" for e in events)
        assert not any("secret" in str(e.payload) for e in events)
    finally:
        executor.close()


def test_operator_emergency_preempts_reanchor_while_durable_append_is_blocked(safety):
    from threading import Thread
    from sentinel.cube.recovery import PoseReport, RecoveryController
    from sentinel.dry_run import DryRunClient
    from tests.cube.test_localization import fix
    supervisor, store, clock, samples, *_ = safety
    appended, release, emergency_sent = Event(), Event(), Event()

    class ObservedEmergency(DryRunClient):
        def emergency(self):
            result = super().emergency()
            emergency_sent.set()
            return result

    executor, commands = real_executor(safety, client=ObservedEmergency())
    supervisor.start(executor)
    supervisor.tracker._health = replace(supervisor.tracker.health,
        state=LocalizationState.RECOVERY_REQUIRED, estimator_failures=3)
    supervisor.recovery = RecoveryController(supervisor.tracker, clock=clock,
        burst_source=lambda action: tuple(PoseReport(str(i), fix(clock,
            position=Position(120, 100, 100))) for i in range(3)), energy_admits=lambda action: True)
    supervisor.mapped_heading_tag_ids = ((90, (1, 2)),)
    append = store.append_reanchor

    def blocked_append(*args, **kwargs):
        result = append(*args, **kwargs)
        appended.set()
        assert release.wait(3)
        return result

    store.append_reanchor = blocked_append
    current = supervisor.prepare(snapshot_version=store.session_version("session"))
    results = []
    worker = Thread(target=lambda: results.append(supervisor.dispatch(current, executor)), daemon=True)
    emergency = Thread(target=executor.runner.trigger_emergency, daemon=True)
    worker.start()
    try:
        assert appended.wait(1)
        emergency.start()
        assert emergency_sent.wait(1)
        assert executor.state == ExecutorState.TERMINAL
        assert not executor.command_lock.acquire(blocking=False)
    finally:
        release.set()
        worker.join(2)
        if emergency.ident is not None:
            emergency.join(1)
        executor.close()
    assert not worker.is_alive() and results[0].state == "EMERGENCY_ABORTED"
    assert executor.runner.envelope.position == Position(100, 100, 100)
    assert supervisor.tracker.health.state == LocalizationState.LOST
    events = store.events("session")
    assert len([e for e in events if e.event_type == "POSE_REANCHORED"]) == 1
    assert any(e.payload.get("reason") == "REANCHOR_PREEMPTED_AFTER_PERSISTENCE" for e in events)
    assert not commands and executor.runner.client.commands.count("emergency") == 1


def test_real_executor_can_recover_resume_and_recover_again(safety):
    from sentinel.cube.recovery import PoseReport, RecoveryController
    from tests.cube.test_localization import fix
    supervisor, store, clock, samples, *_ = safety
    executor, commands = real_executor(safety)
    actions = []

    def burst(action):
        actions.append(action)
        return tuple(PoseReport(str(i), fix(clock)) for i in range(3))

    supervisor.recovery = RecoveryController(supervisor.tracker, clock=clock,
        burst_source=burst, energy_admits=lambda action: True)
    supervisor.mapped_heading_tag_ids = ((90, (1, 2)),)
    try:
        supervisor.start(executor)
        for episode in range(2):
            supervisor.tracker._health = replace(supervisor.tracker.health,
                state=LocalizationState.RECOVERY_REQUIRED, estimator_failures=3)
            current = supervisor.prepare(snapshot_version=store.session_version("session"))
            assert current.transition == "RECOVER"
            result = supervisor.dispatch(current, executor)
            assert isinstance(result, CheckpointOutcome) and result.status == "COMPLETED"
            current = supervisor.prepare(snapshot_version=store.session_version("session"))
            assert current.transition == "CONTINUE"
            assert not supervisor.tracker.health.requires_route_validation
            assert not supervisor.tracker.health.requires_energy_validation
        assert [a.kind for a in actions] == ["STATIONARY_BURST", "STATIONARY_BURST"]
        assert len({a.action_id for a in actions}) == 2
        assert len([e for e in store.events("session") if e.event_type == "POSE_REANCHORED"]) == 2
    finally:
        executor.close()


def test_opportunistic_fix_after_recovery_resume_does_not_reconfirm_old_revision(safety):
    from sentinel.cube.recovery import PoseReport, RecoveryController
    from tests.cube.test_localization import fix
    supervisor, store, clock, samples, *_ = safety
    executor, commands = real_executor(safety)
    supervisor.recovery = RecoveryController(supervisor.tracker, clock=clock,
        burst_source=lambda action: tuple(PoseReport(str(i), fix(clock)) for i in range(3)),
        energy_admits=lambda action: True)
    supervisor.mapped_heading_tag_ids = ((90, (1, 2)),)
    try:
        supervisor.start(executor)
        supervisor.tracker._health = replace(supervisor.tracker.health,
            state=LocalizationState.RECOVERY_REQUIRED, estimator_failures=3)
        first = supervisor.prepare(snapshot_version=store.session_version("session"))
        assert supervisor.dispatch(first, executor).status == "COMPLETED"
        assert supervisor.tracker.health.pose_revision == 2
        assert supervisor.prepare(snapshot_version=store.session_version("session")).transition == "CONTINUE"
        clock.now += .1
        samples["energy"] = EnergySample(80, clock())
        assert supervisor.tracker.accept_fix(fix(clock)).pose_revision == 3
        current = supervisor.prepare(snapshot_version=store.session_version("session"))
        assert current.transition == "CONTINUE"
        assert current.request.pose.pose_revision == 3
        assert not supervisor.tracker.health.requires_route_validation
        assert not supervisor.tracker.health.requires_energy_validation
        supervisor.tracker._health = replace(supervisor.tracker.health,
            state=LocalizationState.RECOVERY_REQUIRED, estimator_failures=3)
        next_episode = supervisor.prepare(snapshot_version=store.session_version("session"))
        assert supervisor.dispatch(next_episode, executor).status == "COMPLETED"
        assert len([e for e in store.events("session") if e.event_type == "POSE_REANCHORED"]) == 2
    finally:
        executor.close()


@pytest.mark.parametrize("source_state", [LocalizationState.ANCHORED, LocalizationState.COASTING])
@pytest.mark.parametrize("immediate_fix", [False, True])
def test_proactive_provider_wait_can_start_recovery_after_an_earlier_resume(safety, source_state, immediate_fix):
    from sentinel.cube.recovery import PoseReport, RecoveryController
    from tests.cube.test_localization import fix
    supervisor, store, clock, samples, _, emergencies = safety
    supervisor.tracker.profile = replace(supervisor.tracker.profile, max_camera_age_s=80)
    supervisor.validate_bindings = lambda active: None  # Isolate the changed synthetic drift bound.
    executor, commands = real_executor(safety)
    actions = []

    def burst(action):
        actions.append(action)
        return tuple(PoseReport(str(i), fix(clock)) for i in range(3))

    supervisor.recovery = RecoveryController(supervisor.tracker, clock=clock,
        burst_source=burst, energy_admits=lambda action: True)
    supervisor.mapped_heading_tag_ids = ((90, (1, 2)),)
    try:
        supervisor.start(executor)
        supervisor.tracker._health = replace(supervisor.tracker.health,
            state=LocalizationState.RECOVERY_REQUIRED, estimator_failures=3)
        first = supervisor.prepare(snapshot_version=store.session_version("session"))
        assert supervisor.dispatch(first, executor).status == "COMPLETED"
        assert supervisor.prepare(snapshot_version=store.session_version("session")).transition == "CONTINUE"
        if immediate_fix:
            clock.now += .1
            samples["energy"] = EnergySample(80, clock())
            assert supervisor.tracker.accept_fix(fix(clock)).pose_revision == 3
            assert supervisor.tracker.health.requires_route_validation
            assert supervisor.tracker.health.requires_energy_validation
        supervisor.tracker._health = replace(supervisor.tracker.health, state=source_state)
        proactive = supervisor.prepare(snapshot_version=store.session_version("session"), provider_wait_s=100)
        assert proactive.transition == "RECOVER"
        assert proactive.request.pose.state == source_state
        assert proactive.pose_forecast.projected.state == LocalizationState.RECOVERY_REQUIRED
        result = supervisor.dispatch(proactive, executor)
        assert isinstance(result, CheckpointOutcome) and result.status == "COMPLETED"
        assert [a.kind for a in actions] == ["STATIONARY_BURST", "STATIONARY_BURST"]
        assert not emergencies and not commands
        evidence = next(e for e in store.events("session") if e.event_type == "RECOVERY_FORECAST_ADMITTED")
        assert evidence.payload["forecast"]["projected"]["state"] == "RECOVERY_REQUIRED"
        assert evidence.payload["pose"]["pose_revision"] == proactive.request.pose.pose_revision
        assert evidence.payload["validated_pose_revision"] == proactive.request.pose.pose_revision
        assert evidence.payload["route_validated"] is True
        assert evidence.payload["energy_validated"] is True
        assert evidence.payload["session_id"] == "session"
        assert evidence.payload["clock_domain_id"] == supervisor.clock_domain_id
        assessment = next(e for e in store.events("session") if e.event_id == evidence.payload["assessment_event_id"])
        assert assessment.event_type == "SAFETY_ASSESSED" and assessment.payload["transition"] == "RECOVER"
    finally:
        executor.close()


@pytest.mark.parametrize("path", ["checkpoint", "contingency"])
def test_supervisor_passes_landing_bound_and_session_deadline_to_real_executor(safety, path):
    from sentinel.dry_run import DryRunClient
    supervisor, store, clock, samples, *_ = safety
    entered, release = Event(), Event()

    class BlockedLanding(DryRunClient):
        def land(self):
            entered.set()
            clock.now += .1
            release.wait(3)
            return super().land()

    executor, commands = real_executor(safety, client=BlockedLanding())
    supervisor.landing_upper_bound_s = .05
    supervisor.reserve = ReserveGuard(replace(supervisor.reserve.profile, percent_per_second_upper=.1), clock)
    supervisor.validate_bindings = lambda active: None  # Changed synthetic landing calibration.
    captured = []
    original_land = executor.land

    def land(**kwargs):
        captured.append(kwargs)
        return original_land(**kwargs)

    executor.land = land
    try:
        supervisor.start(executor)
        executor.deadline = clock() + .5
        if path == "contingency":
            samples["pose"] = supervisor.tracker.health
            supervisor.refresh = lambda: (samples["pose"], samples["energy"])
            supervisor.recovery = None
            # Force the measured energy reserve below the return requirement
            # while retaining the independently affordable native descent.
            clock.now += .01
            samples["energy"] = EnergySample(22, clock())
        current = supervisor.prepare(snapshot_version=store.session_version("session"),
            checkpoint=checkpoint(supervisor.tracker.health, command("LAND", seconds=.05)) if path == "checkpoint" else None)
        if path == "contingency":
            assert current.transition == "LAND_CONTINGENCY" and current.contingency_id is not None
        result = supervisor.dispatch(current, executor)
        assert captured[0] == {"upper_bound_s": .05, "deadline": executor.deadline}
        assert entered.is_set() and result.state == "COMMAND_COMPLETION_UNKNOWN"
        assert result.completed_at is None
        assert not executor.command_lock.acquire(blocking=False)
        state = store.events("session")[-1].payload["state"]
        assert state["airborne"] is None and state["completion_known"] is False
    finally:
        release.set()
        executor.close()


@pytest.mark.parametrize("change", ["valid", "fresh_landing_decision", "expired", "revoked", "stale_telemetry", "descent_geometry",
                                    "wrong_trigger", "low_energy", "emergency"])
def test_exhausted_recovery_uses_its_contingency_only_after_fresh_descent_admission(safety, change):
    from sentinel.cube.recovery import RecoveryController
    supervisor, store, clock, samples, _, emergencies = safety
    supervisor.tracker.profile = replace(supervisor.tracker.profile,
        max_camera_age_s=80, max_recovery_attempts=1)
    supervisor.validate_bindings = lambda active: None  # Changed synthetic recovery calibration.
    if change == "fresh_landing_decision":
        supervisor.site = replace(supervisor.site, terminal_contingencies=tuple(
            replace(entry, max_assurance_age_ms=100000) for entry in supervisor.site.terminal_contingencies))
    executor, commands = real_executor(safety)
    recovery = RecoveryController(supervisor.tracker, clock=clock,
        burst_source=lambda action: (), energy_admits=lambda action: True)
    supervisor.recovery = recovery
    supervisor.mapped_heading_tag_ids = ((90, (1, 2)),)
    decisions, landings, preparations = [], [], []
    next_action, land = recovery.next_action, executor.land
    prepare = supervisor.prepare

    def observed_prepare(**kwargs):
        outcome = prepare(**kwargs)
        preparations.append(outcome)
        return outcome

    supervisor.prepare = observed_prepare

    def decide(*args):
        action = next_action(*args)
        if action.kind == "CONTINGENCY":
            decisions.append(action)
            if change == "fresh_landing_decision":
                clock.now = 30
                samples["energy"] = EnergySample(41, clock())
            elif change == "expired":
                clock.now = 15.1
                samples["energy"] = EnergySample(80, clock())
            elif change == "revoked":
                store.append_contingency_assurance(assurance(assurance_id="NAVIGATION_UNAVAILABLE",
                    state="REVOKED", clock_domain_id=supervisor.clock_domain_id),
                    expected_version=store.session_version("session"))
            elif change == "stale_telemetry":
                samples["energy"] = EnergySample(80, 1)
            elif change == "descent_geometry":
                narrow = ((0, 0), (116.5, 0), (116.5, 200), (0, 200))
                supervisor.site = replace(supervisor.site, terminal_contingencies=tuple(
                    replace(entry, landing_polygon_xy_cm=narrow)
                    if entry.contingency_id == action.contingency_id else entry
                    for entry in supervisor.site.terminal_contingencies))
            elif change == "wrong_trigger":
                action = replace(action, contingency_id="RETURN_ENERGY_UNAVAILABLE")
            elif change == "low_energy":
                clock.now += .1
                samples["energy"] = EnergySample(20, clock())
            elif change == "emergency":
                action = replace(action, kind="EMERGENCY", contingency_id=None)
        return action

    def bounded_land(**kwargs):
        landings.append(kwargs)
        return land(**kwargs)

    recovery.next_action, executor.land = decide, bounded_land
    try:
        supervisor.start(executor)
        first = supervisor.prepare(snapshot_version=store.session_version("session"), provider_wait_s=100)
        assert first.transition == "RECOVER" and first.request.pose.state == LocalizationState.ANCHORED
        assert supervisor.dispatch(first, executor).status == "RECOVERY_PENDING"
        again = supervisor.prepare(snapshot_version=store.session_version("session"), provider_wait_s=100)
        result = supervisor.dispatch(again, executor)
        assert len(decisions) == 1 and decisions[0].reason == "recovery_exhausted"
        assert decisions[0].contingency_id == "NAVIGATION_UNAVAILABLE"
        terminal_audit = [e for e in store.events("session") if e.event_type == "SAFETY_ASSESSED"][-1]
        assert terminal_audit.payload["reason"] == "recovery_exhausted"
        if change in {"valid", "fresh_landing_decision"}:
            assert result.state == "LANDED"
            assert not emergencies and executor.client.commands.count("land") == 1
            assert landings == [{"upper_bound_s": supervisor.landing_upper_bound_s, "deadline": executor.deadline}]
            assert terminal_audit.payload["contingency_id"] == "NAVIGATION_UNAVAILABLE"
            assert terminal_audit.payload["pose_forecast"]["projected"]["propagated_at"] == clock() + 10
            assert dict(terminal_audit.payload["energy"]["components"])["contingency_landing_s"] == 10
            if change == "fresh_landing_decision":
                admission = preparations[-1]
                assert admission.transition == "LAND_CONTINGENCY"
                assert admission.reason == "RETURN_ENERGY_UNAVAILABLE"
                components = dict(admission.energy.components)
                assert components["contingency_landing_cost_pct"] == 21
                assert components["contingency_available_pct"] - components["contingency_landing_cost_pct"] == 20
                audited = terminal_audit.payload["energy"]
                assert {k: v for k, v in audited.items() if k != "components"} == {
                    k: v for k, v in asdict(admission.energy).items() if k != "components"}
                assert dict(audited["components"]) == components
                assert len(audited["components"]) == len(components)
        else:
            assert result.state == "EMERGENCY_ABORTED" and emergencies == [True]
            assert "land" not in executor.client.commands
            assert terminal_audit.payload["contingency_id"] is None
        assert not commands
    finally:
        executor.close()


def test_assessment_is_pure_and_prices_site_route_landing_and_wait(safety):
    supervisor, store, _, samples, refreshes, _ = safety
    req = request(safety, checkpoint(samples["pose"], command()), wait=1)
    before = store.session_version("session"), asdict(supervisor.tracker.health)
    first = supervisor.assess(req, supervisor.site)
    assert first == supervisor.assess(req, supervisor.site)
    assert first.transition == "CONTINUE" and not first.invalidates_snapshot
    assert first.energy_sample == samples["energy"]
    assert dict(first.energy.components)["provider_wait_s"] == 1
    assert dict(first.energy.components)["termination_s"] > 0
    assert first.calibration_hashes
    assert before == (store.session_version("session"), asdict(supervisor.tracker.health))
    assert refreshes == [] and not supervisor.reserve.return_latched


def test_wait_forecast_requires_recovery_before_calibrated_limit(safety):
    supervisor, _, _, samples, *_ = safety
    supervisor.tracker.profile = replace(supervisor.tracker.profile, max_camera_age_s=2)
    # Rebind fixture grant for the deliberately changed calibrated artifact.
    supervisor.validate_bindings = lambda site: None
    outcome = supervisor.assess(request(safety, wait=3), supervisor.site)
    assert outcome.transition == "RECOVER" and outcome.invalidates_snapshot
    assert "camera_age" in outcome.pose_forecast.reasons
    assert samples["pose"].state == LocalizationState.ANCHORED


def test_lost_never_rotates_or_translates_and_uses_existing_emergency(safety):
    supervisor, store, clock, samples, _, emergencies = safety
    samples["pose"] = replace(samples["pose"], state=LocalizationState.LOST)
    executor = Executor(store, clock)
    outcome = supervisor.assess(request(safety), supervisor.site)
    assert outcome.transition == "LAND_CONTINGENCY"
    assert outcome.contingency_id is None
    result = supervisor.dispatch(outcome, executor)
    assert result.state == "EMERGENCY_ABORTED"
    assert emergencies == [True] and executor.calls == []


def test_unaffordable_work_latches_return_before_commands(safety):
    supervisor, store, clock, samples, *_ = safety
    samples["energy"] = EnergySample(22, clock())
    optional = checkpoint(samples["pose"], command(seconds=100))
    outcome = supervisor.assess(request(safety, optional), supervisor.site)
    assert outcome.transition == "RETURN_NOW" and outcome.invalidates_snapshot
    executor = Executor(store, clock)
    supervisor.dispatch(outcome, executor)
    assert supervisor.reserve.return_latched
    assert "CAPTURE" not in executor.calls
    samples["energy"] = EnergySample(100, clock() + 1)
    clock.now += 1
    assert supervisor.assess(request(safety), supervisor.site).transition != "CONTINUE"


@pytest.mark.parametrize("bad", ["missing_site", "assurance_expired", "stale_battery", "binding"])
def test_invalid_prerequisites_fail_closed(safety, bad):
    supervisor, _, clock, samples, *_ = safety
    active = supervisor.site
    if bad == "missing_site":
        active = replace(active, terminal_contingencies=())
    elif bad == "assurance_expired":
        clock.now = 16
        samples["energy"] = EnergySample(80, clock())
    elif bad == "stale_battery":
        samples["energy"] = EnergySample(80, 1)
    else:
        supervisor.aircraft_id = "other-aircraft"
    outcome = supervisor.assess(request(safety), active)
    assert outcome.transition == "LAND_CONTINGENCY"
    assert outcome.invalidates_snapshot
    with pytest.raises(ValueError):
        supervisor.validate_start(active)


def test_swept_path_rejects_hazard_between_safe_endpoints(safety):
    supervisor, _, _, samples, *_ = safety
    blocked = Volume3D("obstacle", ((125, 0), (135, 0), (135, 200), (125, 200)), (0, 200))
    landing = ((75, 75), (124, 75), (124, 125), (75, 125))
    supervisor.site = replace(supervisor.site, forbidden_volumes=(blocked,), terminal_contingencies=tuple(
        replace(entry, landing_polygon_xy_cm=landing) for entry in supervisor.site.terminal_contingencies))
    supervisor.validate_bindings = lambda site: None
    plan = checkpoint(samples["pose"], command("MOVE", direction="right", distance_cm=60))
    result = supervisor.assess(request(safety, plan), supervisor.site)
    assert result.transition != "CONTINUE"
    assert "path" in result.reason.lower()


def test_each_command_refreshes_and_completion_is_append_only_measured_evidence(safety):
    supervisor, store, clock, samples, refreshes, _ = safety
    plan = checkpoint(samples["pose"], command(), command())
    result = supervisor.dispatch(supervisor.assess(request(safety, plan), supervisor.site), Executor(store, clock))
    assert result.status == "COMPLETED"
    # Consecutive stationary steps (here two captures) go out as one batch:
    # one admission, one completion record and one measured state.
    assert len(refreshes) >= 2
    events = store.events("session")
    assert len([e for e in events if e.event_type == "CHECKPOINT_COMPLETED"]) == 1
    states = [e for e in events if e.event_type == "DRONE_STATE_RECORDED"]
    assert len(states) == 1
    assert states[-1].payload["state"]["battery_percent"] == 80
    queries = ReadQueries(store, clock=clock, clock_domain_id=supervisor.clock_domain_id)
    snapshot = queries.project("session")
    state = dict(next(f for f in snapshot.sources if f.kind == "READ_DRONE").values)
    assert state["x_cm"] == 100 and state["pose_source"] == "mapped_joint_tags"
    assert state["localization_state"] == "ANCHORED"
    assert state["state_freshness"] == "FRESH"
    clock.now += 4
    stale = queries.project("session")
    assert dict(next(f for f in stale.sources if f.kind == "READ_DRONE").values)["state_freshness"] == "STALE"


def test_rejected_completion_battery_is_stale_and_retains_validated_provenance(safety):
    supervisor, store, clock, samples, *_ = safety

    class ConflictingBattery(Executor):
        def execute(self, plan):
            result = super().execute(plan)
            samples["energy"] = EnergySample(99, clock())  # Same timestamp, different payload.
            return result

    executor = ConflictingBattery(store, clock)
    plan = checkpoint(samples["pose"], command())
    result = supervisor.dispatch(supervisor.assess(request(safety, plan), supervisor.site), executor)
    assert result.status == "COMPLETED"
    recorded = store.events("session")[-1].payload
    assert recorded["refresh_succeeded"] is False
    assert recorded["state"]["battery_percent"] == 80
    assert recorded["state"]["telemetry_error_category"] == "ENERGY_SAMPLE_REJECTED"
    assert recorded["energy_measured_at"] == 10
    snapshot = ReadQueries(store, clock=clock, clock_domain_id=supervisor.clock_domain_id).project("session")
    state = dict(next(f for f in snapshot.sources if f.kind == "READ_DRONE").values)
    assert state["state_freshness"] == "STALE" and state["battery_percent"] == 80
    assert supervisor.prepare(snapshot_version=store.session_version("session")).transition == "LAND_CONTINGENCY"


def test_stale_optional_snapshot_cannot_dispatch(safety):
    supervisor, store, clock, samples, *_ = safety
    outcome = supervisor.assess(request(safety, checkpoint(samples["pose"], command())), supervisor.site)
    store.append_event(session_id="session", expected_version=store.session_version("session"),
        event_type="OTHER", payload={}, correlation_id="other", causation_id=None, idempotency_key="other")
    executor = Executor(store, clock)
    with pytest.raises(VersionConflict):
        supervisor.dispatch(outcome, executor)
    assert executor.calls == []


def test_clock_regression_cannot_revive_assurance(safety):
    supervisor, _, clock, _, *_ = safety
    supervisor.prepare(snapshot_version=request(safety).snapshot_version)
    clock.now = 9
    assert supervisor.prepare(snapshot_version=request(safety).snapshot_version).transition == "LAND_CONTINGENCY"
    clock.now = 10
    assert supervisor.prepare(snapshot_version=request(safety).snapshot_version).transition == "LAND_CONTINGENCY"


def test_room_controller_mandatory_transition_preempts_judge_and_reprojects(safety):
    from sentinel.room.controller import RoomController
    from sentinel.room.models import TerminalState
    supervisor, store, clock, samples, *_ = safety
    samples["pose"] = replace(samples["pose"], state=LocalizationState.LOST)

    class Judge:
        def choice(self, *args, **kwargs):
            pytest.fail("mandatory safety transition must not call Jev")

    controller = RoomController(store, Judge(), supervisor=supervisor, flight_executor=Executor(store, clock))
    result = controller.step("session")
    assert result.candidate_id == "LAND_CONTINGENCY"
    assert result.terminal_state == TerminalState.ABORTED
    assert controller.queries.project("session").terminal_state == TerminalState.ABORTED


def test_provider_wait_preempts_noncooperative_work_without_consuming_late_result(safety):
    supervisor, store, clock, samples, _, emergencies = safety
    entered, release = Event(), Event()

    def provider(cancel):
        samples["pose"] = replace(samples["pose"], state=LocalizationState.LOST)
        entered.set()
        release.wait(2)
        return "stale candidate"

    try:
        result = supervisor.run_wait(provider, snapshot_version=store.session_version("session"),
                                     timeout_s=1, executor=Executor(store, clock))
        assert entered.is_set()
        assert result.status == "PREEMPTED" and result.value is None
        assert emergencies == [True]
    finally:
        release.set()


def test_perception_wait_uses_the_same_safety_boundary(safety):
    from sentinel.room.controller import RoomController
    supervisor, store, clock, samples, *_ = safety
    samples["pose"] = replace(samples["pose"], state=LocalizationState.LOST)
    calls = []
    controller = RoomController(store, object(), supervisor=supervisor, flight_executor=Executor(store, clock))
    result = controller.wait_for_perception(lambda cancel: calls.append(True), session_id="session")
    assert result.status == "PREEMPTED" and calls == []


def test_measurement_failure_is_stale_and_sdk_uncertainty_never_releases_next_command(safety):
    supervisor, store, clock, samples, *_ = safety

    class UnknownExecutor(Executor):
        def execute(self, plan):
            super().execute(plan)
            self.state = ExecutorState.TERMINAL
            supervisor.refresh = lambda: (_ for _ in ()).throw(RuntimeError("sensor failed"))
            return CheckpointOutcome("COMMAND_COMPLETION_UNKNOWN", (), (), None, None, False)

    executor = UnknownExecutor(store, clock)
    plan = checkpoint(samples["pose"], command("MOVE", direction="right", distance_cm=20), command())
    result = supervisor.dispatch(supervisor.assess(request(safety, plan), supervisor.site), executor)
    assert result.status == "COMMAND_COMPLETION_UNKNOWN" and not result.completion_known
    assert executor.calls == ["MOVE"]
    facts = ReadQueries(store, clock=clock, clock_domain_id=supervisor.clock_domain_id).project("session").sources
    state = dict(next(f for f in facts if f.kind == "READ_DRONE").values)
    assert state["state_freshness"] == "STALE" and not state["completion_known"]


def test_return_cannot_translate_when_refresh_loses_localization(safety):
    supervisor, store, clock, samples, _, emergencies = safety
    samples["energy"] = EnergySample(22, clock())
    plan = checkpoint(samples["pose"], command(seconds=100))
    outcome = supervisor.assess(request(safety, plan), supervisor.site)
    samples["pose"] = replace(samples["pose"], state=LocalizationState.LOST)
    executor = Executor(store, clock)
    supervisor.dispatch(outcome, executor)
    assert executor.calls == [] and emergencies == [True]


def test_landing_node_requires_current_declared_clearance(safety):
    supervisor, store, clock, samples, _, emergencies = safety
    # This hover lies inside surveyed free space but outside the contingency's
    # uncertainty-inflated descent footprint. A bare LAND must not bypass it.
    narrow = ((0, 0), (50, 0), (50, 50), (0, 50))
    supervisor.site = replace(supervisor.site, terminal_contingencies=tuple(
        replace(entry, landing_polygon_xy_cm=narrow) for entry in supervisor.site.terminal_contingencies))
    supervisor.validate_bindings = lambda site: None
    plan = checkpoint(samples["pose"], command("LAND"))
    executor = Executor(store, clock)
    supervisor.dispatch(supervisor.assess(request(safety, plan), supervisor.site), executor)
    assert executor.calls == [] and emergencies == [True]


@pytest.mark.parametrize("outside", ["both", "energy"])
def test_home_landing_outside_assured_geometry_blocks_start_and_optional_work(safety, outside):
    supervisor, _, _, samples, *_ = safety
    narrow = ((0, 0), (50, 0), (50, 50), (0, 50))
    supervisor.site = replace(supervisor.site, terminal_contingencies=tuple(
        replace(entry, landing_polygon_xy_cm=narrow)
        if outside == "both" or entry.trigger == ContingencyTrigger.RETURN_ENERGY_UNAVAILABLE else entry
        for entry in supervisor.site.terminal_contingencies))
    supervisor.validate_bindings = lambda site: None
    # Home is (100, 100): unrelated navigation contingency geometry cannot
    # authorize the energy-reserved home descent.
    with pytest.raises(ValueError, match="start blocked"):
        supervisor.validate_start()
    outcome = supervisor.assess(request(safety, checkpoint(samples["pose"], command())), supervisor.site)
    assert outcome.transition != "CONTINUE"


def test_optional_work_preserves_home_descent_with_accumulated_uncertainty(safety):
    supervisor, _, _, samples, *_ = safety
    narrow = ((0, 0), (125, 0), (125, 200), (0, 200))
    supervisor.site = replace(supervisor.site, terminal_contingencies=tuple(
        replace(entry, landing_polygon_xy_cm=narrow) for entry in supervisor.site.terminal_contingencies))
    supervisor.validate_bindings = lambda site: None
    assert supervisor.assess(request(safety), supervisor.site).transition == "CONTINUE"
    plan = checkpoint(samples["pose"], command("SETTLE", seconds=100))
    outcome = supervisor.assess(request(safety, plan), supervisor.site)
    assert outcome.transition == "RETURN_NOW"
    assert outcome.reason == "PROJECTED_TERMINATION_LANDING_INVALID"


def test_home_landing_requires_declared_touchdown_height(safety):
    supervisor, *_ = safety
    supervisor.site = replace(supervisor.site, terminal_contingencies=tuple(
        replace(entry, z_band_cm=(50, 200)) for entry in supervisor.site.terminal_contingencies))
    supervisor.validate_bindings = lambda site: None
    with pytest.raises(ValueError, match="start blocked"):
        supervisor.validate_start()
    assert supervisor.assess(request(safety), supervisor.site).transition != "CONTINUE"


def test_unrelated_current_assurance_cannot_replace_expired_home_landing_assurance(safety):
    supervisor, store, clock, samples, *_ = safety
    navigation, home = supervisor.site.terminal_contingencies
    remote = replace(home, contingency_id="remote-energy", max_assurance_age_ms=100000,
        landing_polygon_xy_cm=((0, 0), (50, 0), (50, 50), (0, 50)))
    supervisor.site = replace(supervisor.site, terminal_contingencies=(
        replace(navigation, max_assurance_age_ms=100000), home, remote))
    supervisor.validate_bindings = lambda site: None
    store.append_contingency_assurance(assurance(assurance_id="remote", contingency_id="remote-energy",
        clock_domain_id=supervisor.clock_domain_id), expected_version=store.session_version("session"))
    clock.now = 16
    samples["energy"] = EnergySample(80, clock())
    with pytest.raises(ValueError, match="start blocked"):
        supervisor.validate_start()
    assert supervisor.assess(request(safety), supervisor.site).transition != "CONTINUE"


def test_home_landing_uses_descent_geometry_even_when_emergency_policy_is_first(safety):
    supervisor, store, clock, samples, *_ = safety
    emergency = replace(supervisor.site.terminal_contingencies[1], contingency_id="energy-emergency",
                        kind=ContingencyKind.EMERGENCY)
    supervisor.site = replace(supervisor.site,
        terminal_contingencies=(emergency,) + supervisor.site.terminal_contingencies)
    supervisor.validate_bindings = lambda site: None
    store.append_contingency_assurance(assurance(assurance_id="emergency", contingency_id="energy-emergency",
        clock_domain_id=supervisor.clock_domain_id), expected_version=store.session_version("session"))
    plan = checkpoint(samples["pose"], command("LAND"))
    outcome = supervisor.assess(request(safety, plan), supervisor.site)
    assert outcome.transition == "CONTINUE"
    assert supervisor.dispatch(outcome, Executor(store, clock)).state == "LANDED"


def test_final_home_landing_uses_energy_contingency_not_navigation_policy(safety):
    supervisor, store, clock, samples, _, emergencies = safety
    executor = Executor(store, clock)
    plan = checkpoint(samples["pose"], command("LAND"))
    outcome = supervisor.assess(request(safety, plan), supervisor.site)
    assert outcome.transition == "CONTINUE"
    result = supervisor.dispatch(outcome, executor)
    assert result.state == "LANDED" and emergencies == []
    landing = [e for e in store.events("session") if e.event_type == "SAFETY_ASSESSED"][-1]
    assert landing.payload["contingency_id"] == ContingencyTrigger.RETURN_ENERGY_UNAVAILABLE.value


@pytest.mark.parametrize("trigger", list(ContingencyTrigger))
def test_termination_failure_selects_only_the_matching_contingency(safety, trigger):
    supervisor, _, clock, samples, *_ = safety
    if trigger == ContingencyTrigger.RETURN_ENERGY_UNAVAILABLE:
        samples["energy"] = EnergySample(22, clock())
        # The route cannot be afforded but a local descent still can.
        supervisor.reserve = ReserveGuard(replace(supervisor.reserve.profile,
            percent_per_second_upper=.1), clock)
    else:
        samples["pose"] = replace(samples["pose"], position=Position(160, 100, 100))
        supervisor.site = replace(supervisor.site, corridors=(Volume3D("corridor",
            ((0, 0), (135, 0), (135, 200), (0, 200)), (0, 200)),))
    supervisor.validate_bindings = lambda site: None
    outcome = supervisor.assess(request(safety), supervisor.site)
    assert outcome.transition == "LAND_CONTINGENCY"
    assert outcome.contingency_id == trigger.value


@pytest.mark.parametrize("trigger", list(ContingencyTrigger))
def test_contingency_requires_clearance_for_full_calibrated_descent(safety, trigger):
    supervisor, store, clock, samples, _, emergencies = safety
    # The measured radius is 15 + 1 = 16 cm. Ten seconds of calibrated
    # descent grows it to 17 cm, outside the 116.5 cm polygon boundary.
    narrow = ((0, 0), (116.5, 0), (116.5, 200), (0, 200))
    supervisor.site = replace(supervisor.site, terminal_contingencies=tuple(
        replace(entry, landing_polygon_xy_cm=narrow) for entry in supervisor.site.terminal_contingencies))
    supervisor.reserve = ReserveGuard(replace(supervisor.reserve.profile,
        percent_per_second_upper=.1), clock)
    supervisor.validate_bindings = lambda site: None
    samples["energy"] = EnergySample(22 if trigger == ContingencyTrigger.RETURN_ENERGY_UNAVAILABLE else 80, clock())
    outcome = supervisor.assess(request(safety), supervisor.site)
    assert outcome.transition == "LAND_CONTINGENCY"
    assert outcome.reason == ("RETURN_ENERGY_UNAVAILABLE"
        if trigger == ContingencyTrigger.RETURN_ENERGY_UNAVAILABLE else "TERMINATION_LANDING_INVALID")
    assert outcome.contingency_id is None
    assert outcome.pose_forecast.projected.propagated_at == clock() + supervisor.landing_upper_bound_s
    assert outcome.pose_forecast.projected.xy_uncertainty_cm == pytest.approx(2)
    assert dict(outcome.energy.components)["contingency_landing_s"] == 10
    assert dict(outcome.energy.components)["contingency_landing_cost_pct"] == 2
    executor = Executor(store, clock)
    assert supervisor.dispatch(outcome, executor).state == "EMERGENCY_ABORTED"
    assert executor.calls == [] and emergencies == [True]


def test_dispatch_rechecks_contingency_descent_after_measured_uncertainty_increases(safety):
    supervisor, store, clock, samples, _, emergencies = safety
    narrow = ((0, 0), (118.5, 0), (118.5, 200), (0, 200))
    supervisor.site = replace(supervisor.site, terminal_contingencies=tuple(
        replace(entry, landing_polygon_xy_cm=narrow) for entry in supervisor.site.terminal_contingencies))
    supervisor.reserve = ReserveGuard(replace(supervisor.reserve.profile,
        percent_per_second_upper=.1), clock)
    supervisor.validate_bindings = lambda site: None
    samples["energy"] = EnergySample(22, clock())
    outcome = supervisor.assess(request(safety), supervisor.site)
    assert outcome.contingency_id == ContingencyTrigger.RETURN_ENERGY_UNAVAILABLE.value
    # Current radius 18 still fits, but the refreshed descent radius 19 does not.
    samples["pose"] = replace(samples["pose"], xy_uncertainty_cm=3)
    executor = Executor(store, clock)
    assert supervisor.dispatch(outcome, executor).state == "EMERGENCY_ABORTED"
    assert executor.calls == [] and emergencies == [True]
    preemption = [e for e in store.events("session") if e.event_type == "SAFETY_ASSESSED"][-1]
    assert preemption.payload["contingency_id"] is None


def test_contingency_cannot_outlast_calibrated_localization_health(safety):
    supervisor, store, clock, samples, _, emergencies = safety
    supervisor.tracker.profile = replace(supervisor.tracker.profile, max_camera_age_s=5)
    supervisor.reserve = ReserveGuard(replace(supervisor.reserve.profile,
        percent_per_second_upper=.1), clock)
    supervisor.validate_bindings = lambda site: None
    samples["energy"] = EnergySample(22, clock())
    outcome = supervisor.assess(request(safety), supervisor.site)
    assert outcome.transition == "LAND_CONTINGENCY" and outcome.contingency_id is None
    assert "camera_age" in outcome.pose_forecast.reasons
    executor = Executor(store, clock)
    assert supervisor.dispatch(outcome, executor).state == "EMERGENCY_ABORTED"
    assert executor.calls == [] and emergencies == [True]


@pytest.mark.parametrize("invalid", ["telemetry", "capability"])
def test_failed_prerequisite_cannot_authorize_normal_contingency_descent(safety, invalid):
    supervisor, store, clock, samples, _, emergencies = safety
    if invalid == "telemetry":
        samples["energy"] = EnergySample(80, 1)
    else:
        supervisor.aircraft_id = "unbound-aircraft"
    outcome = supervisor.assess(request(safety), supervisor.site)
    assert outcome.transition == "LAND_CONTINGENCY" and outcome.contingency_id is None
    executor = Executor(store, clock)
    assert supervisor.dispatch(outcome, executor).state == "EMERGENCY_ABORTED"
    assert executor.calls == [] and emergencies == [True]


@pytest.mark.parametrize("history", ["learned_burn", "prior_minimum"])
def test_contingency_prices_descent_with_validated_telemetry_history(safety, history):
    supervisor, _, clock, samples, *_ = safety
    if history == "learned_burn":
        supervisor.reserve.ingest_sample(EnergySample(80, clock() - 1))
        samples["energy"] = EnergySample(22, clock())
    else:
        supervisor.reserve.ingest_sample(EnergySample(21, clock() - 1))
        samples["pose"] = replace(samples["pose"], position=Position(160, 100, 100))
        supervisor.site = replace(supervisor.site, corridors=(Volume3D("corridor",
            ((0, 0), (135, 0), (135, 200), (0, 200)), (0, 200)),))
        supervisor.validate_bindings = lambda site: None
    outcome = supervisor.assess(request(safety), supervisor.site)
    assert outcome.transition == "LAND_CONTINGENCY" and outcome.contingency_id is None
    descent = dict(outcome.energy.components)
    if history == "learned_burn":
        assert descent["contingency_landing_cost_pct"] == 581
    else:
        assert descent["contingency_available_pct"] == 21


def test_return_forecast_cannot_cross_localization_bound(safety):
    supervisor, _, _, samples, *_ = safety
    supervisor.tracker.profile = replace(supervisor.tracker.profile, max_camera_age_s=2)
    supervisor.validate_bindings = lambda site: None
    # Even an immediate capture requires navigation sufficient for the exact
    # priced home route, not merely enough localization for the optional node.
    outcome = supervisor.assess(request(safety, checkpoint(samples["pose"], command(seconds=.1))), supervisor.site)
    assert outcome.transition != "CONTINUE"


def test_low_reserve_during_recovery_never_attempts_translation(safety):
    supervisor, store, clock, samples, *_ = safety
    supervisor.tracker.profile = replace(supervisor.tracker.profile, max_camera_age_s=2)
    supervisor.validate_bindings = lambda site: None
    samples["energy"] = EnergySample(22, clock())
    plan = checkpoint(samples["pose"], command(seconds=100))
    outcome = supervisor.assess(request(safety, plan), supervisor.site)
    assert outcome.transition in {"RECOVER", "LAND_CONTINGENCY"}


def test_terminal_duration_is_part_of_the_exact_calibration_binding(safety):
    supervisor, *_ = safety
    supervisor.landing_upper_bound_s = .0001
    assert supervisor.assess(request(safety), supervisor.site).transition != "CONTINUE"


def test_stale_pose_command_start_is_rejected_before_optional_movement(safety):
    supervisor, store, clock, samples, *_ = safety
    plan = replace(checkpoint(samples["pose"], command("MOVE", direction="right", distance_cm=20)),
                   start=Position(20, 20, 100))
    result = supervisor.assess(request(safety, plan), supervisor.site)
    assert result.transition != "CONTINUE"


def test_next_checkpoint_node_uses_refreshed_measured_pose(safety):
    supervisor, store, clock, samples, *_ = safety

    class MovingExecutor(Executor):
        def execute(self, plan):
            result = super().execute(plan)
            if plan.commands[0].kind == "MOVE":
                samples["pose"] = replace(samples["pose"], position=Position(120, 100, 100))
            return replace(result, pose=samples["pose"].position)

    executor = MovingExecutor(store, clock)
    plan = checkpoint(samples["pose"], command("MOVE", direction="right", distance_cm=20), command())
    result = supervisor.dispatch(supervisor.assess(request(safety, plan), supervisor.site), executor)
    assert result.status == "COMPLETED" and executor.calls == ["MOVE", "CAPTURE"]


def test_wait_deadline_forces_return_without_waiting_for_provider(safety):
    supervisor, store, clock, _, *_ = safety
    release = Event()

    def slow(cancel):
        clock.now += 1
        release.wait(2)
        return "too late"

    try:
        waited = supervisor.run_wait(slow, snapshot_version=store.session_version("session"),
                                     timeout_s=.1, executor=Executor(store, clock))
        assert waited.status == "PREEMPTED" and waited.value is None
        assert supervisor.reserve.return_latched
    finally:
        release.set()


def test_missing_refresh_after_admission_dispatches_only_emergency(safety):
    supervisor, store, clock, samples, _, emergencies = safety
    plan = checkpoint(samples["pose"], command())
    outcome = supervisor.assess(request(safety, plan), supervisor.site)
    supervisor.refresh = lambda: (_ for _ in ()).throw(RuntimeError("telemetry disconnected"))
    executor = Executor(store, clock)
    result = supervisor.dispatch(outcome, executor)
    assert result.state == "EMERGENCY_ABORTED" and executor.calls == [] and emergencies == [True]


def test_unknown_sdk_completion_does_not_claim_aircraft_is_on_ground(safety):
    supervisor, store, clock, samples, *_ = safety

    class UnknownExecutor(Executor):
        def execute(self, plan):
            self.state = ExecutorState.TERMINAL
            return CheckpointOutcome("COMMAND_COMPLETION_UNKNOWN", (), (), None, None, False)

    plan = checkpoint(samples["pose"], command())
    supervisor.dispatch(supervisor.assess(request(safety, plan), supervisor.site), UnknownExecutor(store, clock))
    state = store.events("session")[-1].payload["state"]
    assert state["airborne"] is None


def test_validated_recovery_commits_reanchor_and_all_invalidations_atomically(safety):
    from sentinel.cube.recovery import PoseReport, RecoveryController
    from tests.cube.test_localization import fix
    supervisor, store, clock, samples, *_ = safety
    supervisor.tracker._health = replace(supervisor.tracker.health,
        state=LocalizationState.RECOVERY_REQUIRED, estimator_failures=3)
    samples["pose"] = supervisor.tracker.health
    supervisor.refresh = lambda: (supervisor.tracker.health, samples["energy"])
    burst = tuple(PoseReport(str(i), fix(clock)) for i in range(3))
    supervisor.recovery = RecoveryController(supervisor.tracker, clock=clock,
        burst_source=lambda action: burst, energy_admits=lambda action: True)
    supervisor.mapped_heading_tag_ids = ((90, (1, 2)),)
    executor = Executor(store, clock)
    outcome = supervisor.assess(request(safety), supervisor.site)
    assert outcome.transition == "RECOVER"
    result = supervisor.dispatch(outcome, executor)
    assert result.status == "COMPLETED"
    types = [e.event_type for e in store.events("session")]
    start = types.index("POSE_REANCHORED")
    assert types[start:start + 4] == ["POSE_REANCHORED", "ROUTES_INVALIDATED",
                                    "CANDIDATES_INVALIDATED", "TARGET_LOCKS_INVALIDATED"]
    assert supervisor.tracker.health.state == LocalizationState.ANCHORED
    assert executor.calls == []  # stationary burst performs no SDK motion


def test_recovery_source_timeout_never_publishes_late_reanchor(safety):
    from sentinel.cube.recovery import PoseReport, RecoveryController
    from tests.cube.test_localization import fix
    supervisor, store, clock, samples, _, emergencies = safety
    release = Event()
    supervisor.tracker._health = replace(supervisor.tracker.health,
        state=LocalizationState.RECOVERY_REQUIRED, estimator_failures=3)
    samples["pose"] = supervisor.tracker.health

    def blocked(action):
        clock.now += 2
        release.wait(2)
        return tuple(PoseReport(str(i), fix(clock)) for i in range(3))

    supervisor.recovery = RecoveryController(supervisor.tracker, clock=clock,
        burst_source=blocked, energy_admits=lambda action: True)
    supervisor.mapped_heading_tag_ids = ((90, (1, 2)),)
    try:
        result = supervisor.dispatch(supervisor.assess(request(safety), supervisor.site), Executor(store, clock))
        assert result.state == "EMERGENCY_ABORTED"
        assert emergencies == [True]
        assert not any(e.event_type == "POSE_REANCHORED" for e in store.events("session"))
    finally:
        release.set()


def test_return_preemption_is_latched_even_if_refresh_now_requires_recovery(safety):
    supervisor, store, clock, samples, *_ = safety
    samples["energy"] = EnergySample(22, clock())
    outcome = supervisor.assess(request(safety, checkpoint(samples["pose"], command(seconds=100))), supervisor.site)
    assert outcome.transition == "RETURN_NOW"
    samples["pose"] = replace(samples["pose"], state=LocalizationState.RECOVERY_REQUIRED, estimator_failures=3)
    executor = Executor(store, clock)
    supervisor.dispatch(outcome, executor)
    assert supervisor.reserve.return_latched
    assert "CAPTURE" not in executor.calls


def test_missing_recovery_adapter_never_runs_an_independent_recovery_policy(safety):
    supervisor, store, clock, samples, _, emergencies = safety
    samples["pose"] = replace(samples["pose"], state=LocalizationState.RECOVERY_REQUIRED)
    executor = Executor(store, clock)
    result = supervisor.dispatch(supervisor.assess(request(safety), supervisor.site), executor)
    assert result.state == "EMERGENCY_ABORTED"
    assert executor.calls == [] and emergencies == [True]


def test_existing_recovery_policy_stationary_then_ranked_yaw_then_canonical_reanchor(safety):
    from sentinel.cube.recovery import PoseReport, RecoveryController
    from tests.cube.test_localization import fix
    supervisor, store, clock, samples, *_ = safety
    supervisor.site = replace(supervisor.site, terminal_contingencies=tuple(
        replace(e, max_assurance_age_ms=100000) for e in supervisor.site.terminal_contingencies))
    supervisor.tracker.profile = replace(supervisor.tracker.profile, recovery_deadline_s=80,
        recovery_action_upper_bound_s=10, max_look_away_s=2000, max_yaw_deg=2000)
    supervisor.validate_bindings = lambda site: None  # isolate runtime recovery policy from the grant fixture
    supervisor.tracker._health = replace(supervisor.tracker.health,
        state=LocalizationState.RECOVERY_REQUIRED, estimator_failures=3)
    supervisor.refresh = lambda: (supervisor.tracker.health, samples["energy"])
    samples["pose"] = supervisor.tracker.health
    actions = []

    def burst(action):
        actions.append((action.kind, action.heading_deg))
        if action.kind == "STATIONARY_BURST":
            return ()
        return tuple(PoseReport(str(i), fix(clock, heading_deg=action.heading_deg)) for i in range(3))

    supervisor.recovery = RecoveryController(supervisor.tracker, clock=clock,
        burst_source=burst, energy_admits=lambda action: True)
    supervisor.mapped_heading_tag_ids = ((90, (1, 2)), (60, (1, 2, 3)))
    executor = Executor(store, clock)
    for _ in range(3):
        samples["pose"] = supervisor.tracker.health
        result = supervisor.dispatch(supervisor.assess(request(safety), supervisor.site), executor)
    assert actions == [("STATIONARY_BURST", 90), ("YAW_BURST", 60), ("RESTORE_CANONICAL", 90)]
    assert result.status == "COMPLETED"
    assert executor.calls == ["ROTATE", "ROTATE"]
    assert supervisor.tracker.health.state == LocalizationState.ANCHORED


def test_initial_telemetry_failure_is_audited_and_preempts_judge(safety):
    from sentinel.room.controller import RoomController
    from sentinel.room.models import TerminalState
    supervisor, store, clock, _, _, emergencies = safety
    supervisor.refresh = lambda: (_ for _ in ()).throw(RuntimeError("disconnected"))
    controller = RoomController(store, object(), supervisor=supervisor, flight_executor=Executor(store, clock))
    result = controller.step("session")
    assert result.terminal_state == TerminalState.ABORTED
    assert emergencies == [True]
    state = store.events("session")[-1].payload["state"]
    assert state["battery_percent"] is None and state["pose_source"] == "UNAVAILABLE"


def test_missing_drift_calibration_produces_fail_closed_assessment(safety):
    supervisor, *_ = safety
    supervisor.tracker.profile = None
    assert supervisor.assess(request(safety), supervisor.site).transition == "LAND_CONTINGENCY"


def test_invalid_clock_preemption_is_persistable_and_cannot_issue_optional_work(safety):
    supervisor, store, clock, samples, _, emergencies = safety
    plan = checkpoint(samples["pose"], command())
    supervisor.clock = lambda: float("nan")
    result = supervisor.dispatch(supervisor.assess(request(safety, plan), supervisor.site), Executor(store, clock))
    assert result.state == "EMERGENCY_ABORTED" and emergencies == [True]


def test_failed_refresh_after_provider_completion_discards_provider_result(safety):
    supervisor, store, clock, _, _, emergencies = safety

    def provider(cancel):
        supervisor.refresh = lambda: (_ for _ in ()).throw(RuntimeError("disconnected"))
        return "not safe to use"

    result = supervisor.run_wait(provider, snapshot_version=store.session_version("session"),
                                 timeout_s=1, executor=Executor(store, clock))
    assert result.status == "PREEMPTED" and result.value is None and emergencies == [True]


@pytest.mark.parametrize("return_to_start", [False, True])
def test_optional_movement_cannot_leave_the_declared_return_corridor(safety, return_to_start):
    supervisor, store, clock, samples, *_ = safety
    corridor = Volume3D("corridor", ((0, 0), (135, 0), (135, 200), (0, 200)), (0, 200))
    supervisor.site = replace(supervisor.site, corridors=(corridor,))
    supervisor.validate_bindings = lambda site: None  # isolate geometry from the altered fixture artifact
    commands = (command("MOVE", direction="right", distance_cm=60),)
    if return_to_start:
        commands += (command("MOVE", direction="left", distance_cm=60),)
    plan = checkpoint(samples["pose"], *commands)
    outcome = supervisor.assess(request(safety, plan), supervisor.site)
    assert outcome.transition == "RETURN_NOW"
    assert outcome.reason == "PROJECTED_TERMINATION_PATH_INVALID"
    executor = Executor(store, clock)
    supervisor.dispatch(outcome, executor)
    assert "MOVE" not in executor.calls


def test_projected_return_path_includes_uncertainty_accumulated_during_optional_work(safety):
    supervisor, _, _, samples, *_ = safety
    corridor = Volume3D("corridor", ((0, 0), (125, 0), (125, 200), (0, 200)), (0, 200))
    supervisor.site = replace(supervisor.site, corridors=(corridor,))
    supervisor.validate_bindings = lambda site: None
    assert supervisor.assess(request(safety), supervisor.site).transition == "CONTINUE"
    plan = checkpoint(samples["pose"], command("SETTLE", seconds=100))
    outcome = supervisor.assess(request(safety, plan), supervisor.site)
    assert outcome.transition == "RETURN_NOW"
    assert outcome.reason == "PROJECTED_TERMINATION_PATH_INVALID"


@pytest.mark.parametrize("status", ["FAILED", "DEADLINE_EXCEEDED", "PREEMPTED"])
def test_closed_executor_failure_is_persisted_terminal_without_claiming_landing(safety, status):
    from sentinel.room.controller import RoomController
    from sentinel.room.models import TerminalState
    supervisor, store, clock, samples, refreshes, _ = safety

    class ClosedExecutor(Executor):
        def execute(self, plan):
            super().execute(plan)
            self.state = ExecutorState.CLOSED
            return CheckpointOutcome(status, (), (), None, None, True)

    class Judge:
        def choice(self, *args, **kwargs):
            pytest.fail("closed executor must never return control to Jev")

    executor = ClosedExecutor(store, clock)
    plan = checkpoint(samples["pose"], command())
    supervisor.dispatch(supervisor.assess(request(safety, plan), supervisor.site), executor)
    state = store.events("session")[-1].payload["state"]
    assert state["terminal_state"] == "ABORTED"
    assert state["airborne"] is None
    assert state["action_progress"] == status
    queries = ReadQueries(store, clock=clock, clock_domain_id=supervisor.clock_domain_id)
    snapshot = queries.project("session")
    assert snapshot.terminal_state == TerminalState.ABORTED
    assert dict(next(f for f in snapshot.sources if f.kind == "READ_DRONE").values)["state_freshness"] == "STALE"
    refresh_count = len(refreshes)
    with pytest.raises(RuntimeError, match="terminal"):
        supervisor.prepare(snapshot_version=snapshot.state_version)
    assert len(refreshes) == refresh_count
    assert supervisor.assess(request(safety), supervisor.site).transition != "CONTINUE"
    events = store.events("session")
    controller = RoomController(store, Judge(), supervisor=supervisor, flight_executor=executor)
    assert controller.step("session").terminal_state == TerminalState.ABORTED
    assert store.events("session") == events


def test_default_controller_queries_use_supervisor_clock_for_fresh_measured_state(safety):
    from sentinel.room.controller import RoomController
    supervisor, store, clock, samples, *_ = safety
    samples["pose"] = replace(samples["pose"], measured_at=clock() - .5)
    executor = Executor(store, clock)
    controller = RoomController(store, object(), supervisor=supervisor, flight_executor=executor)
    plan = checkpoint(samples["pose"], command())
    supervisor.dispatch(supervisor.assess(request(safety, plan), supervisor.site), executor)
    state = dict(next(f for f in controller.queries.project("session").sources if f.kind == "READ_DRONE").values)
    assert state["state_freshness"] == "FRESH"
    assert state["pose_age_s"] == .5
    assert controller.queries.clock is clock
    assert controller.queries.clock_domain_id == supervisor.clock_domain_id


def test_explicit_controller_queries_keep_the_callers_clock_binding(safety):
    from sentinel.room.controller import RoomController
    supervisor, store, clock, *_ = safety
    explicit_clock = lambda: 9000
    queries = ReadQueries(store, clock=explicit_clock, clock_domain_id="caller-clock")
    controller = RoomController(store, object(), queries=queries, supervisor=supervisor,
                                flight_executor=Executor(store, clock))
    assert controller.queries is queries
    assert queries.clock is explicit_clock and queries.clock_domain_id == "caller-clock"


def inject_concurrent_append(store, monkeypatch, event_type):
    original = store.append_events
    injected = []

    def append(**kwargs):
        if kwargs["events"][0]["event_type"] == event_type:
            key = f"concurrent:{len(injected)}"
            injected.append(key)
            original(session_id="session", expected_version=store.session_version("session"),
                events=({"event_type": "CONCURRENT", "payload": {}, "correlation_id": key,
                         "causation_id": None, "idempotency_key": key},))
        return original(**kwargs)

    monkeypatch.setattr(store, "append_events", append)
    return injected


@pytest.mark.parametrize("operation", ["lost", "refresh_failure", "recovery_stop"])
def test_concurrent_event_cannot_cancel_mandatory_safety_effect(safety, monkeypatch, operation):
    supervisor, store, clock, samples, _, emergencies = safety
    if operation == "lost":
        samples["pose"] = replace(samples["pose"], state=LocalizationState.LOST)
    elif operation == "recovery_stop":
        samples["pose"] = replace(samples["pose"], state=LocalizationState.RECOVERY_REQUIRED)
        supervisor.mapped_heading_tag_ids = ((90, (1, 2)),)
        supervisor.recovery = SimpleNamespace(tracker=supervisor.tracker,
            next_action=lambda *args: SimpleNamespace(kind="EMERGENCY", reason="RECOVERY_EXHAUSTED"))
    outcome = supervisor.assess(request(safety), supervisor.site)
    assessed_version = outcome.request.snapshot_version
    injected = inject_concurrent_append(store, monkeypatch, "SAFETY_ASSESSED")
    executor = Executor(store, clock)
    result = supervisor.refresh_failure(executor) if operation == "refresh_failure" else supervisor.dispatch(outcome, executor)
    assert result.state == "EMERGENCY_ABORTED" and emergencies == [True]
    assert executor.calls == [] and injected
    audits = [e for e in store.events("session") if e.event_type == "SAFETY_ASSESSED"]
    assert audits[0].payload["assessed_snapshot_version"] == assessed_version
    assert all(e.payload["append_version"] == e.state_version - 1 for e in audits)
    assert store.events("session")[-1].event_type == "DRONE_STATE_RECORDED"


def test_measured_completion_survives_concurrent_append(safety, monkeypatch):
    supervisor, store, clock, samples, *_ = safety
    injected = inject_concurrent_append(store, monkeypatch, "CHECKPOINT_COMPLETED")
    plan = checkpoint(samples["pose"], command())
    result = supervisor.dispatch(supervisor.assess(request(safety, plan), supervisor.site), Executor(store, clock))
    assert result.status == "COMPLETED" and injected
    assert [e.event_type for e in store.events("session")][-3:] == [
        "CONCURRENT", "CHECKPOINT_COMPLETED", "DRONE_STATE_RECORDED"]


@pytest.mark.parametrize("operation", ["return", "refresh_failure", "completion"])
def test_permanent_audit_failure_uses_emergency_and_latches_local_terminal(safety, monkeypatch, operation):
    supervisor, store, clock, samples, *_ = safety
    if operation == "return":
        samples["energy"] = EnergySample(22, clock())
    plan = checkpoint(samples["pose"], command(seconds=100) if operation == "return" else command(), command())
    outcome = supervisor.assess(request(safety, plan), supervisor.site)
    original = store.append_events

    def fail(**kwargs):
        if operation != "completion" or kwargs["events"][0]["event_type"] == "CHECKPOINT_COMPLETED":
            raise sqlite3.OperationalError("disk I/O error")
        return original(**kwargs)

    monkeypatch.setattr(store, "append_events", fail)
    emergency_calls = []

    def emergency(executor):
        emergency_calls.append(True)
        executor.state = ExecutorState.TERMINAL
        return TerminalOutcome("EMERGENCY_ABORTED", "AUDIT_PERSISTENCE_FAILED", None, None, clock())

    supervisor.emergency = emergency
    executor = Executor(store, clock)
    result = supervisor.refresh_failure(executor) if operation == "refresh_failure" else supervisor.dispatch(outcome, executor)
    assert result.state == "EMERGENCY_ABORTED" and emergency_calls == [True]
    # The two stationary captures go out as one batch before its completion
    # record fails.
    assert executor.calls == (["CAPTURE", "CAPTURE"] if operation == "completion" else [])
    assert supervisor.last_terminal_outcome == result
    assert supervisor.last_persistence_error
    with pytest.raises(RuntimeError, match="terminal"):
        supervisor.prepare(snapshot_version=store.session_version("session"))
    assert not any(e.event_type == "DRONE_STATE_RECORDED" for e in store.events("session"))
    # A later writable ledger does not renew permission to issue emergency.
    monkeypatch.setattr(store, "append_events", original)
    assert supervisor.refresh_failure(executor) == result
    assert emergency_calls == [True]


def test_mandatory_audit_retry_keeps_original_identity_after_unrelated_append(safety):
    supervisor, store, _, samples, *_ = safety
    samples["pose"] = replace(samples["pose"], state=LocalizationState.LOST)
    outcome = supervisor.assess(request(safety), supervisor.site)
    first = supervisor._audit(outcome)
    store.append_event(session_id="session", expected_version=store.session_version("session"),
        event_type="CONCURRENT", payload={}, correlation_id="other", causation_id=None, idempotency_key="other")
    assert supervisor._audit(outcome) == first
    assert len([e for e in store.events("session") if e.event_type == "SAFETY_ASSESSED"]) == 1


def test_optional_audit_conflict_cannot_authorize_stale_movement(safety, monkeypatch):
    supervisor, store, clock, samples, *_ = safety
    inject_concurrent_append(store, monkeypatch, "SAFETY_ASSESSED")
    plan = checkpoint(samples["pose"], command("MOVE", direction="right", distance_cm=20))
    executor = Executor(store, clock)
    with pytest.raises(VersionConflict):
        supervisor.dispatch(supervisor.assess(request(safety, plan), supervisor.site), executor)
    assert executor.calls == []


def test_terminal_completion_storage_failure_does_not_repeat_landing(safety, monkeypatch):
    supervisor, store, clock, samples, _, emergencies = safety
    original = store.append_events

    def fail(**kwargs):
        if kwargs["events"][0]["event_type"] == "CHECKPOINT_COMPLETED":
            raise sqlite3.OperationalError("disk I/O error")
        return original(**kwargs)

    monkeypatch.setattr(store, "append_events", fail)
    plan = checkpoint(samples["pose"], command("LAND"))
    executor = Executor(store, clock)
    result = supervisor.dispatch(supervisor.assess(request(safety, plan), supervisor.site), executor)
    assert result.state == "LANDED" and executor.calls == ["LAND"] and emergencies == []
    assert supervisor.last_terminal_outcome == result and supervisor.last_persistence_error


def test_takeoff_is_admitted_from_the_declared_pad_when_the_tracker_never_had_a_fix(safety):
    from sentinel.cube.localization import LocalizationTracker
    supervisor, _, clock, samples, *_ = safety
    fresh = LocalizationTracker(supervisor.tracker.profile, clock=clock)
    supervisor.tracker = fresh
    samples["pose"] = fresh.health
    assert samples["pose"].state == LocalizationState.LOST
    supervisor.validate_start()  # no tag fix on the pad: still admitted


def test_a_tracker_lost_after_a_fix_still_blocks_takeoff(safety):
    supervisor, _, _, samples, *_ = safety
    samples["pose"] = supervisor.tracker.mark_lost()
    with pytest.raises(ValueError, match="LOCALIZATION_LOST"):
        supervisor.validate_start()


# ---------------------------------------------------------------------------
# 2026-09-26: the CanningSt battery floor is supervisor-owned (22 %)
# ---------------------------------------------------------------------------

def _canningst_energy():
    from pathlib import Path
    from sentinel.room import live_session
    return live_session.load_energy_profile(
        Path(__file__).resolve().parents[2] / "fixtures" / "room_agent" / "v1" / "profiles" / "canningst-energy.json")


def _floor_guarded(safety, *, disable_predictive_guard=False):
    """The fixture supervisor pricing energy with the committed CanningSt
    burn rate and landing floor (bindings are the fixture's, not these)."""
    supervisor, _, clock, *_ = safety
    committed = _canningst_energy()
    profile = replace(supervisor.reserve.profile, landing_floor_pct=committed.landing_floor_pct,
                      percent_per_second_upper=committed.percent_per_second_upper)
    supervisor.reserve = ReserveGuard(profile, clock, disable_predictive_guard=disable_predictive_guard)
    supervisor.validate_bindings = lambda site: None
    return supervisor


def _zone_cost(safety, plan):
    supervisor, _, clock, samples, *_ = safety
    samples["energy"] = EnergySample(90, clock())
    admitted = supervisor.assess(request(safety, plan), supervisor.site)
    assert admitted.transition == "CONTINUE", admitted.reason
    return admitted.energy.action_cost_pct


def test_work_whose_projection_crosses_the_22_percent_floor_is_a_mandatory_return(safety):
    # Live 2026-09-26 (l3-20260926-111405): with the predictive guard off the
    # battery drained to 9 % and the flight ended on a motor-stop mid-turn.
    # The supervisor, not Jev, returns once the next zone would land below 22 %.
    supervisor, store, clock, samples, *_ = safety
    _floor_guarded(safety)
    assert supervisor.reserve.profile.landing_floor_pct == 22
    zone = checkpoint(samples["pose"], command(seconds=30))
    cost = _zone_cost(safety, zone)
    samples["energy"] = EnergySample(22 + cost, clock())
    affordable = supervisor.assess(request(safety, zone), supervisor.site)
    assert affordable.transition == "CONTINUE"
    assert dict(affordable.energy.components)["predictive_guard_disabled"] == 0.0
    samples["energy"] = EnergySample(22 + cost - 1, clock())
    crossing = supervisor.assess(request(safety, zone), supervisor.site)
    assert (crossing.transition, crossing.reason) == ("RETURN_NOW", "OPTIONAL_WORK_UNAFFORDABLE")
    assert crossing.energy.projected_landing_pct == 21 and crossing.energy.landing_floor_pct == 22
    executor = Executor(store, clock)
    supervisor.dispatch(crossing, executor)
    assert supervisor.reserve.return_latched and "CAPTURE" not in executor.calls


def test_below_the_floor_with_no_affordable_return_the_supervisor_lands_in_place(safety):
    supervisor, _, clock, samples, *_ = safety
    _floor_guarded(safety)
    samples["energy"] = EnergySample(21, clock())
    outcome = supervisor.assess(request(safety, checkpoint(samples["pose"], command(seconds=30))), supervisor.site)
    assert (outcome.transition, outcome.reason) == ("LAND_CONTINGENCY", "RETURN_ENERGY_UNAVAILABLE")


def test_the_operator_override_still_disables_the_predictive_floor(safety):
    # The explicit --disable-battery-guard override keeps the 2026-09-24
    # behaviour: no projected cost, so the same crossing continues.
    supervisor, _, clock, samples, *_ = safety
    zone = checkpoint(samples["pose"], command(seconds=30))
    _floor_guarded(safety)
    cost = _zone_cost(safety, zone)
    _floor_guarded(safety, disable_predictive_guard=True)
    samples["energy"] = EnergySample(22 + cost - 1, clock())
    outcome = supervisor.assess(request(safety, zone), supervisor.site)
    assert outcome.transition == "CONTINUE"
    assert dict(outcome.energy.components)["predictive_guard_disabled"] == 1.0


def test_a_declared_translation_speed_is_bound_by_the_calibration_hash_and_its_absence_changes_nothing():
    import hashlib
    import json
    room_site = site()
    profile = EnergyProfile("energy", room_site.cube_site_sha256, "aircraft", .01, 1, 20, 2, "SIMULATION_ONLY")
    drift = tracker()[0].profile
    base = CommandLimits(trim_rounds=0, localise=False)
    faster = replace(base, translation_speed_cm_s=40)
    assert calibration_hashes(profile, drift, base, room_site) != calibration_hashes(profile, drift, faster, room_site)
    # Grants issued before the field existed still bind: an undeclared speed
    # hashes exactly as the limits did without it.
    legacy = {k: v for k, v in asdict(base).items() if k != "translation_speed_cm_s"}
    digest = hashlib.sha256(json.dumps({"commands": legacy, "landing_upper_bound_s": 10.0}, sort_keys=True,
                                       separators=(",", ":")).encode("utf-8")).hexdigest()
    assert digest in calibration_hashes(profile, drift, base, room_site)
