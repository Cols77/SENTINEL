"""One connected lifecycle, shared by checkpoint sessions and legacy runners.

The runner supplies the existing preflight, envelope and emergency policy. A
room adapter may inject measured-home and command callbacks; this module does
not depend on room types. All ordinary client calls use ``command_lock``.
"""

from __future__ import annotations

import math
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from enum import StrEnum
from threading import Event, RLock, Thread, local
from typing import Callable

from sentinel.client import EmergencyAborted
from sentinel.cube.checkpoint import CheckpointPlan, CommandOutcome, HomeDefinition, PlannedCommand
from sentinel.cube.supervised_wait import SupervisedWait, WaitOutcome
from sentinel.models import Position, Telemetry


class ExecutorState(StrEnum):
    NEW = "NEW"
    AIRBORNE = "AIRBORNE"
    RETURNING = "RETURNING"
    TERMINAL = "TERMINAL"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class HomeState:
    pose: Position
    heading_deg: float
    route_id: str
    measured_at: float


@dataclass(frozen=True, slots=True)
class CommandState:
    command: PlannedCommand
    status: str
    started_at: float
    completed_at: float | None
    error_category: str | None = None


@dataclass(frozen=True, slots=True)
class CheckpointOutcome:
    status: str
    command_states: tuple[CommandState, ...]
    observation_ids: tuple[str, ...]
    telemetry: Telemetry | None
    pose: Position | None
    completion_known: bool


@dataclass(frozen=True, slots=True)
class TerminalOutcome:
    state: str
    reason: str
    pose: Position | None
    battery: int | None
    completed_at: float | None


