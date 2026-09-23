from dataclasses import replace
from threading import Event, Thread, current_thread
import time

import pytest

from sentinel.cube.checkpoint import CheckpointPlan, CommandOutcome, HomeDefinition, PlannedCommand
from sentinel.cube.step_executor import ExecutorState, HomeMeasurementError, HomeState, StepExecutor
from sentinel.cube.supervised_wait import SupervisedWait
from sentinel.dry_run import DryRunClient
from sentinel.envelope import FlightEnvelope
from sentinel.models import Position, Telemetry
from sentinel.runner import FlightTestRunner


HOME = HomeDefinition("pad", Position(0, 0, 200), 90.0, "home-route")


class Client(DryRunClient):
    def __init__(self):
        super().__init__()
        self.close_count = 0

    def close(self):
        self.close_count += 1
        super().close()


def command(kind="MOVE", bound=1.0, **arguments):
    if kind == "MOVE" and not arguments:
        arguments = {"direction": "forward", "distance_cm": 20}
    return PlannedCommand.from_mapping(kind, arguments, upper_bound_s=bound,
                                       interruptible=kind in {"CAPTURE", "LOCALISE", "SETTLE"})


def plan(*commands, start=None):
    return CheckpointPlan("p", start or HOME.nominal_pose, Position(0, 20, 200), 90, 90,
                          commands, sum(c.upper_bound_s for c in commands))


def executor(**kwargs):
    client = kwargs.pop("client", None) or Client()
    runner = FlightTestRunner(client, "dry-run", retain_video_stream=True,
                              envelope=FlightEnvelope())
    measured = HomeState(Position(2.5, -1, 200), 91.0, "home-route", 12.0)
    result = StepExecutor(runner, measure_home=lambda definition: measured, **kwargs)
    return result, client, measured


def threaded(operation):
    result = []
    thread = Thread(target=lambda: result.append(operation()), daemon=True)
    thread.start()
    return thread, result


def test_one_connection_takeoff_and_terminal_action_span_several_checkpoints():
    run, client, measured = executor()
    assert run.start(HOME) == measured
    assert run.start(HOME) is run.home
    first = run.execute(plan(command()))
    second = run.execute(plan(command(direction="back", distance_cm=20)))
    assert first.status == second.status == "COMPLETED"
    assert first.completion_known and first.command_states[0].status == "COMPLETED"
    assert first.command_states[0].completed_at >= first.command_states[0].started_at
    terminal = run.land()
    assert run.land() is terminal
    run.close()
    run.close()
    assert run.state is ExecutorState.CLOSED
    assert client.commands.count("command") == client.commands.count("takeoff") == 1
    assert client.commands.count("streamon") == client.commands.count("land") == 1
    assert client.close_count == 1
    assert run.land() is terminal
    with pytest.raises(RuntimeError, match="AIRBORNE"):
        run.execute(plan(command()))


def test_home_is_measured_and_validation_never_clamps_it():
    run, client, measured = executor(home_tolerance_cm=1.0)
    with pytest.raises(ValueError, match="home"):
        run.start(HOME)
    assert run.home == measured
    assert run.home.pose != HOME.nominal_pose
    assert client.commands[-1] == "land" and client.close_count == 1


def test_surveyed_touchdown_height_is_distinct_from_measured_airborne_home():
    run, client, measured = executor()
    touchdown = replace(HOME, nominal_pose=Position(0, 0, 0))
    assert run.start(touchdown) == measured
    assert run.runner.envelope.position == measured.pose
    assert run.home.pose.z_cm == 200 and touchdown.nominal_pose.z_cm == 0
    run.close()


def test_reanchor_commits_durably_before_executor_pose_and_invalidates_old_return():
    run, client, _ = executor()
    run.start(HOME)
    old = run.runner.envelope.position
    old_plan = plan(command(), start=old)
    revision, generation = run.pose_revision, run._generation
    committed = []
    corrected = Position(20, 0, 200)
    run.apply_reanchor(corrected, persist=lambda: committed.append(run.runner.envelope.position))
    assert committed == [old]
    assert run.runner.envelope.position == corrected
    assert run.pose_revision == revision + 1 and run._generation > generation
    assert run.return_home(old_plan).status == "REPLAN_REQUIRED"
    assert run.return_home(plan(command(), start=corrected)).status == "COMPLETED"
    run.close()


@pytest.mark.parametrize("position", [Position(float("nan"), 0, 200), Position(1000, 0, 200)])
def test_invalid_reanchor_never_persists_or_updates_pose(position):
    run, client, measured = executor()
    run.start(HOME)
    persisted = []
    with pytest.raises(ValueError, match="reanchor"):
        run.apply_reanchor(position, persist=lambda: persisted.append(True))
    assert persisted == [] and run.runner.envelope.position == measured.pose
    run.close()


@pytest.mark.parametrize("field", ["heading_deg", "xy_uncertainty_cm", "estimator_failures", "pose_revision"])
def test_reanchor_health_rejects_nonfinite_state_before_persistence(field):
    from tests.cube.test_localization import tracker
    value, _ = tracker()
    run, client, measured = executor()
    run.start(HOME)
    pose = replace(value.health, position=Position(20, 0, 200), **{field: float("nan")})
    persisted = []
    with pytest.raises(ValueError, match="reanchor"):
        run.apply_reanchor(pose, persist=lambda: persisted.append(True))
    assert not persisted and run.runner.envelope.position == measured.pose
    run.close()


def test_reanchor_invalidates_optional_work_already_waiting_for_operation_lock():
    run, client, _ = executor()
    run.start(HOME)
    operation_lock, waiting = run._operation_lock, Event()

    class QueuedOperation:
        def __enter__(self):
            waiting.set()
            operation_lock.acquire()

        def __exit__(self, *args):
            operation_lock.release()

    with operation_lock:
        run._operation_lock = QueuedOperation()
        worker, results = threaded(lambda: run.execute(plan(command())))
        assert waiting.wait(1)
        run.apply_reanchor(Position(20, 0, 200), persist=lambda: None)
    worker.join(1)
    assert not worker.is_alive()
    assert results[0].status == "PREEMPTED"
    assert "forward 20" not in client.commands
    run.close()


