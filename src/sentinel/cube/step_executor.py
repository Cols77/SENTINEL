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
from sentinel.cube.localization import LocalizationState, PoseHealth
from sentinel.cube.supervised_wait import SupervisedWait, WaitOutcome
from sentinel.models import Position, Telemetry


_NOT_DISPATCHED = object()


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


class HomeMeasurementError(RuntimeError):
    """Sanitized start failure; a preempted callback cannot publish its result."""

    def __init__(self, status: str, *, completion_known: bool,
                 error_category: str | None = None) -> None:
        super().__init__(f"home measurement: {status}")
        self.status = status
        self.completion_known = completion_known
        self.error_category = error_category


class ReanchorPreempted(RuntimeError):
    """Evidence committed, but a newer lifecycle prevented pose publication."""

    persisted = True

    def __init__(self) -> None:
        super().__init__("reanchor publication preempted after persistence")


@dataclass(frozen=True, slots=True)
class CooperativeLegacySink:
    """Opt in to cancellation with all SDK effects through ``client_call``.

    The callback receives the command cancellation Event. It must check
    ``check_legacy_cancellation`` after provider waits and before publishing
    state. Ordinary SDK calls must run on the callback thread; nested calls
    are gated and tracked by the executor itself.
    Unwrapped legacy sinks retain conservative, noninterruptible semantics.
    """
    callback: Callable[[PlannedCommand, Event], object]


@dataclass
class _LegacyScope:
    cancellation: Event
    publication: list
    active_calls: int = 0
    completion_unknown: bool = False


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


@dataclass(frozen=True, slots=True)
class HeightTelemetry:
    """One declared vertical height read, without inventing a new battery sample."""
    height_cm: int