class StepExecutor:
    """Compose with a FlightTestRunner (or its CubeMissionRunner subclass).

    ``measure_home`` returns actual takeoff measurement and provenance, never a
    nominal pad estimate. ``deadline`` uses the injected monotonic clock. A
    command sink receives (immutable command, cancellation Event), must return
    observations rather than publish them, and must use the runner's client
    call boundary for effects. Native SDK operations hold the command lock
    until their real call returns, including after a deadline has elapsed.
    """

    def __init__(self, runner, *,
                 measure_home: Callable[[HomeDefinition], HomeState] | None = None,
                 command_sink: Callable[[PlannedCommand, Event], object] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 deadline: float | None = None,
                 home_tolerance_cm: float = 20.0,
                 home_heading_tolerance_deg: float = 15.0) -> None:
        self.runner, self.client = runner, runner.client
        self.measure_home, self.command_sink = measure_home, command_sink
        self.clock, self.deadline = clock, deadline
        if deadline is not None and not math.isfinite(deadline):
            raise ValueError("deadline must be finite")
        if any(not math.isfinite(v) or v < 0 for v in
               (home_tolerance_cm, home_heading_tolerance_deg)):
            raise ValueError("home tolerances must be finite and nonnegative")
        self.home_tolerance_cm = home_tolerance_cm
        self.home_heading_tolerance_deg = home_heading_tolerance_deg
        self.command_lock = RLock()
        self._operation_lock, self._state_lock = RLock(), RLock()
        self._terminal_action_lock = RLock()
        self._context = local()
        self._state = ExecutorState.NEW
        self._generation = 0
        self.home: HomeState | None = None
        self._home_definition: HomeDefinition | None = None
        self._may_be_airborne = False
        self._land_attempted = self._close_attempted = self._cleanup_started = False
        self._terminal: TerminalOutcome | None = None
        self._terminal_reason = "REQUESTED"
        self._command_completion_unknown = False
        self._telemetry: Telemetry | None = None
        self._wait = SupervisedWait(clock=clock)
        with runner._emergency_lock:
            if runner._step_executor is not None:
                raise RuntimeError("runner already owns a step executor")
            runner._step_executor = self

    @property
    def state(self) -> ExecutorState:
        with self._state_lock:
            return self._state

    def _transition_locked(self, state: ExecutorState, *, invalidate: bool = False) -> None:
        """Change lifecycle and invalidate pending publications under _state_lock."""
        if self._state is not state or invalidate:
            self._generation += 1
            self._state = state

    @contextmanager
    def client_call(self):
        """The one lock used by both ordinary and legacy client calls."""
        with self.command_lock:
            with self._state_lock:
                if (self._state in {ExecutorState.TERMINAL, ExecutorState.CLOSED}
                        and not getattr(self._context, "terminal", False)):
                    raise RuntimeError("executor is terminal")
            yield

    def _emergency(self) -> bool:
        return (self.runner.emergency_triggered
                or bool(getattr(self.client, "emergency_latched", False))
                or bool(getattr(self.client, "emergency_triggered", False)))

    def note_emergency(self) -> None:
        """Latch the existing operator emergency path without taking the SDK lock."""
        with self._state_lock:
            if self._state is not ExecutorState.CLOSED:
                self._transition_locked(ExecutorState.TERMINAL, invalidate=True)
                self._terminal_reason = "OPERATOR_EMERGENCY"

    def start(self, home_definition: HomeDefinition) -> HomeState:
        if self.measure_home is None:
            raise ValueError("start requires a measured home source")
        self._start(home_definition)
        assert self.home is not None
        return self.home

    def start_legacy(self, *, video_only: bool = False) -> None:
        """Same lifecycle; the historical flight hook owns altitude/localisation."""
        self._start(None, video_only=video_only)

    def _start(self, definition: HomeDefinition | None, *, video_only: bool = False) -> None:
        with self._operation_lock:
            with self._state_lock:
                if self._state is ExecutorState.AIRBORNE and definition == self._home_definition:
                    return
                if self._state is not ExecutorState.NEW:
                    raise RuntimeError("executor must be NEW to start")
            try:
                self.runner._preflight()
                if video_only:
                    return
                self.runner._emit("preflight", "preflight passed; starting flight sequence")
                self.runner._assert_route_unchanged()
                self.runner._emit("flight", "sending takeoff")
                with self._state_lock:
                    if self._state is not ExecutorState.NEW:
                        raise RuntimeError("executor became terminal before takeoff")
                    self._may_be_airborne = True
                response = self.runner._call(self.client.takeoff)
                if not self.runner._is_ok(response):
                    raise RuntimeError(f"takeoff failed: {response}")
                if self._emergency():
                    raise EmergencyAborted("emergency stop triggered during takeoff")
                if definition is not None:
                    self.home = self.measure_home(definition)
                    self._validate_home(definition)
                    self.runner.envelope.position = self.home.pose
                self._home_definition = definition
                with self._state_lock:
                    if self._state is not ExecutorState.NEW:
                        raise RuntimeError("executor became terminal during start")
                    self._transition_locked(ExecutorState.AIRBORNE)
            except BaseException:
                self._cleanup()
                raise

    def _validate_home(self, definition: HomeDefinition) -> None:
        measured = self.home
        if not isinstance(measured, HomeState):
            raise ValueError("invalid measured home")
        values = (measured.pose.x_cm, measured.pose.y_cm, measured.pose.z_cm,
                  measured.heading_deg, measured.measured_at)
        if (any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                for v in values) or measured.measured_at < 0
                or measured.route_id != definition.route_id
                or math.dist((measured.pose.x_cm, measured.pose.y_cm, measured.pose.z_cm),
                             (definition.nominal_pose.x_cm, definition.nominal_pose.y_cm,
                              definition.nominal_pose.z_cm)) > self.home_tolerance_cm
                or abs((measured.heading_deg - definition.heading_deg + 180) % 360 - 180)
                > self.home_heading_tolerance_deg):
            raise ValueError("measured home disagrees with static home definition")
        self.runner.envelope.validate_position(measured.pose)

    def execute(self, plan: CheckpointPlan) -> CheckpointOutcome:
        return self._execute(plan, ExecutorState.AIRBORNE)

    def return_home(self, plan: CheckpointPlan) -> CheckpointOutcome:
        with self._state_lock:
            if self._state not in {ExecutorState.AIRBORNE, ExecutorState.RETURNING}:
                raise RuntimeError("return_home requires AIRBORNE or RETURNING")
            self._transition_locked(ExecutorState.RETURNING, invalidate=True)
        return self._execute(plan, ExecutorState.RETURNING, admitted=True)

    def execute_legacy(self, plan: CheckpointPlan, sink: Callable[[PlannedCommand], object]) -> None:
        """Preserve historical exception/log semantics through the same iterator."""
        self._execute(plan, ExecutorState.AIRBORNE, legacy_sink=sink)

    def _outcome(self, status, states, observations=(), *, known=True):
        with self._state_lock:
            return CheckpointOutcome(status, tuple(states), tuple(observations), self._telemetry,
                                     self.runner.envelope.position, known)

    def _preempted_outcome(self, states=(), observations=(), *, known=True):
        with self._state_lock:
            if self._command_completion_unknown:
                return self._outcome("COMMAND_COMPLETION_UNKNOWN", states, observations, known=False)
            return self._outcome("PREEMPTED", states, observations, known=known)

    def _execute(self, plan, required_state, *, legacy_sink=None, admitted=False):
        # Reject terminal work before waiting behind a pending SDK command's
        # cleanup. Otherwise the supervisor itself could block on that reply.
        if self.state is not required_state:
            if admitted:
                return self._preempted_outcome()
            raise RuntimeError(f"execute requires {required_state.value}")
        with self._operation_lock:
            if self.state is not required_state:
                return self._preempted_outcome()
            states, observations = [], []
            try:
                if self.deadline is not None and self.clock() + plan.worst_case_s > self.deadline:
                    if plan.commands:
                        states.append(CommandState(plan.commands[0], "NOT_STARTED", self.clock(), None,
                                                   "DEADLINE_EXCEEDED"))
                    self._terminal_reason = "DEADLINE_EXCEEDED"
                    self._cleanup()
                    return self._outcome("DEADLINE_EXCEEDED", states)
                for command in plan.commands:
                    with self._state_lock:
                        if self._state is not required_state:
                            return self._preempted_outcome(states, observations)
                        publication = [self._generation, required_state]
                    started = self.clock()
                    if self.deadline is not None and started + command.upper_bound_s > self.deadline:
                        states.append(CommandState(command, "NOT_STARTED", started, None,
                                                   "DEADLINE_EXCEEDED"))
                        self._terminal_reason = "DEADLINE_EXCEEDED"
                        self._cleanup()
                        return self._outcome("DEADLINE_EXCEEDED", states, observations)
                    deadline = started + command.upper_bound_s
                    if self.deadline is not None:
                        deadline = min(deadline, self.deadline)
                    deadline_owned = Event()

                    def operation(cancellation, command=command, publication=publication):
                        # A blocked perception/provider must not retain the SDK
                        # movement lock. Its result is published only by run().
                        context = (self.client_call() if not command.interruptible or command.kind == "LAND"
                                   else nullcontext())
                        with context:
                            return self._dispatch_current(command, cancellation, publication)

                    def on_deadline(publication=publication, owned=deadline_owned, command=command):
                        with self._state_lock:
                            # Provider results expire with their generation.
                            # An unresolved SDK call remains a global movement
                            # hazard even after return/terminal preemption.
                            if not command.interruptible or self._current_locked(publication):
                                owned.set()
                                self._deadline_expired(completion_unknown=not command.interruptible)

                    # A native SETTLE *is* a timed delay: its own timer ends it
                    # successfully. The session deadline still preempts it.
                    settle_timer = (command.kind == "SETTLE" and self.command_sink is None
                                    and (self.deadline is None or deadline < self.deadline))
                    if legacy_sink is not None:
                        with self.client_call():
                            value = self._dispatch_current(command, Event(), publication, legacy_sink)
                        result = WaitOutcome("COMPLETED", value, started, self.clock(), True)
                    elif command.upper_bound_s == 0 and command.kind in {"WATCHDOG", "TRIM_ALLOWANCE"}:
                        result = WaitOutcome("COMPLETED", None, started, started, True)
                    else:
                        result = self._wait.run(
                            operation, deadline, (lambda: None) if settle_timer else on_deadline,
                        )
                    outcome, cleanup = self._publish_result(
                        command, result, publication, states, observations, started,
                        settle_timer=settle_timer, deadline_owned=deadline_owned.is_set(),
                    )
                    if cleanup:
                        self._cleanup()
                    if outcome is not None:
                        return outcome
                return self._outcome("COMPLETED", states, observations)
            except BaseException:
                self._cleanup()
                raise

    def _current_locked(self, publication) -> bool:
        return self._generation == publication[0] and self._state is publication[1]

    def _dispatch_current(self, command, cancellation, publication, legacy_sink=None):
        with self._state_lock:
            if cancellation.is_set() or not self._current_locked(publication):
                return None
            if command.kind == "LAND":
                # LAND is always executor-owned; an injected sink cannot bypass
                # the terminal latch or create an untracked landing attempt.
                self._transition_locked(ExecutorState.TERMINAL)
                publication[:] = [self._generation, ExecutorState.TERMINAL]
        if command.kind == "LAND":
            return self._land_locked()
        if legacy_sink is not None:
            return legacy_sink(command)
        return self._dispatch(command, cancellation)

    def _publish_result(self, command, result, publication, states, observations, started, *,
                        settle_timer: bool, deadline_owned: bool):
        # This lock covers validation AND publication, so a terminal/return
        # latch cannot slip between the check and a pose/telemetry/result write.
        with self._state_lock:
            if not self._current_locked(publication) and not deadline_owned:
                states.append(CommandState(command, "PREEMPTED", started,
                                           result.completed_at if result.completion_known else None,
                                           "LIFECYCLE_PREEMPTED"))
                return self._preempted_outcome(states, observations,
                                               known=result.completion_known), False
            if settle_timer and result.status == "DEADLINE_EXCEEDED":
                states.append(CommandState(command, "COMPLETED", started, result.completed_at))
                return None, False
            if result.status != "COMPLETED":
                unknown = not command.interruptible and not result.completion_known
                status = "COMMAND_COMPLETION_UNKNOWN" if unknown else result.status
                states.append(CommandState(command, status, started,
                                           None if unknown else result.completed_at,
                                           result.error_category or "DEADLINE_EXCEEDED"))
                return self._outcome(status, states, observations, known=not unknown), result.status == "FAILED"
            failure = None
            if isinstance(result.value, CommandOutcome) and not result.value.completed:
                failure = "COMMAND_REJECTED"
            elif isinstance(result.value, TerminalOutcome) and result.value.state == "LANDING_FAILED":
                failure = "LANDING_FAILED"
            if failure is not None:
                states.append(CommandState(command, "FAILED", started, result.completed_at, failure))
                self._terminal_reason = failure
                return self._outcome("FAILED", states, observations), True
            states.append(CommandState(command, "COMPLETED", started, result.completed_at))
            if isinstance(result.value, Telemetry):
                self._telemetry = result.value
            elif isinstance(result.value, Position):
                self.runner.envelope.position = result.value
                if self._telemetry is not None:
                    self._telemetry = replace(self._telemetry, height_cm=result.value.z_cm)
            elif command.kind == "CAPTURE" and isinstance(result.value, str):
                observations.append(result.value)
            return None, False

    def _dispatch(self, command: PlannedCommand, cancellation: Event):
        if self.command_sink is not None:
            return self.command_sink(command, cancellation)
        args, call = dict(command.arguments), self.runner._call
        if command.kind == "MOVE":
            return self.runner.move_checked(args["direction"], args["distance_cm"],
                                            validate_vertical_telemetry=False, commit_position=False)
        elif command.kind == "ROTATE":
            response = call(lambda: self.client.rotate(args["direction"], round(args["degrees"])))
            if not self.runner._is_ok(response):
                raise RuntimeError("rotation failed")
        elif command.kind == "SET_SPEED":
            response = call(lambda: self.client.set_speed(args["speed_cm_s"]))
            if not self.runner._is_ok(response):
                raise RuntimeError("speed command failed")
        elif command.kind == "READ_TELEMETRY":
            if args.get("phase") == "vertical":
                height = self.runner._require_height(call(self.client.height_cm))
                measured = replace(self.runner.envelope.position, z_cm=height)
                return self.runner.envelope.validate_position(measured)
            return Telemetry(call(self.client.battery), self.runner.envelope.position.z_cm)
        elif command.kind == "CAPTURE":
            # Decoded video reads use the separate video transport and do not
            # issue SDK movement. A stalled frame source cannot delay landing.
            frame = self.runner._call_locked(lambda: self.client.read_frame(timeout=command.upper_bound_s))
            return f"frame-{frame.frame_number}"
        elif command.kind == "SETTLE":
            cancellation.wait(command.upper_bound_s)
        elif command.kind not in {"WATCHDOG", "TRIM_ALLOWANCE"}:
            raise ValueError("command requires an injected execution sink")

    def _deadline_expired(self, *, completion_unknown: bool = False) -> None:
        with self._state_lock:
            if self._state is ExecutorState.CLOSED:
                return
            self._command_completion_unknown |= completion_unknown
            self._transition_locked(ExecutorState.TERMINAL, invalidate=True)
            self._terminal_reason = ("COMMAND_COMPLETION_UNKNOWN" if self._command_completion_unknown
                                     else "DEADLINE_EXCEEDED")
            if self._cleanup_started:
                return
            self._cleanup_started = True
        # The watcher never waits on the SDK lock. Landing queues behind the
        # unresolved command; firmware-unvalidated concurrent movement is not
        # a cancellation mechanism. The caller remains free to supervise.
        Thread(target=self._cleanup, name="checkpoint-terminal", daemon=True).start()

    def land(self) -> TerminalOutcome:
        with self._state_lock:
            if self._terminal is not None:
                return self._terminal
            if self._state is not ExecutorState.CLOSED:
                self._transition_locked(ExecutorState.TERMINAL)
        with self._operation_lock:
            result = self._land_locked()
            if result.state == "LANDING_FAILED":
                try:
                    self.close()
                except BaseException:
                    pass
            return result

    def _land_locked(self) -> TerminalOutcome:
        with self.command_lock, self._terminal_action_lock:
            if self._terminal is not None:
                return self._terminal
            state = "NOT_AIRBORNE"
            if self._emergency():
                state = "EMERGENCY_ABORTED"
            elif self._may_be_airborne and not self._land_attempted:
                self._land_attempted = True
                self._context.terminal = True
                try:
                    response = self.runner._call(self.client.land)
                    state = "LANDED" if self.runner._is_ok(response) else "LANDING_FAILED"
                except BaseException:
                    state = "EMERGENCY_ABORTED" if self._emergency() else "LANDING_FAILED"
                finally:
                    self._context.terminal = False
            self._may_be_airborne = False
            self._terminal = self._terminal_outcome(state)
            return self._terminal

    def _terminal_outcome(self, state: str) -> TerminalOutcome:
        try:
            completed_at = self.clock()
            if not math.isfinite(completed_at):
                completed_at = None
        except BaseException:
            completed_at = None
        return TerminalOutcome(
            state, self._terminal_reason, self.runner.envelope.position,
            self._telemetry.battery_percent if self._telemetry else None, completed_at,
        )

    def land_during_command(self, validated_action: Callable[[], str]) -> TerminalOutcome:
        """Bookkeep the legacy, explicitly enabled firmware landing hook once.

        Only the existing land_during_move policy selects this path. Normal
        deadlines never assume the firmware supports concurrent landing. A
        fire-and-forget ``sent`` reply is not evidence of completed landing.
        """
        with self._terminal_action_lock:
            if self._terminal is not None:
                return self._terminal
            with self._state_lock:
                self._transition_locked(ExecutorState.TERMINAL, invalidate=True)
                self._terminal_reason = "CONTAINMENT_ABORT"
                self._land_attempted = True
            state = "LANDING_FAILED"
            try:
                if self._emergency():
                    state = "EMERGENCY_ABORTED"
                else:
                    response = validated_action()
                    state = "LANDED" if self.runner._is_ok(response) else "COMMAND_COMPLETION_UNKNOWN"
            finally:
                self._may_be_airborne = False
                self._terminal = self._terminal_outcome(state)
            return self._terminal

    def _cleanup(self) -> None:
        try:
            self.land()
        except BaseException:
            pass
        finally:
            try:
                self.close()
            except BaseException:
                # Preserve the primary, sanitized operation outcome. close()
                # latches CLOSED even if the backend's resource release fails.
                pass

    def close(self) -> None:
        with self._operation_lock:
            self.land()
            with self.command_lock:
                if self._close_attempted:
                    return
                self._close_attempted = True
                try:
                    self.client.close()
                finally:
                    with self._state_lock:
                        self._transition_locked(ExecutorState.CLOSED)