def test_reanchor_persistence_failure_leaves_executor_pose_and_generation_intact():
    run, client, measured = executor()
    run.start(HOME)
    revision, generation = run.pose_revision, run._generation

    def fail():
        raise OSError("database unavailable")

    with pytest.raises(OSError):
        run.apply_reanchor(Position(20, 0, 200), persist=fail)
    assert run.runner.envelope.position == measured.pose
    assert run.pose_revision == revision and run._generation == generation
    run.close()


def test_emergency_sends_while_reanchor_persistence_blocks_and_preempts_pose_publication():
    entered, release, emergency_sent = Event(), Event(), Event()

    class EmergencyObserved(Client):
        def emergency(self):
            response = super().emergency()
            emergency_sent.set()
            return response

    run, client, measured = executor(client=EmergencyObserved())
    run.start(HOME)
    outcomes, persisted = [], []

    def persist():
        entered.set()
        assert release.wait(3)
        persisted.append(True)

    def reanchor():
        try:
            run.apply_reanchor(Position(20, 0, 200), persist=persist)
        except Exception as exc:
            outcomes.append(exc)

    worker, _ = threaded(reanchor)
    emergency = None
    try:
        assert entered.wait(1)
        emergency, _ = threaded(run.runner.trigger_emergency)
        assert emergency_sent.wait(1), "durable I/O must never block the operator emergency send"
        assert run.state == ExecutorState.TERMINAL
    finally:
        release.set()
        worker.join(1)
        if emergency is not None:
            emergency.join(1)
        run.close()
    assert persisted == [True]
    assert len(outcomes) == 1 and type(outcomes[0]).__name__ == "ReanchorPreempted"
    assert outcomes[0].persisted is True
    assert run.runner.envelope.position == measured.pose
    assert client.commands.count("emergency") == 1 and "land" not in client.commands


def test_measured_home_route_and_time_are_validated():
    run, client, measured = executor()
    run.measure_home = lambda definition: replace(measured, route_id="other")
    with pytest.raises(ValueError, match="home"):
        run.start(HOME)
    assert run.home.route_id == "other"
    assert client.close_count == 1


@pytest.mark.parametrize("terminal_action", ["land", "close"])
def test_terminal_action_cancels_blocked_home_measurement_without_waiting_for_callback(terminal_action):
    entered, release, finished = Event(), Event(), Event()
    run, client, measured = executor()
    original_pose = run.runner.envelope.position

    def measure(definition):
        assert definition is HOME
        entered.set()
        assert release.wait(3)
        finished.set()
        return measured

    def start():
        try:
            return run.start(HOME)
        except RuntimeError as error:
            return error

    run.measure_home = measure
    worker, results = threaded(start)
    assert entered.wait(1)
    terminal, _ = threaded(getattr(run, terminal_action))
    try:
        terminal.join(0.3)
        worker.join(0.3)
        assert not terminal.is_alive(), "landing must not wait for a noncooperative measurement"
        assert not worker.is_alive()
        assert isinstance(results[0], HomeMeasurementError)
        assert results[0].status == "PREEMPTED" and not results[0].completion_known
        assert run.home is None and run.runner.envelope.position == original_pose
        assert client.commands.count("land") == client.commands.count("takeoff") == 1
        assert client.close_count == 1
        with pytest.raises(RuntimeError, match="AIRBORNE"):
            run.execute(plan(command()))
    finally:
        release.set()
        worker.join(1)
        terminal.join(1)
    assert finished.wait(1)
    assert run.home is None and run.runner.envelope.position == original_pose
    assert run.state is ExecutorState.CLOSED


def test_home_measurement_timeout_lands_and_discards_late_home():
    entered, release, finished = Event(), Event(), Event()
    now = [10.0]
    run, client, measured = executor(home_measurement_timeout_s=0.05, clock=lambda: now[0])
    original_pose = run.runner.envelope.position

    def measure(definition):
        entered.set()
        now[0] += .1
        assert release.wait(3)
        finished.set()
        return measured

    run.measure_home = measure
    try:
        with pytest.raises(RuntimeError) as failure:
            run.start(HOME)
        assert isinstance(failure.value, HomeMeasurementError)
        assert entered.is_set()
        assert failure.value.status == "DEADLINE_EXCEEDED"
        assert not failure.value.completion_known
        assert run.state is ExecutorState.CLOSED
        assert run.home is None and run.runner.envelope.position == original_pose
        assert client.commands.count("takeoff") == client.commands.count("land") == 1
        assert client.close_count == 1
        assert run.land().reason == "DEADLINE_EXCEEDED"
        with pytest.raises(RuntimeError, match="AIRBORNE"):
            run.execute(plan(command()))
    finally:
        release.set()
    assert finished.wait(1)
    assert run.home is None and run.runner.envelope.position == original_pose


def test_home_measurement_uses_admitted_deadline_and_cancellation_registration():
    run, client, measured = executor(home_measurement_timeout_s=3.0, deadline=12.0, clock=lambda: 10.0)
    wait = run._wait.run
    calls = []

    def supervise(operation, deadline, on_deadline, *, cancellation):
        calls.append(deadline)
        assert cancellation is run._active_cancellation
        assert not cancellation.is_set()
        return wait(operation, deadline, on_deadline, cancellation=cancellation)

    run._wait.run = supervise
    assert run.start(HOME) is measured
    assert calls == [12.0]  # The overall deadline also bounds an interruptible measurement.
    assert run._active_cancellation is None
    assert run.home is measured and run.runner.envelope.position == measured.pose
    assert run.state is ExecutorState.AIRBORNE
    assert run.start(HOME) is measured and calls == [12.0]
    run.close()