class StepExecutor:
    """Compose with a FlightTestRunner (or its CubeMissionRunner subclass).

    ``measure_home`` returns actual takeoff measurement and provenance, never a
    nominal pad estimate. It must return data without publishing state or
    issuing SDK commands. Its interruptible wait is bounded by
    ``home_measurement_timeout_s`` and the session deadline; start raises
    HomeMeasurementError on timeout, failure or lifecycle preemption. A late
    callback result is discarded. ``deadline`` uses the monotonic clock. A
    command sink receives (immutable command, cancellation Event), must return
    observations rather than publish them, and must use the runner's client
    call boundary for effects. Native SDK operations hold the command lock
    until their real call returns, including after a deadline has elapsed.

    Sink results: CAPTURE returns one nonempty observation-ID string; LOCALISE
    returns Position; READ_TELEMETRY returns Telemetry (or HeightTelemetry for
    a vertical read); MOVE returns Position or CommandOutcome; other effect
    nodes return CommandOutcome. LAND is executor-owned. Invalid payloads fail
    closed before publication. Only interruptible waits receive cancellation
    on lifecycle preemption; acknowledged SDK motion remains a physical fact.
    """

    def __init__(self, runner, *,
                 measure_home: Callable[[HomeDefinition], HomeState] | None = None,
                 command_sink: Callable[[PlannedCommand, Event], object] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 deadline: float | None = None,
                 home_measurement_timeout_s: float = 10.0,
                 home_tolerance_cm: float = 20.0,
                 home_heading_tolerance_deg: float = 15.0) -> None:
        self.runner, self.client = runner, runner.client
        self.measure_home, self.command_sink = measure_home, command_sink
        self.clock, self.deadline = clock, deadline
        if deadline is not None and not math.isfinite(deadline):
            raise ValueError("deadline must be finite")
        if (isinstance(home_measurement_timeout_s, bool)
                or not isinstance(home_measurement_timeout_s, (int, float))
                or not math.isfinite(home_measurement_timeout_s)
                or home_measurement_timeout_s <= 0):
            raise ValueError("home_measurement_timeout_s must be finite and positive")
        self.home_measurement_timeout_s = home_measurement_timeout_s
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
        self._pose_revision = 0
        self._active_cancellation: Event | None = None
        self.home: HomeState | None = None
        self._home_definition: HomeDefinition | None = None
        self._may_be_airborne = False
        self._takeoff_attempted = False
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

    @property
    def takeoff_attempted(self) -> bool:
        with self._state_lock:
            return self._takeoff_attempted

    @property
    def terminal_outcome(self) -> TerminalOutcome | None:
        with self._state_lock:
            return self._terminal

    @property
    def pose_revision(self) -> int:
        with self._state_lock:
            return self._pose_revision

    def _transition_locked(self, state: ExecutorState, *, invalidate: bool = False) -> None:
        """Change lifecycle and invalidate pending publications under _state_lock."""
        if self._state is not state or invalidate:
            if self._active_cancellation is not None:
                self._active_cancellation.set()
            self._generation += 1
            self._state = state

    @contextmanager
    def client_call(self):
        """The one lock used by both ordinary and legacy client calls."""
        with self.command_lock:
            with self._state_lock:
                self.check_legacy_cancellation()
                if (self._state in {ExecutorState.TERMINAL, ExecutorState.CLOSED}
                        and not getattr(self._context, "terminal", False)):
                    raise RuntimeError("executor is terminal")
                scope = getattr(self._context, "legacy_scope", None)
                if scope is not None:
                    scope.active_calls += 1
            try:
                yield
            finally:
                if scope is not None:
                    with self._state_lock:
                        scope.active_calls -= 1

    def check_legacy_cancellation(self) -> None:
        """Reject late provider effects, including after return preemption."""
        with self._state_lock:
            scope = getattr(self._context, "legacy_scope", None)
            if scope is not None and (scope.cancellation.is_set()
                                      or not self._current_locked(scope.publication)):
                raise RuntimeError("legacy command was cancelled")

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

    def note_controlled_land(self, acknowledged: bool) -> None:
        """Record an operator Land sent outside the executor's own SDK lock.

        Mission Control's controlled-Land path calls the client directly so a
        blocked move can still be interrupted; it does not take
        ``command_lock``. Without this hook the executor never learns the
        landing happened, so its own cleanup lands nowhere (``land`` is
        already latched) and its later ``_land_locked`` attempt races the
        already-sent command and records the terminal outcome as
        LANDING_FAILED even though the operator's Land was acknowledged. This
        makes the executor terminal immediately and, if nothing has already
        settled a terminal outcome (an emergency or another land already
        completed one), records LANDED on acknowledgement and LANDING_FAILED
        otherwise, so every reader of the shared terminal outcome (the room
        controller, the supervisor) sees the safe landing.
        """
        with self._state_lock:
            if self._state is ExecutorState.CLOSED:
                return
            self._land_attempted = True
            self._may_be_airborne = False
            self._transition_locked(ExecutorState.TERMINAL, invalidate=True)
            if self._terminal is None:
                self._terminal_reason = "OPERATOR_LAND"
                self._terminal = self._terminal_outcome("LANDED" if acknowledged else "LANDING_FAILED")

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
                    self._takeoff_attempted = True
                    self._may_be_airborne = True
                response = self.runner._call(self.client.takeoff)
                if not self.runner._is_ok(response):
                    raise RuntimeError(f"takeoff failed: {response}")
                if self._emergency():
                    raise EmergencyAborted("emergency stop triggered during takeoff")
                if definition is not None:
                    self._measure_and_publish_home(definition)
                else:
                    with self._state_lock:
                        if self._state is not ExecutorState.NEW:
                            raise RuntimeError("executor became terminal during start")
                        self._transition_locked(ExecutorState.AIRBORNE)
            except BaseException:
                self._cleanup()
                raise

    def _measure_and_publish_home(self, definition: HomeDefinition) -> None:
        cancellation = Event()
        with self._state_lock:
            if self._state is not ExecutorState.NEW:
                raise HomeMeasurementError("PREEMPTED", completion_known=True)
            generation = self._generation
            self._active_cancellation = cancellation
        expired_here = False

        def expire() -> None:
            nonlocal expired_here
            with self._state_lock:
                # An obsolete provider deadline must not replace a terminal
                # action that already owns the lifecycle.
                if self._generation == generation and self._state is ExecutorState.NEW:
                    expired_here = True
                    self._deadline_expired()

        try:
            try:
                deadline = self.clock() + self.home_measurement_timeout_s
                if not math.isfinite(deadline):
                    raise ValueError("invalid monotonic deadline")
                if self.deadline is not None:
                    deadline = min(deadline, self.deadline)
            except BaseException:
                expire()
                raise HomeMeasurementError("WATCHDOG_FAILED", completion_known=True,
                                           error_category="WATCHDOG_FAILED") from None
            result = self._wait.run(lambda cancel: self.measure_home(definition), deadline,
                                    expire, cancellation=cancellation)
            with self._state_lock:
                if expired_here:
                    raise HomeMeasurementError(result.status, completion_known=result.completion_known,
                                               error_category=result.error_category)
                if self._generation != generation or self._state is not ExecutorState.NEW:
                    raise HomeMeasurementError("PREEMPTED", completion_known=result.completion_known)
                if result.status != "COMPLETED":
                    raise HomeMeasurementError(result.status, completion_known=result.completion_known,
                                               error_category=result.error_category)
                # Validation, provenance and pose publication share the
                # lifecycle lock, so landing cannot accept a late home.
                self.home = result.value
                self._validate_home(definition)
                self.runner.envelope.position = self.home.pose
                self._home_definition = definition
                self._transition_locked(ExecutorState.AIRBORNE)
        finally:
            with self._state_lock:
                if self._active_cancellation is cancellation:
                    self._active_cancellation = None

    def _validate_home(self, definition: HomeDefinition) -> None:
        measured = self.home
        if not isinstance(measured, HomeState):
            raise ValueError("invalid measured home")
        values = (measured.pose.x_cm, measured.pose.y_cm, measured.pose.z_cm,
                  measured.heading_deg, measured.measured_at)
        if (any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                for v in values) or measured.measured_at < 0
                or measured.route_id != definition.route_id
                or math.dist((measured.pose.x_cm, measured.pose.y_cm),
                             (definition.nominal_pose.x_cm, definition.nominal_pose.y_cm)) > self.home_tolerance_cm
                or abs((measured.heading_deg - definition.heading_deg + 180) % 360 - 180)
                > self.home_heading_tolerance_deg):
            raise ValueError("measured home disagrees with static home definition")
        # The surveyed pad is a touchdown reference. After takeoff, altitude
        # belongs to the configured airborne envelope, not pad XYZ tolerance.
        self.runner.envelope.validate_position(measured.pose)

    def execute(self, plan: CheckpointPlan) -> CheckpointOutcome:
        return self._execute(plan, ExecutorState.AIRBORNE)

    def apply_reanchor(self, pose: PoseHealth | Position, *, persist: Callable[[], object]) -> None:
        """Commit trusted reanchor evidence before publishing its executor pose.

        The caller owns the ledger; cube owns validation, lifecycle exclusion,
        and invalidation. A failed durable append leaves executor state intact.
        Any failure after append latches terminal so the caller can escalate.
        """
        # Retain ordinary operation/SDK ownership, while letting the direct
        # emergency latch and motor-stop send acquire the short lifecycle lock.
        with self._operation_lock, self.command_lock:
            with self._state_lock:
                if self._state not in {ExecutorState.AIRBORNE, ExecutorState.RETURNING}:
                    raise RuntimeError("reanchor requires an airborne executor")
                position = pose.position if isinstance(pose, PoseHealth) else pose
                if not self._valid_pose(position):
                    raise ValueError("invalid reanchor position")
                if isinstance(pose, PoseHealth):
                    values = (pose.heading_deg, pose.measured_at, pose.camera_at, pose.propagated_at,
                              pose.camera_age_s, pose.fix_age_s, pose.look_away_s, pose.translation_cm,
                              pose.yaw_deg, pose.xy_uncertainty_cm, pose.heading_uncertainty_deg)
                    if (pose.state != LocalizationState.ANCHORED
                            or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values)
                            or any(type(v) is not int or v < 0
                                   for v in (pose.pose_revision, pose.estimator_failures))):
                        raise ValueError("invalid reanchor health")
                admitted = self._state, self._generation, self._pose_revision, self.runner.envelope.position
            persist()
            with self._state_lock:
                current = self._state, self._generation, self._pose_revision, self.runner.envelope.position
                if current != admitted or self._emergency():
                    if self._state not in {ExecutorState.TERMINAL, ExecutorState.CLOSED}:
                        self._terminal_reason = "REANCHOR_PREEMPTED"
                        self._transition_locked(ExecutorState.TERMINAL, invalidate=True)
                    raise ReanchorPreempted()
                try:
                    self._commit_pose_locked(position)
                    self._transition_locked(self._state, invalidate=True)
                except BaseException:
                    self._terminal_reason = "REANCHOR_SYNC_FAILED"
                    self._transition_locked(ExecutorState.TERMINAL, invalidate=True)
                    raise

    def return_home(self, plan: CheckpointPlan) -> CheckpointOutcome:
        """Latch return; a stale start/revision yields REPLAN_REQUIRED without motion."""
        with self._state_lock:
            if self._state not in {ExecutorState.AIRBORNE, ExecutorState.RETURNING}:
                raise RuntimeError("return_home requires AIRBORNE or RETURNING")
            self._transition_locked(ExecutorState.RETURNING, invalidate=True)
            revision = self._pose_revision
        return self._execute(plan, ExecutorState.RETURNING, admitted=True, return_revision=revision)

    def execute_legacy(self, plan: CheckpointPlan,
                       sink: Callable[[PlannedCommand], object] | CooperativeLegacySink) -> CheckpointOutcome:
        """Preserve historical exception/log semantics through the same iterator."""
        return self._execute(plan, ExecutorState.AIRBORNE, legacy_sink=sink)

    def _outcome(self, status, states, observations=(), *, known=True):
        with self._state_lock:
            return CheckpointOutcome(status, tuple(states), tuple(observations), self._telemetry,
                                     self.runner.envelope.position, known)

    def _preempted_outcome(self, states=(), observations=(), *, known=True):
        with self._state_lock:
            if self._command_completion_unknown:
                return self._outcome("COMMAND_COMPLETION_UNKNOWN", states, observations, known=False)
            return self._outcome("PREEMPTED", states, observations, known=known)

    def _execute(self, plan, required_state, *, legacy_sink=None, admitted=False, return_revision=None):
        # Reject terminal work before waiting behind a pending SDK command's
        # cleanup. Otherwise the supervisor itself could block on that reply.
        with self._state_lock:
            if self._state is not required_state:
                if admitted:
                    return self._preempted_outcome()
                raise RuntimeError(f"execute requires {required_state.value}")
            admitted_generation = self._generation
        with self._operation_lock:
            with self._state_lock:
                if self._state is not required_state or self._generation != admitted_generation:
                    return self._preempted_outcome()
            states, observations = [], []
            try:
                with self._state_lock:
                    if return_revision is not None and (
                        return_revision != self._pose_revision or plan.start != self.runner.envelope.position
                    ):
                        if plan.commands:
                            states.append(CommandState(plan.commands[0], "NOT_STARTED", self.clock(), None,
                                                       "STALE_RETURN_PLAN"))
                        return self._outcome("REPLAN_REQUIRED", states)
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
                        publication = [self._generation, required_state, self.runner.envelope.position]
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
                    cooperative = isinstance(legacy_sink, CooperativeLegacySink)
                    interruptible = (legacy_sink is None or cooperative) and command.interruptible and command.kind not in {
                        "MOVE", "ROTATE", "SET_SPEED", "LAND",
                    }
                    legacy_errors = []
                    cancellation = Event()
                    scope = _LegacyScope(cancellation, publication) if cooperative else None

                    def operation(cancellation, command=command, publication=publication,
                                  interruptible=interruptible, scope=scope):
                        # A blocked perception/provider must not retain the SDK
                        # movement lock. Its result is published only by run().
                        context = self.client_call() if not interruptible else nullcontext()
                        previous = getattr(self._context, "legacy_scope", None)
                        self._context.legacy_scope = scope
                        try:
                            with context:
                                return self._dispatch_current(command, cancellation, publication, legacy_sink)
                        except BaseException as exc:
                            if legacy_sink is not None:
                                legacy_errors.append(exc)
                            raise
                        finally:
                            self._context.legacy_scope = previous

                    def on_deadline(publication=publication, owned=deadline_owned,
                                    interruptible=interruptible, scope=scope):
                        with self._state_lock:
                            # Provider results expire with their generation.
                            # An unresolved SDK call remains a global movement
                            # hazard even after return/terminal preemption.
                            unknown = not interruptible or (scope is not None and scope.active_calls > 0)
                            if scope is not None:
                                scope.completion_unknown |= unknown
                            if unknown or self._current_locked(publication):
                                owned.set()
                                self._deadline_expired(completion_unknown=unknown)

                    # A native SETTLE *is* a timed delay: its own timer ends it
                    # successfully. The session deadline still preempts it.
                    settle_timer = (command.kind == "SETTLE" and self.command_sink is None and legacy_sink is None
                                    and (self.deadline is None or deadline < self.deadline))
                    if command.upper_bound_s == 0 and command.kind in {"WATCHDOG", "TRIM_ALLOWANCE"}:
                        result = WaitOutcome("COMPLETED", CommandOutcome(), started, started, True)
                    else:
                        if interruptible:
                            with self._state_lock:
                                self._active_cancellation = cancellation
                                if not self._current_locked(publication):
                                    cancellation.set()
                        try:
                            result = self._wait.run(
                                operation, deadline, (lambda: None) if settle_timer else on_deadline,
                                cancellation=cancellation,
                            )
                        finally:
                            with self._state_lock:
                                if self._active_cancellation is cancellation:
                                    self._active_cancellation = None
                    if scope is not None and result.status != "COMPLETED" and not result.completion_known:
                        with self._state_lock:
                            unknown = scope.completion_unknown or scope.active_calls > 0
                            if unknown:
                                self._deadline_expired(completion_unknown=True)
                            else:
                                # Provider work may still run, but cancellation
                                # has revoked its authority to send any SDK call.
                                result = replace(result, completion_known=True)
                    outcome, cleanup = self._publish_result(
                        command, result, publication, states, observations, started,
                        settle_timer=settle_timer, deadline_owned=deadline_owned.is_set(),
                        legacy=legacy_sink is not None,
                    )
                    if cleanup:
                        self._cleanup()
                    if legacy_errors and result.status == "FAILED" and result.completion_known:
                        raise legacy_errors[0]
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
                return _NOT_DISPATCHED
            if command.kind == "LAND":
                # LAND is always executor-owned; an injected sink cannot bypass
                # the terminal latch or create an untracked landing attempt.
                self._transition_locked(ExecutorState.TERMINAL)
                publication[:2] = [self._generation, ExecutorState.TERMINAL]
        if command.kind == "LAND":
            return self._land_locked()
        if legacy_sink is not None:
            if isinstance(legacy_sink, CooperativeLegacySink):
                return legacy_sink.callback(command, cancellation)
            return legacy_sink(command)
        return self._dispatch(command, cancellation)

    def _valid_pose(self, value) -> bool:
        if type(value) is not Position:
            return False
        try:
            return (all(type(v) in (int, float) and math.isfinite(v)
                        for v in (value.x_cm, value.y_cm, value.z_cm))
                    and self.runner.envelope.contains(value))
        except (TypeError, ValueError, OverflowError):
            return False

    def _valid_height(self, height) -> bool:
        return (type(height) is int
                and self.runner.envelope.min_z_cm <= height <= self.runner.envelope.max_z_cm)

    def _normalise_result(self, command, value, origin, *, legacy=False):
        """Only each command's declared payload may reach flight state."""
        invalid = "INVALID_COMMAND_RESULT"
        acknowledgement = type(value) is CommandOutcome and type(value.completed) is bool
        if acknowledgement and not value.completed:
            return None, "COMMAND_REJECTED"
        if legacy:
            # The historical mission adapter owns its validated effects/logs;
            # its public sink contract returns only a completion acknowledgement.
            return (value, None) if acknowledgement else (None, invalid)
        if command.kind == "LAND":
            if type(value) is TerminalOutcome and value is self._terminal:
                return value, "LANDING_FAILED" if value.state == "LANDING_FAILED" else None
        elif command.kind == "CAPTURE":
            if (type(value) is str and 0 < len(value) <= 256 and value.isprintable()
                    and not any(character.isspace() for character in value)):
                return value, None
        elif command.kind in {"LOCALISE", "MOVE"}:
            if command.kind == "MOVE" and acknowledgement:
                try:
                    args = dict(command.arguments)
                    value = self.runner.envelope.validate_move(origin, args["direction"], args["distance_cm"])
                except (KeyError, TypeError, ValueError):
                    return None, invalid
            if self._valid_pose(value):
                return value, None
        elif command.kind == "READ_TELEMETRY":
            if (type(value) is HeightTelemetry and dict(command.arguments).get("phase") == "vertical"
                    and self._valid_height(value.height_cm)):
                return value, None
            if (type(value) is Telemetry and type(value.battery_percent) is int
                    and 0 <= value.battery_percent <= 100 and self._valid_height(value.height_cm)
                    and type(value.flight_time_s) is int and value.flight_time_s >= 0
                    and (value.temperature_c is None or type(value.temperature_c) is int)):
                return value, None
        elif command.kind in {"ROTATE", "SET_SPEED", "SETTLE", "WATCHDOG", "TRIM_ALLOWANCE"}:
            if acknowledgement:
                return value, None
        return None, invalid

    def _commit_pose_locked(self, pose: Position) -> None:
        self.runner.envelope.position = pose
        self._pose_revision += 1
        if self._telemetry is not None:
            self._telemetry = replace(self._telemetry, height_cm=pose.z_cm)

    def _publish_result(self, command, result, publication, states, observations, started, *,
                        settle_timer: bool, deadline_owned: bool, legacy: bool):
        # This lock covers validation AND publication, so a terminal/return
        # latch cannot slip between the check and a pose/telemetry/result write.
        with self._state_lock:
            current = self._current_locked(publication)
            physical_completion = (command.kind in {"MOVE", "ROTATE"} and result.status == "COMPLETED"
                                   and result.completion_known and result.value is not _NOT_DISPATCHED
                                   and not self._command_completion_unknown)
            if not current and not deadline_owned and not physical_completion:
                states.append(CommandState(command, "PREEMPTED", started,
                                           result.completed_at if result.completion_known else None,
                                           "LIFECYCLE_PREEMPTED"))
                return self._preempted_outcome(states, observations,
                                               known=result.completion_known), False
            if settle_timer and result.status == "DEADLINE_EXCEEDED":
                states.append(CommandState(command, "COMPLETED", started, result.completed_at))
                return None, False
            if result.status != "COMPLETED":
                noninterruptible = legacy or not command.interruptible or command.kind in {"MOVE", "ROTATE", "SET_SPEED", "LAND"}
                unknown = noninterruptible and not result.completion_known
                status = "COMMAND_COMPLETION_UNKNOWN" if unknown else result.status
                states.append(CommandState(command, status, started,
                                           None if unknown else result.completed_at,
                                           result.error_category or "DEADLINE_EXCEEDED"))
                return self._outcome(status, states, observations, known=not unknown), result.status in {"FAILED", "WATCHDOG_FAILED"}
            value, failure = self._normalise_result(command, result.value, publication[2], legacy=legacy)
            if failure is not None:
                states.append(CommandState(command, "FAILED", started, result.completed_at, failure))
                self._terminal_reason = failure
                return self._outcome("FAILED", states, observations), True
            states.append(CommandState(command, "COMPLETED", started, result.completed_at))
            if physical_completion:
                # SDK acknowledgement is a physical fact, even if a return or
                # landing preempted the optional plan while it was in flight.
                if type(value) is Position:
                    self._commit_pose_locked(value)
                else:
                    self._pose_revision += 1
                if not current:
                    return self._preempted_outcome(states, observations, known=True), False
            elif type(value) is Position:
                self._commit_pose_locked(value)
            elif type(value) is HeightTelemetry:
                self._commit_pose_locked(replace(self.runner.envelope.position, z_cm=value.height_cm))
            elif type(value) is Telemetry:
                self._telemetry = value
                if dict(command.arguments).get("phase") == "vertical":
                    self._commit_pose_locked(replace(self.runner.envelope.position, z_cm=value.height_cm))
            elif command.kind == "CAPTURE" and type(value) is str:
                observations.append(value)
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
                return HeightTelemetry(height)
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
        return CommandOutcome()

    def _deadline_expired(self, *, completion_unknown: bool = False) -> None:
        with self._state_lock:
            if self._state is ExecutorState.CLOSED:
                return
            self._command_completion_unknown |= completion_unknown
            self._transition_locked(ExecutorState.TERMINAL, invalidate=True)
            if self._terminal is None:
                self._terminal_reason = ("COMMAND_COMPLETION_UNKNOWN" if self._command_completion_unknown
                                         else "DEADLINE_EXCEEDED")
                if self._land_attempted and self._command_completion_unknown:
                    self._terminal = self._terminal_outcome("COMMAND_COMPLETION_UNKNOWN")
            if self._cleanup_started:
                return
            self._cleanup_started = True
        # The watcher never waits on the SDK lock. Landing queues behind the
        # unresolved command; firmware-unvalidated concurrent movement is not
        # a cancellation mechanism. The caller remains free to supervise.
        Thread(target=self._cleanup, name="checkpoint-terminal", daemon=True).start()

    def land(self, *, upper_bound_s: float = 10.0, deadline: float | None = None) -> TerminalOutcome:
        """Supervise one native descent while retaining SDK ownership until reply."""
        if (type(upper_bound_s) not in (int, float) or not math.isfinite(upper_bound_s)
                or upper_bound_s <= 0 or (deadline is not None and not math.isfinite(deadline))):
            raise ValueError("landing requires finite positive bound and finite deadline")
        with self._state_lock:
            if self._terminal is not None:
                return self._terminal
            if self._state is not ExecutorState.CLOSED:
                self._transition_locked(ExecutorState.TERMINAL)
        with self._operation_lock:
            with self._state_lock:
                if self._terminal is not None:
                    return self._terminal
            landing_clock = self.clock
            try:
                landing_deadline = landing_clock() + upper_bound_s
                if not math.isfinite(landing_deadline):
                    raise ValueError("invalid landing clock")
            except BaseException:
                # A poisoned session clock cannot authorize more work. The
                # already selected terminal cleanup still gets a real bound.
                landing_clock = time.monotonic
                landing_deadline = landing_clock() + upper_bound_s
            else:
                for limit in (deadline, self.deadline):
                    if limit is not None:
                        landing_deadline = min(landing_deadline, limit)

            def expire():
                with self._state_lock:
                    # A controlled Land already recorded (note_controlled_land)
                    # must never be overwritten by this landing's own deadline.
                    if self._terminal is None:
                        self._terminal_reason = "COMMAND_COMPLETION_UNKNOWN"
                        self._terminal = self._terminal_outcome("COMMAND_COMPLETION_UNKNOWN")
                    self._deadline_expired(completion_unknown=True)

            waited = SupervisedWait(clock=landing_clock).run(
                lambda cancel: self._land_locked(), landing_deadline, expire)
            if waited.status != "COMPLETED":
                expire()
            result = self._terminal
            assert result is not None
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
            with self._state_lock:
                if self._terminal is None:
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
        if state == "COMMAND_COMPLETION_UNKNOWN":
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
        # Latch terminal/cancel an interruptible wait before waiting for the
        # active operation to release its lock.
        self.land()
        with self._operation_lock:
            with self.command_lock:
                if self._close_attempted:
                    return
                self._close_attempted = True
                try:
                    self.client.close()
                finally:
                    with self._state_lock:
                        self._transition_locked(ExecutorState.CLOSED)
