"""One authorized live count-and-return session: composition only.

Plan 06 Task 4 fix round 4 (Q2). ``scripts/room_session.py live`` runs every
authorization gate and the motors-off connectivity preflight, then hands this
module the already-verified inputs. This module composes existing, tested
parts -- it defines no new safety rule:

1. ``LocalizationTracker``/``ReserveGuard`` from the operator's measured
   drift/energy calibration *files* (``load_drift_profile``/
   ``load_energy_profile``). Nothing here synthesizes a calibration constant;
   a malformed or missing file refuses.
2. The flight stack (executor, telemetry refresh, emergency landing, frame
   source) from the injected ``flight_stack_factory``, bound to that tracker
   and clock. This module never constructs a drone adapter itself; the CLI's
   default factory is ``sentinel.room.live_stack.TelloFlightStackFactory``.
3. A REAL-mode ``SafetySupervisor``. ``validate_bindings`` requires a grant in
   the session's capability profile bound to ``calibration_hashes`` of the
   exact drift/energy/limits/site this session flies with; the CLI copies the
   matching registry grants into the session row (``session_capabilities``).
4. The operator's site assurances, already verified by the CLI against this
   same monotonic clock, re-keyed into the supervisor's fresh clock domain
   (``adopt_assurances``) -- same window, never renewed.
5. The goal ledger, compiled by ``GoalCompiler`` against the session's own
   ``ReadQueries`` snapshot. A rejected ledger ends BLOCKED without takeoff.
6. ``before_takeoff`` (the CLI's connectivity freshness recheck), then
   ``SafetySupervisor.start`` (takeoff), then
   ``RoomController.run_until_terminal`` with the production observation
   collaborators from ``sentinel.room.wiring``.

Whether the controller offers OBSERVE in REAL mode is the controller's own
decision (``RoomController._observation_enabled``); this module never
overrides it.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, replace
from datetime import datetime
import json
from pathlib import Path
from typing import Callable, Iterable

from sentinel.cube.checkpoint import CommandLimits, TransportLimits
from sentinel.cube.energy import EnergyProfile, ExecutionMode, ReserveGuard
from sentinel.cube.localization import DriftProfile, LocalizationTracker
from sentinel.cube.step_executor import ExecutorState, TerminalOutcome

from . import wiring
from .controller import RoomController
from .goals import GoalCompiler
from .models import (AcceptanceManifest, CapabilityProfile, GoalStatus, TerminalState,
                     canonical_acceptance_manifest_sha256)
from .queries import ReadQueries
from .site import ContingencyAssurance, RoomSite
from .store import RoomStore
from .supervisor import SafetySupervisor

#: Upper bound on the operator request text handed to goal compilation.
REQUEST_LIMIT_BYTES = 4096
_FLIGHT_GRANT_STATES = ("PROMOTED", "TRIAL")
_AIRBORNE_STATES = (ExecutorState.AIRBORNE, ExecutorState.RETURNING)


class LiveSessionError(RuntimeError):
    """The session was refused before takeoff; nothing flew."""


@dataclass(frozen=True)
class FlightStack:
    """What ``client_factory(tracker=..., clock=..., site=...)`` must return.

    ``executor`` is a connected ``StepExecutor``-compatible object (``start``,
    ``execute``, ``return_home``, ``land``, ``apply_reanchor``, ``state``);
    ``refresh()`` returns the current ``(PoseHealth, EnergySample)`` measured
    from the aircraft; ``emergency(executor)`` performs a forced landing and
    returns its ``TerminalOutcome``; ``frame_source.read_frame(timeout=...)``
    reads the connected video stream for perception.
    """

    executor: object
    refresh: Callable[[], tuple]
    emergency: Callable[[object], TerminalOutcome]
    frame_source: object


@dataclass(frozen=True)
class LiveSessionOutcome:
    terminal_state: TerminalState
    took_off: bool
    ledger_status: str
    landing: TerminalOutcome | None
    reason: str


def _load_json_object(path: str | Path, what: str) -> dict:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LiveSessionError(f"{what} {str(path)!r} could not be read: {exc}") from exc
    if not isinstance(document, dict):
        raise LiveSessionError(f"{what} {str(path)!r} must be a JSON object")
    return document


def _exact_fields(document: dict, cls, what: str) -> None:
    names = {field.name for field in fields(cls)}
    if set(document) != names:
        raise LiveSessionError(
            f"{what} fields do not match {cls.__name__}: missing {sorted(names - set(document))}, "
            f"unknown {sorted(set(document) - names)}")


def load_drift_profile(path: str | Path) -> DriftProfile:
    """Parse a measured ``pose-drift-v1`` profile; every field is required."""
    document = _load_json_object(path, "drift profile")
    _exact_fields(document, DriftProfile, "drift profile")
    try:
        return DriftProfile(**{**document, "evidence_refs": tuple(document["evidence_refs"])})
    except (TypeError, ValueError) as exc:
        raise LiveSessionError(f"drift profile {str(path)!r} is invalid: {exc}") from exc


def load_energy_profile(path: str | Path) -> EnergyProfile:
    """Parse a measured energy profile; every field is required."""
    document = _load_json_object(path, "energy profile")
    _exact_fields(document, EnergyProfile, "energy profile")
    try:
        return EnergyProfile(**document)
    except (TypeError, ValueError) as exc:
        raise LiveSessionError(f"energy profile {str(path)!r} is invalid: {exc}") from exc


def load_command_limits(path: str | Path | None) -> CommandLimits:
    """Calibrated ``CommandLimits`` overrides; ``None`` keeps the defaults.

    The supervisor's grant binding (``calibration_hashes``) covers these exact
    limits, so a mismatch with what the grant was issued for refuses there.
    """
    if path is None:
        return CommandLimits()
    document = _load_json_object(path, "command limits")
    names = {field.name for field in fields(CommandLimits)}
    unknown = set(document) - names
    if unknown:
        raise LiveSessionError(f"command limits {str(path)!r} has unknown field(s): {sorted(unknown)}")
    try:
        if "transport_limits" in document:
            document = {**document, "transport_limits": TransportLimits(**document["transport_limits"])}
        return replace(CommandLimits(), **document)
    except (TypeError, ValueError) as exc:
        raise LiveSessionError(f"command limits {str(path)!r} are invalid: {exc}") from exc


def session_capabilities(registry, manifest) -> CapabilityProfile:
    """The registry grants this run's session row carries.

    Only grants for this manifest's capability, site and aircraft in a
    flight-capable state (PROMOTED/TRIAL). Which one actually admits flight is
    decided by ``SafetySupervisor.validate_bindings`` against the calibration
    this session flies with -- never here.
    """
    return CapabilityProfile(tuple(
        grant for grant in registry.grants
        if grant.capability == manifest.requested_capability and grant.site_sha256 == manifest.site_sha256
        and grant.aircraft_id == manifest.aircraft_id and grant.state in _FLIGHT_GRANT_STATES
    ))


def acceptance_manifest_for(manifest) -> AcceptanceManifest | None:
    """The same acceptance binding ``capabilities.admit_live`` admits a TRIAL grant under."""
    if manifest.mode != "acceptance":
        return None
    value = AcceptanceManifest(session_id=manifest.run_id, capability=manifest.requested_capability,
                               manifest_sha256="")
    return replace(value, manifest_sha256=canonical_acceptance_manifest_sha256(value))


def adopt_assurances(store: RoomStore, *, session_id: str, clock_domain_id: str,
                     assurances: Iterable[ContingencyAssurance]) -> tuple[ContingencyAssurance, ...]:
    """Re-key already-verified operator assurances into the supervisor's clock domain.

    ``SafetySupervisor`` mints a new clock domain per instance, so evidence
    recorded under the operator's domain is invisible to it. The CLI has just
    verified each assurance (this run's session, this site's contingencies,
    fresh against this same monotonic clock). Each is copied under a new
    assurance ID with the *same* ``assured_monotonic_ms``/``expires_monotonic_ms``
    window -- no deadline is renewed -- and its original ID kept as evidence.
    """
    adopted = []
    for assurance in assurances:
        copy = ContingencyAssurance(
            assurance_id=f"{assurance.assurance_id}@{clock_domain_id}",
            contingency_id=assurance.contingency_id, session_id=session_id,
            clock_domain_id=clock_domain_id, state=assurance.state, assured_at=assurance.assured_at,
            assured_monotonic_ms=assurance.assured_monotonic_ms,
            expires_monotonic_ms=assurance.expires_monotonic_ms,
            evidence_refs=(*assurance.evidence_refs, f"operator-assurance:{assurance.assurance_id}"),
        )
        store.append_contingency_assurance(copy, expected_version=store.session_version(session_id))
        adopted.append(copy)
    return tuple(adopted)


def _pose_profile(profiles):
    """The calibration profile whose validated speeds the checkpoint wiring flies at."""
    usable = [profile for profile in profiles if getattr(profile, "speeds_cm_s", ())]
    if not usable:
        raise LiveSessionError("no --profile-file carries a validated flight speed for checkpoint planning")
    return usable[0]


def run_count_return(
    *, store: RoomStore, manifest, site: RoomSite, drift_profile: DriftProfile,
    energy_profile: EnergyProfile, limits: CommandLimits, calibration_profiles,
    flight_stack_factory: Callable[..., FlightStack], judge, detector,
    assurances: Iterable[ContingencyAssurance], request_text: str, frames_dir: str | Path,
    clock: Callable[[], float], wall_clock: Callable[[], datetime],
    before_takeoff: Callable[[], None], landing_upper_bound_s: float = 10.0,
    provider_wait_s: float = 10.0,
) -> LiveSessionOutcome:
    """Build, take off, run to a terminal state and return the outcome.

    Raises ``LiveSessionError`` for any refusal before takeoff (nothing flew).
    After takeoff it never raises for a session failure: an exception from the
    controller loop is handled by the supervisor's own forced landing and
    reported as ``ABORTED``.
    """
    try:
        prepared = _prepare(
            store=store, manifest=manifest, site=site, drift_profile=drift_profile,
            energy_profile=energy_profile, limits=limits, calibration_profiles=calibration_profiles,
            flight_stack_factory=flight_stack_factory, judge=judge, detector=detector,
            assurances=assurances, request_text=request_text, frames_dir=frames_dir, clock=clock,
            wall_clock=wall_clock, landing_upper_bound_s=landing_upper_bound_s)
        if isinstance(prepared, LiveSessionOutcome):
            return prepared
        before_takeoff()
    except LiveSessionError:
        raise
    except Exception as exc:
        # Nothing has flown yet: any failure up to here is a refusal.
        raise LiveSessionError(f"session setup failed before takeoff: {type(exc).__name__}: {exc}") from exc
    supervisor, stack, ledger, collaborators = prepared
    try:
        supervisor.start(stack.executor)
    except Exception as exc:
        landing = supervisor.last_terminal_outcome
        if landing is None and getattr(stack.executor, "state", None) in _AIRBORNE_STATES:
            # SafetySupervisor.start only lands for failures *after*
            # executor.start returns; a takeoff that raised with the aircraft
            # already up must still be brought down, never reported as a refusal.
            landing = supervisor.refresh_failure(stack.executor)
        if landing is None:
            raise LiveSessionError(f"takeoff refused: {exc}") from exc
        return LiveSessionOutcome(TerminalState.ABORTED, True, ledger.status.value, landing, "START_FAILED")

    controller = RoomController(
        store, judge, supervisor=supervisor, flight_executor=stack.executor, provider_wait_s=provider_wait_s,
        **collaborators,
    )
    try:
        terminal = controller.run_until_terminal(manifest.run_id)
    except Exception:
        landing = supervisor.refresh_failure(stack.executor)
        return LiveSessionOutcome(TerminalState.ABORTED, True, ledger.status.value, landing,
                                  "SESSION_LOOP_FAILED")
    return LiveSessionOutcome(terminal, True, ledger.status.value, supervisor.last_terminal_outcome,
                              "CONTROLLER_TERMINAL")


def _prepare(*, store, manifest, site, drift_profile, energy_profile, limits, calibration_profiles,
             flight_stack_factory, judge, detector, assurances, request_text, frames_dir, clock, wall_clock,
             landing_upper_bound_s):
    """Everything up to (not including) takeoff; returns an outcome only when
    the session ends on the ground (a goal ledger that is not PENDING)."""
    session_id = manifest.run_id
    tracker = LocalizationTracker(drift_profile, clock=clock, real_mode=True,
                                  canonical_heading_deg=limits.canonical_heading_deg)
    reserve = ReserveGuard(energy_profile, clock)
    pose_profile = _pose_profile(calibration_profiles)
    try:
        stack = flight_stack_factory(tracker=tracker, clock=clock, site=site)
    except Exception as exc:
        raise LiveSessionError(str(exc)) from exc
    if not isinstance(stack, FlightStack):
        raise LiveSessionError("client_factory must return a live_session.FlightStack")
    supervisor = SafetySupervisor(
        store=store, session_id=session_id, site=site, tracker=tracker, reserve=reserve, limits=limits,
        aircraft_id=manifest.aircraft_id, mode=ExecutionMode.REAL, refresh=stack.refresh,
        emergency=stack.emergency, clock=clock, wall_clock=wall_clock,
        capability=manifest.requested_capability, acceptance_manifest=acceptance_manifest_for(manifest),
        landing_upper_bound_s=landing_upper_bound_s,
    )
    try:
        supervisor.validate_bindings(site)
    except (ValueError, TypeError) as exc:
        raise LiveSessionError(f"flight calibration refused: {exc}") from exc
    adopt_assurances(store, session_id=session_id, clock_domain_id=supervisor.clock_domain_id,
                     assurances=assurances)
    try:
        resolve_checkpoint = wiring.make_resolve_checkpoint(
            site=site, profile=pose_profile, pose_source=lambda: supervisor.tracker.health, limits=limits)
    except wiring.WiringError as exc:
        raise LiveSessionError(str(exc)) from exc
    perception_capture = wiring.make_perception_capture(
        store=store, flight_client=stack.frame_source, frames_dir=frames_dir,
        requested_model=manifest.requested_detector_identity, run_id=session_id)
    perception_provider = wiring.make_perception_provider(detector_factory=lambda: detector)

    compiler = GoalCompiler(judge, store, input_limit_bytes=REQUEST_LIMIT_BYTES,
                            prompt_revision=manifest.goal_parser_revision)
    ledger = compiler.compile(session_id, request_text, ReadQueries(store).project(session_id))
    if ledger.status != GoalStatus.PENDING:
        # Nothing to fly for: the controller records its own CLARIFY terminal
        # on the ground (no supervisor, so no physical action is possible).
        terminal = RoomController(store, judge).run_until_terminal(session_id)
        return LiveSessionOutcome(terminal, False, ledger.status.value, None, "GOAL_LEDGER_NOT_PENDING")
    collaborators = {"resolve_checkpoint": resolve_checkpoint, "perception_capture": perception_capture,
                     "perception_provider": perception_provider}
    return supervisor, stack, ledger, collaborators