def test_completed_home_measurement_is_discarded_when_land_wins_publication_race():
    run, client, _ = executor()
    original_pose = run.runner.envelope.position
    wait = run._wait.run

    def preempt_after_worker_completed(*args, **kwargs):
        result = wait(*args, **kwargs)
        assert result.status == "COMPLETED"
        run.land()
        return result

    run._wait.run = preempt_after_worker_completed
    with pytest.raises(RuntimeError) as failure:
        run.start(HOME)
    assert isinstance(failure.value, HomeMeasurementError)
    assert failure.value.status == "PREEMPTED" and failure.value.completion_known
    assert run.home is None and run.runner.envelope.position == original_pose
    assert run.state is ExecutorState.CLOSED
    assert client.commands.count("land") == 1 and client.close_count == 1


def test_home_measurement_failure_is_sanitized_and_lands_once():
    run, client, _ = executor()

    def measure(definition):
        raise OSError("password=very-secret")

    run.measure_home = measure
    with pytest.raises(RuntimeError) as failure:
        run.start(HOME)
    assert isinstance(failure.value, HomeMeasurementError)
    assert failure.value.status == "FAILED" and failure.value.completion_known
    assert failure.value.error_category == "OPERATION_FAILED"
    assert "secret" not in repr(failure.value)
    assert run.home is None and run.state is ExecutorState.CLOSED
    assert client.commands.count("land") == 1 and client.close_count == 1


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True])
def test_invalid_home_measurement_timeout_is_rejected_before_takeoff(timeout):
    client = Client()
    with pytest.raises(ValueError, match="home_measurement_timeout_s"):
        executor(client=client, home_measurement_timeout_s=timeout)
    assert "takeoff" not in client.commands


def test_return_latch_blocks_all_later_optional_work():
    run, client, _ = executor()
    run.start(HOME)
    assert run.return_home(plan(command(), start=run.runner.envelope.position)).status == "COMPLETED"
    assert run.state is ExecutorState.RETURNING
    with pytest.raises(RuntimeError, match="AIRBORNE"):
        run.execute(plan(command()))
    run.close()
    assert client.commands.count("land") == 1


def test_start_failure_attempts_terminal_action_once_and_closes():
    class Broken(Client):
        def takeoff(self):
            super().takeoff()
            raise RuntimeError("secret credential")

    run, client, _ = executor(client=Broken())
    with pytest.raises(RuntimeError):
        run.start(HOME)
    run.close()
    assert client.commands.count("takeoff") == client.commands.count("land") == 1
    assert client.close_count == 1


def test_command_errors_are_sanitized_and_cleanup_is_terminal():
    def fail(command, cancellation):
        raise OSError("password=very-secret")

    run, client, _ = executor(command_sink=fail)
    run.start(HOME)
    outcome = run.execute(plan(command()))
    assert outcome.status == "FAILED" and outcome.completion_known
    assert outcome.command_states[0].error_category == "OPERATION_FAILED"
    assert "secret" not in repr(outcome)
    assert client.commands.count("land") == 1 and client.close_count == 1


def test_concurrent_start_and_land_are_exactly_once():
    run, client, _ = executor()
    threads = [threaded(lambda: run.start(HOME))[0] for _ in range(5)]
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()
    threads = [threaded(run.land)[0] for _ in range(5)]
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()
    run.close()
    assert client.commands.count("takeoff") == client.commands.count("land") == 1


def test_noninterruptible_bound_is_admitted_before_any_command():
    run, client, _ = executor(deadline=time.monotonic() + 10)
    run.start(HOME)
    outcome = run.execute(plan(command(bound=11)))
    assert outcome.status == "DEADLINE_EXCEEDED"
    assert "forward 20" not in client.commands
    assert outcome.command_states[0].status == "NOT_STARTED"
    assert outcome.completion_known


def test_sdk_timeout_keeps_lock_until_reply_and_never_sends_replacement():
    entered, release, finished = Event(), Event(), Event()

    class Blocking(Client):
        def move(self, direction, distance_cm):
            entered.set()
            assert release.wait(3)
            result = super().move(direction, distance_cm)
            finished.set()
            return result

    run, client, _ = executor(client=Blocking())
    run.start(HOME)
    thread, outcomes = threaded(lambda: run.execute(plan(command(bound=0.05), command())))
    try:
        assert entered.wait(1)
        thread.join(1)
        assert not thread.is_alive()
        assert outcomes[0].status == "COMMAND_COMPLETION_UNKNOWN"
        assert not outcomes[0].completion_known
        assert not run.command_lock.acquire(blocking=False)
        assert "land" not in client.commands
        with pytest.raises(RuntimeError, match="AIRBORNE"):
            run.execute(plan(command()))
    finally:
        release.set()
    assert finished.wait(1)
    run.close()
    assert client.commands.count("forward 20") == client.commands.count("land") == 1
    assert client.close_count == 1


def test_legacy_command_crossing_deadline_never_fabricates_completion():
    now = [0.0]
    run, client, _ = executor(clock=lambda: now[0], deadline=1.5)
    run.start_legacy()
    calls = []

    def sink(node):
        calls.append(node.kind)
        now[0] = 2.0
        return CommandOutcome()

    result = run.execute_legacy(plan(command(bound=1)), sink)
    assert result is not None and result.status == "COMMAND_COMPLETION_UNKNOWN"
    assert not result.completion_known
    assert result.command_states[0].completed_at is None
    assert run.state in {ExecutorState.TERMINAL, ExecutorState.CLOSED}
    with pytest.raises(RuntimeError, match="AIRBORNE"):
        run.execute_legacy(plan(command()), sink)
    assert calls == ["MOVE"]
    run.close()


def test_blocked_legacy_command_returns_unknown_and_retains_command_lock():
    now = [10.0]
    entered, release = Event(), Event()
    run, client, _ = executor(clock=lambda: now[0])
    run.start_legacy()

    def sink(node):
        entered.set()
        release.wait(3)
        return CommandOutcome()

    worker, results = threaded(lambda: run.execute_legacy(plan(command(bound=.05)), sink))
    try:
        assert entered.wait(1)
        now[0] += .1
        worker.join(1)
        assert not worker.is_alive()
        assert results[0].status == "COMMAND_COMPLETION_UNKNOWN"
        assert not run.command_lock.acquire(blocking=False)
        assert "land" not in client.commands
    finally:
        release.set()
        worker.join(1)
        run.close()


@pytest.mark.parametrize("kind", ["LOCALISE", "TRIM_ALLOWANCE", "CAPTURE"])
@pytest.mark.parametrize("cooperative", [False, True])
def test_legacy_callback_with_blocked_nested_sdk_move_retains_physical_uncertainty(kind, cooperative):
    now = [10.0]
    entered, release = Event(), Event()

    class BlockedMove(Client):
        def move(self, direction, distance_cm):
            entered.set()
            assert release.wait(3)
            return super().move(direction, distance_cm)

    run, client, _ = executor(client=BlockedMove(), clock=lambda: now[0])
    run.start(HOME)

    def sink(node):
        run.runner.move_checked("forward", 20)
        return CommandOutcome()

    node = PlannedCommand(kind, (), 1, True)
    if cooperative:
        from sentinel.cube.step_executor import CooperativeLegacySink
        original = sink
        sink = CooperativeLegacySink(lambda command, cancellation: original(command))
    worker, outcomes = threaded(lambda: run.execute_legacy(plan(node), sink))
    try:
        assert entered.wait(1)
        now[0] += 2
        worker.join(1)
        assert not worker.is_alive()
        result = outcomes[0]
        assert result.status == "COMMAND_COMPLETION_UNKNOWN"
        assert not result.completion_known and result.command_states[0].completed_at is None
        assert run._command_completion_unknown
        assert not run.command_lock.acquire(blocking=False)
        assert "land" not in client.commands
        with pytest.raises(RuntimeError, match="AIRBORNE"):
            run.execute(plan(command()))
    finally:
        release.set()
        worker.join(1)
        run.close()
    assert client.commands.count("forward 20") == client.commands.count("land") == 1


def test_timely_legacy_failure_preserves_exception_and_terminal_cleanup():
    run, client, _ = executor()
    run.start_legacy()

    def sink(node):
        raise ValueError("legacy failure")

    with pytest.raises(ValueError, match="legacy failure"):
        run.execute_legacy(plan(command()), sink)
    assert client.commands.count("land") == client.close_count == 1


def test_cooperative_legacy_sink_cannot_send_a_late_nested_command_after_provider_timeout():
    from sentinel.cube.step_executor import CooperativeLegacySink
    now = [10.0]
    entered, release, finished, landed = Event(), Event(), Event(), Event()

    class LandingObserved(Client):
        def land(self):
            result = super().land()
            landed.set()
            return result

    run, client, measured = executor(client=LandingObserved(), clock=lambda: now[0])
    run.start(HOME)
    rejected = []

    def callback(command, cancellation):
        entered.set()
        assert release.wait(3)
        try:
            # SDK admission enforces the expired generation even if a
            # provider ignores its cancellation notification.
            run.runner.move_checked("forward", 20)
        except RuntimeError as exc:
            rejected.append(str(exc))
        finally:
            finished.set()
        return CommandOutcome()

    worker, outcomes = threaded(lambda: run.execute_legacy(
        plan(PlannedCommand("LOCALISE", (), 1, True)), CooperativeLegacySink(callback)))
    try:
        assert entered.wait(1)
        now[0] += 2
        worker.join(1)
        assert not worker.is_alive()
        assert outcomes[0].status == "DEADLINE_EXCEEDED" and outcomes[0].completion_known
        assert landed.wait(1)
        assert not run._command_completion_unknown
    finally:
        release.set()
        assert finished.wait(1)
        run.close()
    assert rejected == ["legacy command was cancelled"]
    assert "forward 20" not in client.commands and client.commands.count("land") == 1
    assert run.runner.envelope.position == measured.pose


@pytest.mark.parametrize("session_bound", [False, True])
def test_native_landing_watchdog_returns_unknown_without_releasing_sdk_lock(session_bound):
    entered, release = Event(), Event()
    now = [10.0]

    class BlockingLand(Client):
        def land(self):
            self._record("land-entered")
            entered.set()
            release.wait(3)
            return super().land()

    run, client, _ = executor(client=BlockingLand(), clock=lambda: now[0])
    run.start(HOME)
    if session_bound:
        run.deadline = now[0] + .05
    worker, results = threaded(lambda: run.land(upper_bound_s=1 if session_bound else .05))
    try:
        assert entered.wait(1)
        now[0] += .1
        worker.join(1)
        assert not worker.is_alive()
        terminal = results[0]
        assert terminal.state == terminal.reason == "COMMAND_COMPLETION_UNKNOWN"
        assert terminal.completed_at is None
        assert run.land() is terminal
        assert not run.command_lock.acquire(blocking=False)
        assert client.commands.count("land-entered") == 1
        with pytest.raises(RuntimeError, match="AIRBORNE"):
            run.execute(plan(command()))
    finally:
        release.set()
        worker.join(1)
        run.close()
    assert run.land() is terminal
    assert client.commands.count("land") == client.close_count == 1


@pytest.mark.parametrize("cooperative", [True, False])
def test_supervised_wait_cancels_and_discards_late_provider_results(cooperative):
    now = [10.0]
    entered, release, cancelled, terminal = Event(), Event(), Event(), Event()

    def operation(cancellation):
        entered.set()
        if cooperative:
            cancellation.wait(1)
            cancelled.set()
        else:
            release.wait(2)
        return ["late candidate"]

    wait = SupervisedWait(clock=lambda: now[0])
    thread, results = threaded(lambda: wait.run(operation, now[0] + .05, terminal.set))
    try:
        assert entered.wait(1)
        now[0] += .1
        assert terminal.wait(1)
        thread.join(1)
        assert not thread.is_alive()
        assert results[0].status == "DEADLINE_EXCEEDED" and results[0].value is None
        if cooperative:
            assert cancelled.wait(1)
    finally:
        release.set()
    assert results[0].value is None


def test_deadline_before_operation_prevents_launch():
    called, expired = [], []
    result = SupervisedWait().run(lambda cancellation: called.append(True),
                                  time.monotonic() - 1, lambda: expired.append(True))
    assert not called and expired == [True]
    assert result.status == "DEADLINE_EXCEEDED" and result.completion_known


def test_operation_completion_before_deadline_wins_once():
    expired = []
    result = SupervisedWait().run(lambda cancellation: 42, time.monotonic() + 1,
                                  lambda: expired.append(True))
    assert result.status == "COMPLETED" and result.value == 42
    assert not expired and result.completion_known


def test_operation_crossing_deadline_cannot_publish_even_before_watchdog_tick():
    clock = [1.0]

    def operation(cancellation):
        clock[0] = 3.0
        return "too late"

    expired = Event()
    result = SupervisedWait(clock=lambda: clock[0]).run(operation, 2.0, expired.set)
    assert result.status == "DEADLINE_EXCEEDED" and result.value is None
    assert expired.wait(1)


def test_watchdog_callback_failure_is_sanitized_and_cannot_hang_wait():
    def fail():
        raise RuntimeError("api-key=secret")

    result = SupervisedWait().run(lambda cancellation: None, time.monotonic() - 1, fail)
    assert result.status == "WATCHDOG_FAILED"
    assert result.error_category == "WATCHDOG_FAILED" and "secret" not in repr(result)


def test_supervisor_can_land_while_noncooperative_perception_still_runs():
    entered, release, closed = Event(), Event(), Event()

    class Closing(Client):
        def close(self):
            super().close()
            closed.set()

    def perception(command, cancellation):
        entered.set()
        release.wait(2)
        return "late-observation"

    run, client, _ = executor(client=Closing(), command_sink=perception)
    run.start(HOME)
    thread, results = threaded(lambda: run.execute(plan(command("LOCALISE", bound=0.05))))
    try:
        assert entered.wait(1)
        thread.join(1)
        assert not thread.is_alive()
        assert results[0].observation_ids == ()
        assert closed.wait(0.2), "provider waits must not hold the movement lock"
        assert client.commands[-1] == "land"
    finally:
        release.set()
        run.close()


def test_terminal_request_during_start_cannot_be_overwritten_by_takeoff():
    entered, release = Event(), Event()
    run, client, _ = executor()

    def progress(event, message):
        if message == "sending takeoff":
            entered.set()
            release.wait(2)

    run.runner.progress = progress
    errors = []

    def start():
        try:
            run.start(HOME)
        except RuntimeError as error:
            errors.append(error)

    start_thread, _ = threaded(start)
    assert entered.wait(1)
    land_thread, _ = threaded(run.land)
    deadline = time.monotonic() + 1
    while run.state is not ExecutorState.TERMINAL and time.monotonic() < deadline:
        time.sleep(0.001)
    release.set()
    start_thread.join(1)
    land_thread.join(1)
    run.close()
    assert "takeoff" not in client.commands
    assert errors


def test_return_preempts_queued_optional_commands_without_overlapping_sdk_calls():
    entered, release = Event(), Event()

    class Blocking(Client):
        def move(self, direction, distance_cm):
            if direction == "forward":
                entered.set()
                assert release.wait(2)
            return super().move(direction, distance_cm)

    run, client, _ = executor(client=Blocking())
    run.start(HOME)
    optional, outcomes = threaded(lambda: run.execute(plan(command(bound=2), command())))
    assert entered.wait(1)
    return_plan = plan(command(direction="back", distance_cm=20), start=run.runner.envelope.position)
    returning, returns = threaded(lambda: run.return_home(return_plan))
    deadline = time.monotonic() + 1
    while run.state is not ExecutorState.RETURNING and time.monotonic() < deadline:
        time.sleep(0.001)
    assert run.state is ExecutorState.RETURNING
    release.set()
    optional.join(1)
    returning.join(1)
    assert outcomes[0].status == "PREEMPTED" and returns[0].status == "REPLAN_REQUIRED"
    assert outcomes[0].pose == return_plan.start.moved("forward", 20)
    assert run.pose_revision == 1 and "back 20" not in client.commands
    fresh = plan(command(direction="back", distance_cm=20), start=outcomes[0].pose)
    assert run.return_home(fresh).status == "COMPLETED"
    assert run.pose_revision == 2
    assert client.commands.count("forward 20") == client.commands.count("back 20") == 1
    run.close()


def test_settle_is_a_completed_delay_not_a_command_timeout():
    run, client, _ = executor()
    run.start(HOME)
    result = run.execute(plan(command("SETTLE", bound=0.02), command()))
    assert result.status == "COMPLETED"
    assert "forward 20" in client.commands
    run.close()


def test_land_node_completes_and_forbids_later_plan_nodes():
    run, client, _ = executor()
    run.start(HOME)
    result = run.execute(plan(command("LAND", bound=0.1), command()))
    assert result.status == "PREEMPTED"
    assert result.command_states[0].status == "COMPLETED"
    assert client.commands[-1] == "land" and "forward 20" not in client.commands
    run.close()


def test_failed_terminal_action_closes_resources_without_retry():
    class BadLand(Client):
        def land(self):
            super().land()
            raise OSError("secret")

    run, client, _ = executor(client=BadLand())
    run.start(HOME)
    result = run.land()
    assert result.state == "LANDING_FAILED" and "secret" not in repr(result)
    assert run.state is ExecutorState.CLOSED
    assert client.commands.count("land") == client.close_count == 1


def test_cleanup_failure_does_not_replace_sanitized_command_outcome():
    class BadClose(Client):
        def close(self):
            super().close()
            raise OSError("secret-token")

    def fail(command, cancellation):
        raise RuntimeError("secret-api-key")

    run, client, _ = executor(client=BadClose(), command_sink=fail)
    run.start(HOME)
    outcome = run.execute(plan(command()))
    assert outcome.status == "FAILED" and "secret" not in repr(outcome)
    assert client.close_count == 1


def test_watchdog_clock_failure_cancels_operation_and_fires_terminal_callback():
    entered, release, expired = Event(), Event(), Event()

    def clock():
        if entered.is_set():
            raise RuntimeError("secret-clock")
        return time.monotonic()

    def operation(cancellation):
        entered.set()
        release.wait(2)

    try:
        result = SupervisedWait(clock=clock).run(operation, time.monotonic() + 1, expired.set)
        assert result.status == "WATCHDOG_FAILED" and expired.is_set()
        assert "secret" not in repr(result)
    finally:
        release.set()


def test_completed_capture_and_telemetry_are_retained_in_outcome():
    run, client, _ = executor()
    run.start(HOME)
    outcome = run.execute(plan(command("CAPTURE"), command("READ_TELEMETRY")))
    assert outcome.status == "COMPLETED"
    assert outcome.observation_ids and outcome.telemetry.battery_percent == 100
    assert run.land().battery == 100
    run.close()


def test_runner_and_connected_api_cannot_own_separate_lifecycles():
    run, client, _ = executor()
    assert run.runner.step_executor is run
    with pytest.raises(RuntimeError, match="already owns"):
        StepExecutor(run.runner)
    run.start(HOME)
    run.close()
    assert client.commands.count("takeoff") == 1


def test_each_sdk_command_requires_its_full_bound_before_dispatch():
    now, first_completed = [0.0], Event()
    owner = current_thread()
    commands = []

    def clock():
        if current_thread() is owner and first_completed.is_set():
            now[0] = 1.5  # supervisor/bookkeeping elapsed between commands
        return now[0]

    def sink(command, cancellation):
        commands.append(command)
        now[0] = 0.9
        first_completed.set()
        return CommandOutcome()

    run, client, _ = executor(clock=clock, deadline=2.0, command_sink=sink)
    run.start(HOME)
    outcome = run.execute(plan(command(), command()))
    assert outcome.status == "DEADLINE_EXCEEDED"
    assert len(commands) == 1
    assert outcome.command_states[-1].status == "NOT_STARTED"
    run.close()


def test_clock_failure_after_command_still_closes_client_resources():
    broken = Event()

    def clock():
        if broken.is_set():
            raise RuntimeError("secret-clock")
        return time.monotonic()

    def sink(command, cancellation):
        broken.set()

    run, client, _ = executor(clock=clock, command_sink=sink)
    run.start(HOME)
    outcome = run.execute(plan(command()))
    assert outcome.status == "COMMAND_COMPLETION_UNKNOWN"
    run.close()
    assert run.state is ExecutorState.CLOSED and client.close_count == 1
    assert client.commands.count("land") == 1


def test_initial_watchdog_clock_failure_never_launches_operation():
    called, terminal = [], Event()

    def broken_clock():
        raise RuntimeError("secret-clock")

    outcome = SupervisedWait(clock=broken_clock).run(
        lambda cancelled: called.append(True), 10.0, terminal.set,
    )
    assert outcome.status == "WATCHDOG_FAILED" and terminal.is_set() and not called


def test_sink_rejection_cannot_be_reported_as_success_or_leak_its_detail():
    run, client, _ = executor(command_sink=lambda command, cancel: CommandOutcome(False, "secret"))
    run.start(HOME)
    outcome = run.execute(plan(command()))
    assert outcome.status == "FAILED" and "secret" not in repr(outcome)
    assert outcome.command_states[0].error_category == "COMMAND_REJECTED"
    assert client.commands[-1] == "land" and client.close_count == 1


def test_operator_emergency_latches_executor_before_another_checkpoint():
    run, client, _ = executor()
    run.start(HOME)
    run.runner.trigger_emergency()
    assert run.state is ExecutorState.TERMINAL
    with pytest.raises(RuntimeError, match="AIRBORNE"):
        run.execute(plan(command()))
    assert run.land().state == "EMERGENCY_ABORTED"
    run.close()
    assert client.commands[-1] == "emergency" and "land" not in client.commands


def test_vertical_plan_reads_measured_height_only_at_its_telemetry_node():
    class Measured(Client):
        def __init__(self):
            super().__init__()
            self.height_queries = 0

        def takeoff(self):
            result = super().takeoff()
            self._height = 200
            return result

        def height_cm(self):
            self.height_queries += 1
            return super().height_cm()

        def move(self, direction, distance_cm):
            result = super().move(direction, distance_cm)
            self._height += 2
            return result

    run, client, _ = executor(client=Measured())
    run.start(HOME)
    outcome = run.execute(plan(command(direction="up", distance_cm=20),
                               command("READ_TELEMETRY", phase="vertical")))
    assert outcome.status == "COMPLETED" and outcome.pose.z_cm == 222
    assert client.height_queries == 2  # preflight plus the declared read node
    run.close()


def test_planned_float_yaw_is_sent_as_an_sdk_integer():
    run, client, _ = executor()
    run.start(HOME)
    result = run.execute(plan(command("ROTATE", degrees=90.0, direction="cw")))
    assert result.status == "COMPLETED" and "cw 90" in client.commands
    run.close()


def test_failed_land_node_has_a_failed_command_record():
    class RejectedLand(Client):
        def land(self):
            super().land()
            return "error"

    run, client, _ = executor(client=RejectedLand())
    run.start(HOME)
    result = run.execute(plan(command("LAND")))
    assert result.status == "FAILED"
    assert result.command_states[0].status == "FAILED"
    assert result.command_states[0].error_category == "LANDING_FAILED"
    assert client.close_count == 1


def wait_for_state(run, state):
    deadline = time.monotonic() + 1
    while run.state is not state and time.monotonic() < deadline:
        time.sleep(0.001)
    assert run.state is state


def test_injected_sink_cannot_bypass_land_latch_or_send_following_move():
    dispatched = []
    run, client, _ = executor()

    def sink(node, cancellation):
        dispatched.append(node.kind)
        if node.kind == "LAND":
            run.runner._call(client.land)
        elif node.kind == "MOVE":
            run.runner.move_checked("forward", 20)
        return CommandOutcome()

    run.command_sink = sink
    run.start(HOME)
    outcome = run.execute(plan(command("LAND"), command()))
    assert outcome.status == "PREEMPTED"
    assert outcome.command_states[0].status == "COMPLETED"
    assert run.state is ExecutorState.TERMINAL and "MOVE" not in dispatched
    run.close()
    assert client.commands.count("land") == 1
    assert "forward 20" not in client.commands


def test_blocked_capture_result_is_discarded_after_concurrent_land():
    entered, release = Event(), Event()

    def capture(node, cancellation):
        entered.set()
        assert release.wait(2)
        return "late-observation"

    run, client, _ = executor(command_sink=capture)
    run.start(HOME)
    worker, outcomes = threaded(lambda: run.execute(plan(command("CAPTURE", bound=2))))
    assert entered.wait(1)
    landing, terminals = threaded(run.land)
    try:
        wait_for_state(run, ExecutorState.TERMINAL)
    finally:
        release.set()
    worker.join(1)
    landing.join(1)
    assert outcomes[0].status == "PREEMPTED"
    assert outcomes[0].observation_ids == ()
    assert outcomes[0].command_states[0].status == "PREEMPTED"
    assert terminals[0].state == "LANDED"
    run.close()
    assert client.commands.count("land") == 1


def test_localise_result_is_discarded_after_return_home_preempts_it():
    entered, release = Event(), Event()
    late_pose = Position(40, 40, 220)

    def localise(node, cancellation):
        entered.set()
        assert release.wait(2)
        return late_pose

    run, client, measured = executor(command_sink=localise)
    run.start(HOME)
    worker, outcomes = threaded(lambda: run.execute(plan(command("LOCALISE", bound=2))))
    assert entered.wait(1)
    returning, returns = threaded(lambda: run.return_home(plan(start=measured.pose)))
    try:
        wait_for_state(run, ExecutorState.RETURNING)
    finally:
        release.set()
    worker.join(1)
    returning.join(1)
    assert outcomes[0].status == "PREEMPTED"
    assert outcomes[0].pose == measured.pose
    assert run.runner.envelope.position == measured.pose
    assert returns[0].status == "COMPLETED"
    run.close()


@pytest.mark.parametrize("kind,value", [
    ("CAPTURE", "late-observation"),
    ("LOCALISE", Position(40, 40, 220)),
    ("READ_TELEMETRY", Telemetry(80, 220)),
])
def test_completed_provider_result_cannot_publish_after_emergency_wins_race(kind, value):
    run, client, measured = executor(command_sink=lambda node, cancellation: value)
    run.start(HOME)
    wait = run._wait.run

    def preempt_after_worker_completed(*args, **kwargs):
        result = wait(*args, **kwargs)
        assert result.status == "COMPLETED"
        run.runner.trigger_emergency()
        return result

    run._wait.run = preempt_after_worker_completed
    outcome = run.execute(plan(command(kind)))
    assert outcome.status == "PREEMPTED" and outcome.completion_known
    assert outcome.command_states[0].status == "PREEMPTED"
    assert outcome.observation_ids == () and outcome.telemetry is None
    assert outcome.pose == run.runner.envelope.position == measured.pose
    run.close()


def test_known_native_movement_completion_updates_pose_after_landing_preempts_it():
    entered, release = Event(), Event()

    class Blocking(Client):
        def move(self, direction, distance_cm):
            entered.set()
            assert release.wait(2)
            return super().move(direction, distance_cm)

    run, client, measured = executor(client=Blocking())
    run.start(HOME)
    worker, outcomes = threaded(lambda: run.execute(plan(command(bound=2))))
    assert entered.wait(1)
    landing, _ = threaded(run.land)
    try:
        wait_for_state(run, ExecutorState.TERMINAL)
    finally:
        release.set()
    worker.join(1)
    landing.join(1)
    assert outcomes[0].status == "PREEMPTED"
    assert outcomes[0].completion_known
    assert outcomes[0].pose == run.runner.envelope.position == measured.pose.moved("forward", 20)
    assert run.pose_revision == 1
    run.close()


def test_preempted_provider_deadline_cannot_cancel_the_return_that_replaced_it():
    entered, release = Event(), Event()

    def provider(node, cancellation):
        entered.set()
        release.wait(2)
        return Position(40, 40, 220)

    run, client, measured = executor(command_sink=provider)
    run.start(HOME)
    worker, outcomes = threaded(lambda: run.execute(plan(command("LOCALISE", bound=0.1))))
    assert entered.wait(1)
    returning, returns = threaded(lambda: run.return_home(plan(start=measured.pose)))
    try:
        wait_for_state(run, ExecutorState.RETURNING)
        worker.join(1)
        returning.join(1)
        assert outcomes[0].status == "PREEMPTED" and not outcomes[0].completion_known
        assert run.state is ExecutorState.RETURNING and returns[0].status == "COMPLETED"
        assert run.runner.envelope.position == measured.pose and "land" not in client.commands
    finally:
        release.set()
        run.close()


def test_sdk_deadline_globally_blocks_queued_return_after_generation_changed():
    entered, release = Event(), Event()

    class Blocking(Client):
        def move(self, direction, distance_cm):
            if direction == "forward":
                entered.set()
                assert release.wait(2)
            return super().move(direction, distance_cm)

    run, client, _ = executor(client=Blocking())
    run.start(HOME)
    worker, outcomes = threaded(lambda: run.execute(plan(command(bound=0.1))))
    assert entered.wait(1)

    def request_return():
        try:
            return run.return_home(plan(command(direction="back", distance_cm=20)))
        except Exception as error:
            return error

    returning, returns = threaded(request_return)
    try:
        wait_for_state(run, ExecutorState.RETURNING)
        worker.join(1)
        assert not worker.is_alive()
    finally:
        release.set()
        returning.join(1)
        run.close()
    assert outcomes[0].status == "COMMAND_COMPLETION_UNKNOWN"
    assert outcomes[0].command_states[0].status == "COMMAND_COMPLETION_UNKNOWN"
    assert not outcomes[0].completion_known
    assert not isinstance(returns[0], Exception)
    assert returns[0].status == "COMMAND_COMPLETION_UNKNOWN" and not returns[0].completion_known
    assert "back 20" not in client.commands
    assert client.commands.count("forward 20") == client.commands.count("land") == 1
    assert run.state is ExecutorState.CLOSED
    assert run.land().reason == "COMMAND_COMPLETION_UNKNOWN"


@pytest.mark.parametrize("preempt", ["land", "return", "close"])
@pytest.mark.parametrize("cooperative", [True, False])
def test_preemption_signals_interruptible_wait_and_finishes_before_provider_reply(preempt, cooperative):
    entered, release, finished = Event(), Event(), Event()
    tokens = []

    def provider(node, cancellation):
        tokens.append(cancellation)
        entered.set()
        if cooperative:
            cancellation.wait(2)
        else:
            release.wait(2)
        finished.set()
        return "late-observation"

    run, client, measured = executor(command_sink=provider)
    run.start(HOME)
    worker, outcomes = threaded(lambda: run.execute(plan(command("CAPTURE", bound=30))))
    assert entered.wait(1)
    action = {"land": run.land, "close": run.close,
              "return": lambda: run.return_home(plan(start=measured.pose))}[preempt]
    terminal, results = threaded(action)
    try:
        assert tokens[0].wait(0.3), "preemption must signal cancellation before waiting for operation lock"
        worker.join(0.3)
        terminal.join(0.3)
        assert not worker.is_alive() and not terminal.is_alive()
        assert outcomes[0].status == "PREEMPTED" and outcomes[0].observation_ids == ()
        if not cooperative:
            assert not finished.is_set()
        if preempt in {"land", "close"}:
            assert client.commands[-1] == "land"
            if preempt == "land":
                assert results[0].state == "LANDED"
            else:
                assert run.state is ExecutorState.CLOSED
        else:
            assert results[0].status == "COMPLETED" and run.state is ExecutorState.RETURNING
    finally:
        tokens[0].set()
        release.set()
        assert finished.wait(1)
        worker.join(1)
        terminal.join(1)
        run.close()


@pytest.mark.parametrize("kind,value", [
    ("CAPTURE", Position(10, 10, 200)),
    ("CAPTURE", Telemetry(80, 200)),
    ("CAPTURE", {"observation_ids": ["fake"]}),
    ("CAPTURE", ""),
    ("CAPTURE", "not an observation id"),
    ("LOCALISE", "arbitrary"),
    ("LOCALISE", Position(1000, 0, 200)),
    ("LOCALISE", Position(0, 0, -1)),
    ("LOCALISE", Position(float("nan"), 0, 200)),
    ("LOCALISE", Position(0, float("inf"), 200)),
    ("LOCALISE", Position(True, 0, 200)),
    ("READ_TELEMETRY", Position(0, 0, 200)),
    ("READ_TELEMETRY", Telemetry(True, 200)),
    ("READ_TELEMETRY", Telemetry(101, 200)),
    ("READ_TELEMETRY", Telemetry(80, -1)),
    ("READ_TELEMETRY", Telemetry(80, float("nan"))),
    ("READ_TELEMETRY", Telemetry(80, 200, -1)),
    ("READ_TELEMETRY", Telemetry(80, 200, 0, float("inf"))),
    ("MOVE", "ok"),
    ("MOVE", Telemetry(80, 200)),
    ("MOVE", Position(1000, 0, 200)),
    ("ROTATE", Position(0, 0, 200)),
    ("ROTATE", CommandOutcome("yes")),
])
def test_sink_results_are_validated_by_command_before_safety_state_mutation(kind, value):
    run, client, measured = executor(command_sink=lambda node, cancel: value)
    run.start(HOME)
    node = command() if kind == "MOVE" else command(kind)
    outcome = run.execute(plan(node))
    assert outcome.status == "FAILED"
    assert outcome.command_states[0].error_category == "INVALID_COMMAND_RESULT"
    assert outcome.pose == run.runner.envelope.position == measured.pose
    assert outcome.telemetry is None and outcome.observation_ids == ()
    assert client.commands[-1] == "land" and client.close_count == 1


def test_return_with_stale_start_requires_replan_before_any_movement():
    run, client, measured = executor()
    run.start(HOME)
    outcome = run.return_home(plan(command(direction="back", distance_cm=20), start=Position(40, 40, 200)))
    assert outcome.status == "REPLAN_REQUIRED" and outcome.pose == measured.pose
    assert outcome.command_states[0].status == "NOT_STARTED"
    assert outcome.command_states[0].error_category == "STALE_RETURN_PLAN"
    assert "back 20" not in client.commands and run.state is ExecutorState.RETURNING
    run.close()


def test_return_preemption_before_sdk_dispatch_does_not_invent_a_movement_result():
    entered, release = Event(), Event()
    run, client, measured = executor()
    run.start(HOME)
    wait = run._wait.run

    def delayed_wait(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return wait(*args, **kwargs)

    run._wait.run = delayed_wait
    worker, outcomes = threaded(lambda: run.execute(plan(command(bound=2))))
    assert entered.wait(1)
    returning, returns = threaded(lambda: run.return_home(plan(start=measured.pose)))
    try:
        wait_for_state(run, ExecutorState.RETURNING)
    finally:
        release.set()
    worker.join(1)
    returning.join(1)
    assert outcomes[0].status == "PREEMPTED" and returns[0].status == "COMPLETED"
    assert run.pose_revision == 0 and run.runner.envelope.position == measured.pose
    assert "forward 20" not in client.commands and "land" not in client.commands
    run.close()
