"""Deterministic flight admission with durable, independently supervised effects.

This is an opt-in runtime boundary, not a room action catalogue. ``refresh``
must read current battery telemetry and the localization stream; forecasts are
never published as measurements. A session has one supervisor/clock domain.
"""
from __future__ import annotations

from copy import copy
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
from threading import Event, RLock, Thread
import time
from typing import Callable, Literal

from sentinel.cube.checkpoint import CheckpointPlan, CommandLimits, PlannedCommand, plan_checkpoint
from sentinel.cube.energy import EnergyEstimate, EnergySample, ExecutionMode, ReserveDecision, ReserveGuard
from sentinel.cube.localization import LocalizationState, LocalizationTracker, PoseForecast, PoseHealth
from sentinel.cube.recovery import RecoveryForecastEvidence, RecoveryVolume, contains_disk
from sentinel.cube.step_executor import CheckpointOutcome, ExecutorState, HomeState, ReanchorPreempted, TerminalOutcome
from sentinel.cube.supervised_wait import SupervisedWait, WaitOutcome
from sentinel.models import Position
from sentinel.motion_bounds import rotate_reply_upper_bound_s

from .models import CapabilityGrant, CapabilityProfile, canonical_grant_sha256
from .site import (ContingencyKind, ContingencyTrigger, RoomSite, SessionClockGuard,
                   adapt_recovery_environment, admit_terminal_contingency, room_site_document)
from .store import RoomStore, VersionConflict


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def calibration_hashes(energy, drift, limits, site, landing_upper_bound_s=10.0) -> tuple[str, ...]:
    """Bind the exact calibrations, command limits, tag map and surveyed site."""
    return tuple(sorted((_sha(asdict(energy)), _sha(asdict(drift) if drift is not None else None),
                         _sha({"commands": asdict(limits), "landing_upper_bound_s": landing_upper_bound_s}),
                         _sha(room_site_document(site)), site.tag_map_sha256)))


@dataclass(frozen=True)
class SupervisorRequest:
    snapshot_version: int
    checkpoint: CheckpointPlan | None
    provider_wait_s: float
    pose: PoseHealth
    energy_sample: EnergySample

    def __post_init__(self):
        if type(self.snapshot_version) is not int or self.snapshot_version < 0:
            raise ValueError("snapshot version must be a nonnegative integer")
        if (type(self.provider_wait_s) not in (int, float)
                or not math.isfinite(self.provider_wait_s) or self.provider_wait_s < 0):
            raise ValueError("provider wait must have a finite nonnegative bound")


@dataclass(frozen=True)
class SupervisorOutcome:
    transition: Literal["CONTINUE", "RECOVER", "RETURN_NOW", "LAND_CONTINGENCY"]
    reason: str
    invalidates_snapshot: bool
    energy: EnergyEstimate
    pose_forecast: PoseForecast
    request: SupervisorRequest
    energy_sample: EnergySample
    calibration_hashes: tuple[str, ...]
    site_sha256: str
    assessed_at: float | None
    termination_plan: CheckpointPlan | None = None
    contingency_id: str | None = None


class _AuditPersistenceFailure(RuntimeError):
    """The durable safety boundary is unavailable; only emergency may run."""


class SafetySupervisor:
    def __init__(self, *, store: RoomStore, session_id: str, site: RoomSite,
                 tracker: LocalizationTracker, reserve: ReserveGuard, limits: CommandLimits,
                 aircraft_id: str, mode: ExecutionMode,
                 refresh: Callable[[], tuple[PoseHealth, EnergySample]],
                 emergency: Callable[[object], TerminalOutcome],
                 clock: Callable[[], float] = time.monotonic,
                 wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 capability: str = "ADAPTIVE_FLIGHT", acceptance_manifest=None,
                 landing_upper_bound_s: float = 10.0, recovery=None, mapped_heading_tag_ids=()):
        if not math.isfinite(landing_upper_bound_s) or landing_upper_bound_s <= 0:
            raise ValueError("landing requires a positive calibrated upper bound")
        self.store, self.session_id, self.site = store, session_id, site
        self.tracker, self.reserve, self.limits = tracker, reserve, limits
        self.aircraft_id, self.mode = aircraft_id, mode
        self.refresh, self.emergency, self.clock, self.wall_clock = refresh, emergency, clock, wall_clock
        self.capability, self.acceptance_manifest = capability, acceptance_manifest
        self.landing_upper_bound_s = landing_upper_bound_s
        self.clock_guard = SessionClockGuard(session_id=session_id)
        self.clock_domain_id = self.clock_guard.clock_domain_id
        self._lock = RLock()
        self._dispatch_lock = RLock()
        self.recovery, self.mapped_heading_tag_ids = recovery, mapped_heading_tag_ids
        self._last_clock = None
        self._terminal = False
        self._measured_home: HomeState | None = None
        self.last_terminal_outcome = None
        self.last_persistence_error = None
        self.last_preempted_version = 0
        self.last_preemption = None
        # A restart cannot erase a durable return/terminal latch or import old
        # clearances: it has a new clock domain and must obtain new assurances.
        events = store.events(session_id)
        self._terminal = any(e.event_type == "DRONE_STATE_RECORDED" and (
            e.payload.get("state", {}).get("terminal_state")
            or e.payload.get("state", {}).get("flight_state") in {"TERMINAL", "CLOSED"}) for e in events)
        self._returning = any(e.event_type == "SAFETY_ASSESSED" and
                             e.payload.get("transition") in {"RETURN_NOW", "LAND_CONTINGENCY"} for e in events)

    def validate_bindings(self, site: RoomSite) -> None:
        self.reserve.profile.validate_for_execution(mode=self.mode,
            site_sha256=site.cube_site_sha256, aircraft_id=self.aircraft_id)
        profile = self.tracker.profile
        if profile is None or not profile.validated or not profile.evidence_refs:
            raise ValueError("validated drift calibration required")
        hashes = calibration_hashes(self.reserve.profile, profile, self.limits, site, self.landing_upper_bound_s)
        with self.store._lock:
            row = self.store.connection.execute(
                "SELECT capability_json FROM room_sessions WHERE session_id = ?", (self.session_id,)).fetchone()
        raw = json.loads(row[0])
        grants = tuple(CapabilityGrant(**{**g, "profile_sha256s": tuple(g["profile_sha256s"]),
                                         "evidence_ids": tuple(g["evidence_ids"])}) for g in raw["grants"])
        now = self.wall_clock()
        if self.mode == ExecutionMode.SIMULATION:
            allowed = any(g.state == "SIMULATION_ONLY" and g.capability == self.capability
                and g.site_sha256 == site.cube_site_sha256 and g.aircraft_id == self.aircraft_id
                and tuple(sorted(g.profile_sha256s)) == hashes and g.evidence_ids
                and g.grant_sha256 == canonical_grant_sha256(g) and g.expires_at is not None
                and now < datetime.fromisoformat(g.expires_at.replace("Z", "+00:00")) for g in grants)
        else:
            allowed = CapabilityProfile(grants).allows(capability=self.capability,
                site_sha256=site.cube_site_sha256, aircraft_id=self.aircraft_id,
                profile_sha256s=hashes, now=now, session_id=self.session_id,
                acceptance_manifest=self.acceptance_manifest)
        if not allowed:
            raise ValueError("capability does not match session/site/aircraft/calibrations")

    def _assurances(self):
        return self.store.contingency_assurances(self.session_id, clock_domain_id=self.clock_domain_id)

    def _site_current(self, site, now):
        site.validate(real_mode=True)
        self.validate_bindings(site)
        guard = copy(self.clock_guard)
        if not guard.observe(now * 1000, session_id=self.session_id, clock_domain_id=self.clock_domain_id):
            raise ValueError("session clock is invalid")
        assurances = self._assurances()
        for trigger in ContingencyTrigger:
            usable = []
            for entry in site.terminal_contingencies:
                if entry.trigger != trigger:
                    continue
                matching = [a for a in assurances if a.contingency_id == entry.contingency_id]
                revoked = {a.assurance_id for a in matching if a.state == "REVOKED"}
                current = [a for a in matching if a.state == "CURRENT" and a.assurance_id not in revoked]
                if len(current) == 1:
                    a = current[0]
                    deadline = min(a.expires_monotonic_ms, a.assured_monotonic_ms + entry.max_assurance_age_ms)
                    if a.assured_monotonic_ms <= now * 1000 < deadline:
                        usable.append(entry)
            if not usable:
                raise ValueError("site contingency assurance missing or expired")

    def _plan(self, pose, commands, name="wait"):
        commands = tuple(commands)
        return CheckpointPlan(name, pose.position, pose.position, pose.heading_deg, pose.heading_deg,
                              commands, sum(c.upper_bound_s for c in commands))

    def _termination(self, pose, site):
        home = self._measured_home
        route = next(r for r in site.routes if r.route_id == (home.route_id if home else site.home.route_id))
        end = route.checkpoints[-1]
        if (end.x_cm, end.y_cm) != (site.home.nominal_pose.x_cm, site.home.nominal_pose.y_cm):
            raise ValueError("home route must end over its declared pad")
        commands, position, heading = [], pose.position, pose.heading_deg
        points = route.checkpoints[:-1] + (home.pose,) if home else route.checkpoints
        for point in points:
            part = plan_checkpoint(checkpoint_id=route.route_id, start=position, target=point,
                start_heading_deg=heading, terminal_heading_deg=self.limits.canonical_heading_deg,
                speed_cm_s=self.limits.trim_speed_cm_s, looks_deg=(), capture_count=0,
                limits=self.limits)
            commands.extend(part.commands)
            position, heading = point, part.terminal_heading_deg
        if home is not None:
            delta = (home.heading_deg - heading + 180) % 360 - 180
            if delta:
                commands.append(PlannedCommand.from_mapping("ROTATE",
                    {"degrees": abs(delta), "direction": "ccw" if delta > 0 else "cw"},
                    upper_bound_s=rotate_reply_upper_bound_s(degrees=abs(delta),
                        base_timeout_s=self.limits.transport_limits.reply_timeout_s), interruptible=False))
                heading = home.heading_deg
        commands.append(PlannedCommand("LAND", (), self.landing_upper_bound_s, False))
        return CheckpointPlan(route.route_id, pose.position, site.home.nominal_pose,
                              pose.heading_deg, heading, tuple(commands), sum(c.upper_bound_s for c in commands))

    def _forecast(self, pose, plan, now):
        tracker = copy(self.tracker)
        # Work only on immutable input/copies. LocalizationTracker.forecast
        # deliberately latches recovery in its owner; assessment must not.
        tracker._health = pose
        return tracker.forecast(plan, now)

    def _path_clear(self, pose, plan, site, *, corridor=False, now=None):
        profile = self.tracker.profile
        if profile is None:
            return False
        position, heading = pose.position, pose.heading_deg
        forecast = self._forecast(pose, plan, self.clock() if now is None else now)
        radius = profile.rotation_radius_cm + max(pose.xy_uncertainty_cm,
                                                  forecast.projected.xy_uncertainty_cm)
        height = profile.max_height_error_cm
        volumes = site.free_space_volumes
        if corridor:
            route = next(r for r in site.routes if r.route_id == site.home.route_id)
            volumes = tuple(v for v in site.corridors if v.volume_id in route.corridor_volume_ids)
        for command in plan.commands:
            previous = position
            args = dict(command.arguments)
            if command.kind == "LAND":
                # assess checks descent separately after localization/reserve
                # admission, using the full projected landing footprint.
                continue
            if command.kind == "TRIM_ALLOWANCE" and args.get("max_rounds", 0):
                # Trims are conditional commands not exposed by this adapter.
                return False
            propagated = self.tracker._command(replace(pose, position=position, heading_deg=heading), command)
            position, heading = propagated.position, propagated.heading_deg
            points = tuple(Position(p.x_cm, p.y_cm, p.z_cm + dz)
                           for p in (previous, position) for dz in (-height, height))
            if not any(all(contains_disk(RecoveryVolume(v.volume_id, v.polygon_xy_cm, v.z_interval_cm), p, radius)
                           for p in points) for v in volumes):
                return False
            low = (min(previous.x_cm, position.x_cm) - radius, min(previous.y_cm, position.y_cm) - radius,
                   min(previous.z_cm, position.z_cm) - height)
            high = (max(previous.x_cm, position.x_cm) + radius, max(previous.y_cm, position.y_cm) + radius,
                    max(previous.z_cm, position.z_cm) + height)
            for forbidden in site.forbidden_volumes:
                f_low = (*map(min, zip(*forbidden.polygon_xy_cm)), forbidden.z_interval_cm[0])
                f_high = (*map(max, zip(*forbidden.polygon_xy_cm)), forbidden.z_interval_cm[1])
                if all(low[i] <= f_high[i] and f_low[i] <= high[i] for i in range(3)):
                    return False
        return True

    def _landing_contingency(self, site, pose, *, now, trigger, energy_available,
                             home=False, require_descent=False):
        home_pose = self._measured_home.pose if self._measured_home is not None else site.home.nominal_pose
        for entry in site.terminal_contingencies:
            if (home or require_descent) and entry.kind == ContingencyKind.EMERGENCY:
                # Emergency escalation is not verified landing geometry.
                continue
            if home and (entry.z_band_cm[0] != site.home.nominal_pose.z_cm
                         or not math.isclose(pose.position.x_cm, home_pose.x_cm, abs_tol=1e-6)
                         or not math.isclose(pose.position.y_cm, home_pose.y_cm, abs_tol=1e-6)):
                # The policy must describe the actual pad and touchdown level.
                continue
            try:
                admitted = admit_terminal_contingency(site, self._assurances(),
                    contingency_id=entry.contingency_id, trigger=trigger,
                    session_id=self.session_id, clock_domain_id=self.clock_domain_id,
                    now_monotonic_ms=now * 1000, pose=pose, energy_available=energy_available,
                    drift_profile=self.tracker.profile, clock_guard=copy(self.clock_guard))
            except (TypeError, ValueError):
                admitted = None
            if admitted is not None:
                return admitted
        return None

    def _landings_clear(self, pose, plan, site, *, now, assessment_now, energy_available, home=False):
        prefix = ()
        for command in plan.commands:
            prefix += (command,)
            if command.kind != "LAND":
                continue
            landing = self._forecast(pose, self._plan(pose, prefix), now).projected
            # Include all drift through descent. Assurances must be current at
            # admission, then refreshed/rechecked before the physical approach.
            if self._landing_contingency(site, landing, now=assessment_now,
                    trigger=ContingencyTrigger.RETURN_ENERGY_UNAVAILABLE,
                    energy_available=energy_available, home=home, require_descent=True) is None:
                return False
        return True

    def _landing_plan(self, pose):
        descent = PlannedCommand("LAND", (), self.landing_upper_bound_s, False)
        return self._plan(pose, (descent,))

    def _land(self, executor):
        return executor.land(upper_bound_s=self.landing_upper_bound_s,
                             deadline=getattr(executor, "deadline", None))

    def _landing_forecast(self, pose, now):
        return self._forecast(pose, self._landing_plan(pose), now)

    def _landing_energy(self, guard, request):
        return guard.assess(current=request.energy_sample, next_plan=None, recovery_plan=None,
            provider_wait_s=0, termination_plan=self._landing_plan(request.pose))

    def assess(self, request: SupervisorRequest, site: RoomSite) -> SupervisorOutcome:
        """Read-only decision; no telemetry I/O, latch, pose mutation or events."""
        try:
            now = self.clock()
        except Exception:
            now = None
        clock_valid = type(now) in (int, float) and math.isfinite(now) and now >= 0
        forecast = PoseForecast(False, request.pose, ())
        energy = EnergyEstimate(request.energy_sample.battery_pct, 0, request.energy_sample.battery_pct,
                                self.reserve.profile.landing_floor_pct, ReserveDecision.LAND_CONTINGENCY, ())
        termination = None
        prerequisites_valid = False
        hashes = calibration_hashes(self.reserve.profile, self.tracker.profile, self.limits, site, self.landing_upper_bound_s)

        def result(transition, reason, trigger=ContingencyTrigger.NAVIGATION_UNAVAILABLE):
            contingency = None
            decision_forecast = forecast
            decision_energy = energy
            if transition == "LAND_CONTINGENCY" and clock_valid:
                # A current hover footprint cannot authorize ten seconds of
                # descent: retain its entire calibrated forecast in the audit.
                decision_forecast = self._landing_forecast(request.pose, now)
                if prerequisites_valid:
                    descent_energy = self._landing_energy(guard, request)
                    decision_energy = replace(energy, components=energy.components + (
                        ("contingency_landing_s", self.landing_upper_bound_s),
                        ("contingency_landing_cost_pct", descent_energy.action_cost_pct),
                        ("contingency_available_pct", descent_energy.current_pct)))
                    admitted = self._landing_contingency(site, decision_forecast.projected, now=now, trigger=trigger,
                        energy_available=descent_energy.decision != ReserveDecision.LAND_CONTINGENCY)
                    contingency = admitted.contingency_id if admitted is not None else None
            return SupervisorOutcome(transition, reason, transition != "CONTINUE", decision_energy, decision_forecast,
                request, request.energy_sample, hashes, _sha(room_site_document(site)),
                now if clock_valid else None, termination, contingency)

        if self._terminal:
            return result("LAND_CONTINGENCY", "SESSION_TERMINAL")
        try:
            if not clock_valid:
                raise ValueError("invalid session clock")
            self._site_current(site, now)
        except (ValueError, TypeError, OverflowError):
            return result("LAND_CONTINGENCY", "SITE_OR_CAPABILITY_INVALID")
        guard = copy(self.reserve)
        guard.clock = lambda: now
        if not guard.ingest_sample(request.energy_sample) or not guard._sample_is_current(request.energy_sample, now):
            return result("LAND_CONTINGENCY", "TELEMETRY_INVALID", ContingencyTrigger.RETURN_ENERGY_UNAVAILABLE)
        prerequisites_valid = True
        commands = request.checkpoint.commands if request.checkpoint else ()
        if request.provider_wait_s:
            commands += (PlannedCommand("SETTLE", (), request.provider_wait_s, True),)
        plan = self._plan(request.pose, commands)
        forecast = self._forecast(request.pose, plan, now)
        if request.pose.state == LocalizationState.LOST or forecast.projected.state == LocalizationState.LOST:
            return result("LAND_CONTINGENCY", "LOCALIZATION_LOST")
        if request.checkpoint is not None and (
                request.checkpoint.start != request.pose.position
                or request.checkpoint.start_heading_deg != request.pose.heading_deg):
            return result("LAND_CONTINGENCY", "STALE_CHECKPOINT_START")
        try:
            termination = self._termination(request.pose, site)
            projected_termination = self._termination(forecast.projected, site)
            termination_price = max((termination, projected_termination), key=lambda p: p.worst_case_s)
            path_clear = self._path_clear(request.pose, plan, site)
            home_clear = self._path_clear(request.pose, termination, site, corridor=True)
            # Each command boundary must retain a usable declared home route.
            # Price/check future route segments at that future pose's time so
            # their envelope includes drift already accumulated by optional work.
            projected_home_clear = True
            projected_routes = []
            prefix = ()
            for command in plan.commands:
                prefix += (command,)
                projected = self._forecast(request.pose, self._plan(request.pose, prefix), now).projected
                route = self._termination(projected, site)
                projected_routes.append((projected, route))
                if not self._path_clear(projected, route, site, corridor=True, now=projected.propagated_at):
                    projected_home_clear = False
                    break
            home_forecast = self._forecast(request.pose, termination, now)
            full_forecast = self._forecast(request.pose,
                self._plan(request.pose, plan.commands + projected_termination.commands), now)
        except (ValueError, KeyError, StopIteration):
            return result("LAND_CONTINGENCY", "TERMINATION_PATH_INVALID")
        if not home_clear:
            return result("LAND_CONTINGENCY", "TERMINATION_PATH_INVALID")
        recovery = self._plan(request.pose, (PlannedCommand("LOCALISE", (),
                              self.tracker.profile.recovery_deadline_s, True),), "recovery-reserve")
        energy = guard.assess(current=request.energy_sample, next_plan=request.checkpoint,
                              recovery_plan=recovery, provider_wait_s=request.provider_wait_s,
                              termination_plan=termination_price)
        if energy.decision == ReserveDecision.LAND_CONTINGENCY:
            return result("LAND_CONTINGENCY", "RETURN_ENERGY_UNAVAILABLE", ContingencyTrigger.RETURN_ENERGY_UNAVAILABLE)
        refresh_reasons = {"fresh_route_validation", "fresh_energy_validation"}
        if set(home_forecast.reasons) - refresh_reasons:
            forecast = replace(forecast, admitted=False,
                reasons=tuple(sorted(set(forecast.reasons) | set(home_forecast.reasons))))
            if self._returning or guard.return_latched:
                return result("LAND_CONTINGENCY", "RETURN_LOCALIZATION_UNAVAILABLE")
            recovery_energy = guard.assess(current=request.energy_sample, next_plan=None,
                recovery_plan=recovery, provider_wait_s=0, termination_plan=termination)
            if recovery_energy.decision == ReserveDecision.CONTINUE:
                return result("RECOVER", "RETURN_REQUIRES_RECOVERY")
            return result("LAND_CONTINGENCY", "RECOVERY_ENERGY_UNAVAILABLE")
        # The reserve decision covers the entire termination, including LAND.
        # A clear route to the pad alone never authorizes its final descent.
        if not self._landings_clear(request.pose, termination, site, now=now,
                assessment_now=now, energy_available=True, home=True):
            return result("LAND_CONTINGENCY", "TERMINATION_LANDING_INVALID")
        if self._returning or guard.return_latched or energy.decision == ReserveDecision.RETURN_NOW:
            return result("RETURN_NOW", "RETURN_LATCHED" if self._returning else "OPTIONAL_WORK_UNAFFORDABLE")
        if not path_clear:
            return result("RETURN_NOW", "OPTIONAL_PATH_INVALID")
        if not projected_home_clear:
            return result("RETURN_NOW", "PROJECTED_TERMINATION_PATH_INVALID")
        if set(full_forecast.reasons) - refresh_reasons:
            forecast = replace(forecast, admitted=False,
                reasons=tuple(sorted(set(forecast.reasons) | set(full_forecast.reasons))))
        if not forecast.admitted:
            if set(forecast.reasons) <= {"fresh_route_validation", "fresh_energy_validation"}:
                forecast = replace(forecast, admitted=True, reasons=())
            else:
                return result("RECOVER", "LOCALIZATION_RECOVERY_REQUIRED")
        if not self._landings_clear(request.pose, plan, site, now=now,
                assessment_now=now, energy_available=True):
            return result("RETURN_NOW", "OPTIONAL_LANDING_INVALID")
        for projected, route in projected_routes:
            if not self._landings_clear(projected, route, site, now=projected.propagated_at,
                    assessment_now=now, energy_available=True, home=True):
                return result("RETURN_NOW", "PROJECTED_TERMINATION_LANDING_INVALID")
        return result("CONTINUE", "ADMITTED")

    def prepare(self, *, snapshot_version, checkpoint=None, provider_wait_s=0, fallback=None,
                remaining_commands=None):
        """Effectful telemetry refresh and stream/clock observation before admission."""
        with self._lock:
            if self._terminal:
                raise RuntimeError("supervisor is terminal")
            try:
                now = self.clock()
            except Exception:
                now = float("nan")
            if self._last_clock is not None and now < self._last_clock:
                self.clock_guard.invalidate()
            self._last_clock = now
            self.clock_guard.observe(now * 1000, session_id=self.session_id, clock_domain_id=self.clock_domain_id)
            try:
                pose, sample = self.refresh()
            except Exception:
                if fallback is None:
                    raise
                return replace(fallback, transition="LAND_CONTINGENCY", reason="TELEMETRY_UNAVAILABLE",
                    invalidates_snapshot=True, contingency_id=None,
                    request=replace(fallback.request, snapshot_version=snapshot_version))
            self.reserve.ingest_sample(sample)
            if remaining_commands is not None:
                checkpoint = self._plan(pose, remaining_commands)
            outcome = self.assess(SupervisorRequest(snapshot_version, checkpoint, provider_wait_s, pose, sample), self.site)
            if outcome.transition == "CONTINUE":
                if getattr(self.recovery, "resume_required", False):
                    validated = self.recovery.confirm_resume(pose, route_validated=True, energy_validated=True)
                    outcome = replace(outcome, request=replace(outcome.request, pose=validated))
                elif pose.requires_route_validation or pose.requires_energy_validation:
                    validated = self.tracker.acknowledge_validation(pose.pose_revision,
                        route_validated=True, energy_validated=True)
                    outcome = replace(outcome, request=replace(outcome.request, pose=validated))
            return outcome

    def validate_start(self, site=None):
        active = site or self.site
        self._site_current(active, self.clock())
        pose, sample = self.refresh()
        outcome = self.assess(SupervisorRequest(self.store.session_version(self.session_id), None, 0, pose, sample), active)
        if outcome.transition != "CONTINUE":
            raise ValueError(f"start blocked: {outcome.reason}")

    def _bind_home(self, executor):
        home = getattr(executor, "home", None)
        if home is not None:
            if not isinstance(home, HomeState) or home.route_id != self.site.home.route_id:
                raise ValueError("executor home must match the surveyed home route")
            if self._measured_home is not None and self._measured_home != home:
                raise ValueError("session measured home cannot change")
            self._measured_home = home

    def start(self, executor) -> HomeState:
        """Admit takeoff, bind its measured home, and validate the real return."""
        with self._dispatch_lock:
            self.validate_start()
            home = executor.start(self.site.home)
            try:
                self._bind_home(executor)
                self.validate_start()
            except Exception:
                self.refresh_failure(executor)
                raise
            return home

    def refresh_failure(self, executor):
        """Fail closed when no sample exists; absent measurements stay null."""
        with self._dispatch_lock:
            if self._terminal and self.last_terminal_outcome is not None:
                return self.last_terminal_outcome
            try:
                return self._refresh_failure(executor)
            except _AuditPersistenceFailure as exc:
                return self._persistence_emergency(executor, exc)

    def _refresh_failure(self, executor):
        with self._dispatch_lock:
            try:
                version = self.store.session_version(self.session_id)
                self.last_preempted_version = version
                correlation = f"refresh-failure:{self.session_id}:{version}"
                event = self.store.append_event_current(session_id=self.session_id, assessed_snapshot_version=version,
                    event_type="SAFETY_ASSESSED", payload={"transition": "LAND_CONTINGENCY",
                        "reason": "TELEMETRY_UNAVAILABLE", "invalidates_snapshot": True,
                        "energy_sample": None, "pose_forecast": None, "energy": None},
                    correlation_id=correlation, causation_id=None, idempotency_key=correlation)
            except Exception as exc:
                raise _AuditPersistenceFailure(str(exc)) from exc
            self._returning = self._terminal = True
            self.last_preemption = None
            self.last_preempted_version = version
            result = self.emergency(executor)
            state = {"battery_percent": None, "pose_source": "UNAVAILABLE", "pose_quality": "UNAVAILABLE",
                     "flight_state": executor.state.value, "airborne": None, "terminal_state": result.state,
                     "action_progress": result.state, "completion_known": result.completed_at is not None}
            self._persist_completion(result=result, state=state,
                pose_measured_at=None, energy_measured_at=None, clock_domain_id=self.clock_domain_id,
                sample_max_age_s=self.reserve.profile.sample_max_age_s, fresh=False, causation_id=event.event_id,
                assessed_snapshot_version=version)
            return result

    def run_wait(self, operation, *, snapshot_version, timeout_s, executor):
        """Supervise Jev/perception without relying on provider cancellation.

        Operations return data; they must not publish events or issue SDK calls.
        A late result is discarded. Safety dispatch happens on the caller after
        cancellation, never inside the bounded watchdog callback.
        """
        if self._terminal:
            raise RuntimeError("supervisor is terminal")
        try:
            initial = self.prepare(snapshot_version=snapshot_version, provider_wait_s=timeout_s)
        except Exception:
            self.refresh_failure(executor)
            return WaitOutcome("PREEMPTED", None, None, None, True)
        self.last_preemption = None
        if initial.transition != "CONTINUE":
            self.last_preemption = initial
            self.dispatch(initial, executor)
            return WaitOutcome("PREEMPTED", None, self.clock(), self.clock(), True)
        deadline = self.clock() + timeout_s
        cancel, done = Event(), Event()
        mandatory = []

        def monitor():
            while not done.wait(.01):
                try:
                    decision = self.prepare(snapshot_version=self.store.session_version(self.session_id),
                                            provider_wait_s=max(0, deadline - self.clock()))
                except Exception:
                    decision = replace(initial, transition="LAND_CONTINGENCY", reason="SUPERVISION_FAILED",
                                       invalidates_snapshot=True, contingency_id=None)
                if decision.transition != "CONTINUE":
                    mandatory.append(decision)
                    cancel.set()
                    return

        worker = Thread(target=monitor, name="room-safety-watchdog", daemon=True)
        worker.start()
        try:
            waited = SupervisedWait(clock=self.clock).run(operation, deadline, lambda: None, cancellation=cancel)
        finally:
            done.set()
            worker.join()
        latest = mandatory[0] if mandatory else self.prepare(
            snapshot_version=self.store.session_version(self.session_id), provider_wait_s=0, fallback=initial)
        if waited.status in {"DEADLINE_EXCEEDED", "WATCHDOG_FAILED"} and latest.transition == "CONTINUE":
            latest = replace(latest, transition="RETURN_NOW", reason="PROVIDER_DEADLINE_EXCEEDED",
                             invalidates_snapshot=True)
        if latest.transition != "CONTINUE":
            self.last_preemption = latest
            self.dispatch(latest, executor)
            return replace(waited, status="PREEMPTED", value=None)
        return waited

    def _audit(self, outcome):
        payload = asdict(outcome)
        correlation = f"safety:{_sha(payload)}"
        mandatory = outcome.transition != "CONTINUE"
        try:
            append = self.store.append_event_current if mandatory else self.store.append_event
            version = ({"assessed_snapshot_version": outcome.request.snapshot_version} if mandatory
                       else {"expected_version": outcome.request.snapshot_version})
            event = append(session_id=self.session_id, **version, event_type="SAFETY_ASSESSED",
                payload=payload, correlation_id=correlation, causation_id=None, idempotency_key=correlation)
        except VersionConflict as exc:
            if not mandatory:
                raise
            raise _AuditPersistenceFailure(str(exc)) from exc
        except Exception as exc:
            raise _AuditPersistenceFailure(str(exc)) from exc
        if outcome.transition in {"RETURN_NOW", "LAND_CONTINGENCY"}:
            self._returning = True
            self.reserve.latch_return(outcome.energy)
        return event

    def _persist_completion(self, **values):
        if isinstance(values["result"], TerminalOutcome):
            self.last_terminal_outcome = values["result"]
        try:
            return self.store.append_checkpoint_completion(self.session_id, **values)
        except Exception as exc:
            raise _AuditPersistenceFailure(str(exc)) from exc

    def _persistence_emergency(self, executor, error, admitted=None):
        """Keep a local terminal result when the ledger cannot record safety.

        Never execute return motion without its audit. The already configured
        emergency path owns any SDK uncertainty; no persistence success is
        inferred from this local fallback.
        """
        self.last_persistence_error = str(error)
        self._returning = self._terminal = True
        if admitted is not None:
            self.last_preempted_version = admitted.request.snapshot_version
        if self.last_terminal_outcome is None:
            try:
                self.last_terminal_outcome = self.emergency(executor)
            except Exception as exc:
                self.last_persistence_error += f"; emergency failed: {exc}"
                self.last_terminal_outcome = TerminalOutcome(
                    "EMERGENCY_ABORTED", "AUDIT_PERSISTENCE_FAILED", None, None, None)
        result = self.last_terminal_outcome
        if admitted is not None:
            try:
                self._record_completion(result, admitted, None, executor)
            except _AuditPersistenceFailure:
                pass  # Local terminal state remains authoritative for this runtime.
        return result

    def _record_completion(self, result, admitted, event, executor):
        telemetry_error = None
        try:
            pose, sample = self.refresh()
            fresh = self.reserve.ingest_sample(sample)
            if not fresh:
                sample = self.reserve.conservative_sample
                telemetry_error = "ENERGY_SAMPLE_REJECTED"
        except Exception:
            # Retain the last timed observation, visibly stale; never substitute
            # the commanded target or the forecast for a failed sensor read.
            pose, sample = admitted.request.pose, self.reserve.conservative_sample
            fresh = False
            telemetry_error = "TELEMETRY_REFRESH_FAILED"
        terminal = isinstance(result, TerminalOutcome)
        self._terminal = terminal or executor.state in {ExecutorState.TERMINAL, ExecutorState.CLOSED}
        try:
            now = self.clock()
            pose_age = max(0, now - pose.measured_at) if math.isfinite(now) else None
        except Exception:
            pose_age = None
        # Closed/terminal execution without a TerminalOutcome proves that work
        # stopped, not that the aircraft landed or that telemetry is usable.
        unavailable = self._terminal and not terminal
        fresh = fresh and pose_age is not None and not unavailable
        state = {"battery_percent": sample.battery_pct if sample is not None else None, **asdict(pose.position),
            "telemetry_error_category": telemetry_error,
            "heading_deg": pose.heading_deg, "pose_source": pose.source,
            "pose_quality": "MEASURED" if pose.source == "mapped_joint_tags" else "PROPAGATED",
            "pose_age_s": pose_age,
            "localization_state": pose.state.value, "flight_state": executor.state.value,
            "airborne": (False if terminal and result.state in {"LANDED", "NOT_AIRBORNE"}
                         else None if self._terminal else True),
            "terminal_state": result.state if terminal else "ABORTED" if unavailable else None,
            "action_progress": result.state if terminal else result.status,
            "completion_known": result.completed_at is not None if terminal else result.completion_known}
        self._persist_completion(result=result, state=state,
            pose_measured_at=pose.measured_at, energy_measured_at=sample.measured_at_s if sample is not None else None,
            clock_domain_id=self.clock_domain_id, sample_max_age_s=self.reserve.profile.sample_max_age_s,
            fresh=fresh, causation_id=event.event_id if event is not None else None,
            assessed_snapshot_version=admitted.request.snapshot_version)

    def _recover(self, current, executor):
        """Use the cube recovery policy; publish only a timely validated burst."""
        recovery = self.recovery
        if recovery.tracker is not self.tracker:
            raise ValueError("recovery must own the active localization tracker")
        def commit_reanchor(outcome):
            def persist():
                try:
                    self.store.append_reanchor(outcome,
                        expected_version=self.store.session_version(self.session_id))
                except Exception as exc:
                    raise _AuditPersistenceFailure("reanchor persistence failed") from exc
            executor.apply_reanchor(outcome.health, persist=persist)

        recovery.commit_reanchor = commit_reanchor
        navigation = next(e for e in self.site.terminal_contingencies
                          if e.trigger == ContingencyTrigger.NAVIGATION_UNAVAILABLE)
        environment = adapt_recovery_environment(self.site, self._assurances(),
            session_id=self.session_id, clock_domain_id=self.clock_domain_id,
            navigation_contingency_id=navigation.contingency_id, now_monotonic_ms=self.clock() * 1000,
            mapped_heading_tag_ids=self.mapped_heading_tag_ids)
        def stop(reason):
            terminal = replace(current, transition="LAND_CONTINGENCY", reason=reason,
                               invalidates_snapshot=True, contingency_id=None)
            event = self._audit(terminal)
            result = self.emergency(executor)
            self._record_completion(result, terminal, event, executor)
            return result

        if getattr(recovery, "episode_succeeded", False) and not self._begin_recovery_episode(
                current, environment):
            return stop("RECOVERY_EPISODE_NOT_ADMITTED")
        action = recovery.next_action(current.request.pose, environment, ())

        if action.kind in {"CONTINGENCY", "EMERGENCY"}:
            return stop(action.reason)
        deadline = self.clock() + action.upper_bound_s
        if action.kind != "STATIONARY_BURST":
            delta = (action.heading_deg - current.request.pose.heading_deg + 180) % 360 - 180
            rotation_s = rotate_reply_upper_bound_s(degrees=abs(delta),
                base_timeout_s=self.limits.transport_limits.reply_timeout_s)
            if rotation_s >= action.upper_bound_s:
                return stop("RECOVERY_ROTATION_BOUND_UNAVAILABLE")
            rotation = PlannedCommand.from_mapping("ROTATE",
                {"degrees": abs(delta), "direction": "ccw" if delta > 0 else "cw"},
                upper_bound_s=rotation_s, interruptible=False)
            plan = self._plan(current.request.pose, (rotation,), "recovery-yaw")
            current = self.prepare(snapshot_version=self.store.session_version(self.session_id),
                                   checkpoint=plan, fallback=current)
            if current.transition in {"RETURN_NOW", "LAND_CONTINGENCY"}:
                return self.dispatch(current, executor)
            event = self._audit(current)
            result = executor.execute(plan)
            self._record_completion(result, current, event, executor)
            if result.status != "COMPLETED" or not result.completion_known:
                return result
        current = self.prepare(snapshot_version=self.store.session_version(self.session_id), fallback=current)
        if current.transition in {"RETURN_NOW", "LAND_CONTINGENCY"}:
            return self.dispatch(current, executor)
        event = self._audit(current)
        done, cancellation = Event(), Event()
        mandatory = []

        def monitor():
            while not done.wait(.01):
                decision = self.prepare(snapshot_version=self.store.session_version(self.session_id), fallback=current)
                if decision.transition in {"RETURN_NOW", "LAND_CONTINGENCY"}:
                    mandatory.append(decision)
                    cancellation.set()
                    return

        watcher = Thread(target=monitor, name="room-recovery-watchdog", daemon=True)
        watcher.start()
        try:
            waited = SupervisedWait(clock=self.clock).run(lambda cancel: recovery.burst_source(action),
                deadline, lambda: None, cancellation=cancellation)
        finally:
            done.set()
            watcher.join()
        if mandatory:
            return self.dispatch(mandatory[0], executor)
        if waited.status != "COMPLETED":
            return stop("RECOVERY_WAIT_FAILED")
        # Recheck the live session/telemetry before any provider result can
        # advance pose or commit a reanchor event.
        latest = self.prepare(snapshot_version=self.store.session_version(self.session_id), fallback=current)
        if latest.transition in {"RETURN_NOW", "LAND_CONTINGENCY"}:
            return self.dispatch(latest, executor)
        try:
            accepted = recovery.assess_burst(action, waited.value)
        except _AuditPersistenceFailure:
            raise
        except ReanchorPreempted:
            self.tracker.mark_lost()
            return stop("REANCHOR_PREEMPTED_AFTER_PERSISTENCE")
        except Exception:
            self.tracker.mark_lost()
            return stop("REANCHOR_SYNC_FAILED")
        result = CheckpointOutcome("COMPLETED" if accepted.accepted else "RECOVERY_PENDING", (), (), None,
                                   self.tracker.health.position, True)
        self._record_completion(result, current, event, executor)
        return result

    def _begin_recovery_episode(self, current, environment):
        recovery, pose = self.recovery, current.request.pose
        if pose.state == LocalizationState.RECOVERY_REQUIRED:
            return recovery.begin_episode(pose, environment, route_validated=True, energy_validated=True)
        # Preserve the exact unsafe horizon, including provider wait and the
        # reserved return. Cube independently recomputes this projection.
        commands = current.request.checkpoint.commands if current.request.checkpoint else ()
        if current.request.provider_wait_s:
            commands += (PlannedCommand("SETTLE", (), current.request.provider_wait_s, True),)
        optional = self._plan(pose, commands)
        projected = self._forecast(pose, optional, current.assessed_at).projected
        plans = (optional, current.termination_plan,
                 self._plan(pose, commands + self._termination(projected, self.site).commands))
        for plan in plans:
            if plan is None:
                continue
            forecast = self._forecast(pose, plan, current.assessed_at)
            if forecast.admitted or forecast.projected.state != LocalizationState.RECOVERY_REQUIRED:
                continue
            assessment = self._audit(current)
            evidence = RecoveryForecastEvidence(self.session_id, self.clock_domain_id,
                assessment.event_id, current.assessed_at, pose, plan, forecast)
            correlation = f"recovery-forecast:{assessment.event_id}"
            try:
                self.store.append_event_current(session_id=self.session_id,
                    assessed_snapshot_version=current.request.snapshot_version,
                    event_type="RECOVERY_FORECAST_ADMITTED", payload=asdict(evidence),
                    correlation_id=correlation, causation_id=assessment.event_id, idempotency_key=correlation)
            except Exception as exc:
                raise _AuditPersistenceFailure("recovery forecast persistence failed") from exc
            # Only this durably recorded object has authority for this call;
            # neither a reconstructed payload nor a later replay can reset it.
            previous = recovery.forecast_authorized
            recovery.forecast_authorized = lambda supplied: supplied is evidence
            try:
                return recovery.begin_episode(pose, environment, route_validated=True,
                    energy_validated=True, forecast_evidence=evidence)
            finally:
                recovery.forecast_authorized = previous
        return False

    def dispatch(self, outcome: SupervisorOutcome, executor) -> CheckpointOutcome | TerminalOutcome:
        with self._dispatch_lock:
            try:
                return self._dispatch(outcome, executor)
            except _AuditPersistenceFailure as exc:
                return self._persistence_emergency(executor, exc, outcome)

    def _dispatch(self, outcome: SupervisorOutcome, executor) -> CheckpointOutcome | TerminalOutcome:
        """Revalidate fresh input, persist preemption, then use the executor lock.

        A command completing with SDK uncertainty is returned unchanged. No
        replacement motion is attempted; StepExecutor owns its terminal path.
        """
        with self._dispatch_lock:
            self._bind_home(executor)
            if self._terminal:
                raise RuntimeError("supervisor is terminal")
            if (outcome.transition == "CONTINUE"
                    and outcome.request.snapshot_version != self.store.session_version(self.session_id)):
                raise VersionConflict("optional command snapshot is stale")
            if outcome.site_sha256 != _sha(room_site_document(self.site)):
                raise VersionConflict("site changed after assessment")
            if outcome.transition in {"RETURN_NOW", "LAND_CONTINGENCY"}:
                # A newer pose/sample can strengthen a mandatory preemption,
                # but cannot erase an already selected return obligation.
                self._audit(outcome)
            current = self.prepare(snapshot_version=self.store.session_version(self.session_id),
                checkpoint=outcome.request.checkpoint, provider_wait_s=outcome.request.provider_wait_s, fallback=outcome)
            if outcome.transition == "RETURN_NOW" and current.transition == "CONTINUE":
                current = replace(current, transition="RETURN_NOW", reason=outcome.reason, invalidates_snapshot=True)
            elif outcome.reason == "SUPERVISION_FAILED":
                current = replace(current, transition="LAND_CONTINGENCY", reason=outcome.reason,
                                  invalidates_snapshot=True, contingency_id=None)
            states, observations = [], []
            if current.transition == "LAND_CONTINGENCY":
                event = self._audit(current)
                entry = next((e for e in self.site.terminal_contingencies if e.contingency_id == current.contingency_id), None)
                result = (self._land(executor) if entry is not None and entry.kind != ContingencyKind.EMERGENCY
                          else self.emergency(executor))
                self._record_completion(result, current, event, executor)
                return result
            if current.transition == "RECOVER":
                if self.recovery is not None:
                    return self._recover(current, executor)
                current = replace(current, transition="LAND_CONTINGENCY", reason="RECOVERY_UNAVAILABLE",
                                  invalidates_snapshot=True, contingency_id=None)
                event = self._audit(current)
                result = self.emergency(executor)
                self._record_completion(result, current, event, executor)
                return result
            elif current.transition == "RETURN_NOW":
                plan = current.termination_plan
            else:
                plan = current.request.checkpoint or self._plan(current.request.pose, ())
            remaining = list(plan.commands)
            if not remaining:
                raise ValueError("dispatch requires a checkpoint; use supervised waits for providers")
            while remaining:
                cmd = remaining.pop(0)
                if cmd.kind == "LAND":
                    # Landing is a new physical admission, including the entire
                    # descent footprint and its current session assurance.
                    current = self.prepare(snapshot_version=self.store.session_version(self.session_id), fallback=current)
                    eligible = None
                    if current.reason not in {"SITE_OR_CAPABILITY_INVALID", "TELEMETRY_INVALID", "TELEMETRY_UNAVAILABLE", "LOCALIZATION_LOST"}:
                        landing = self._landing_forecast(current.request.pose, self.clock())
                        current = replace(current, pose_forecast=landing)
                        descent_energy = self._landing_energy(copy(self.reserve), current.request)
                        eligible = self._landing_contingency(self.site, landing.projected, now=self.clock(),
                            trigger=ContingencyTrigger.RETURN_ENERGY_UNAVAILABLE,
                            energy_available=descent_energy.decision != ReserveDecision.LAND_CONTINGENCY,
                            home=plan.checkpoint_id == self.site.home.route_id, require_descent=True)
                    current = replace(current, transition="LAND_CONTINGENCY", invalidates_snapshot=True,
                                      reason="LANDING_APPROACH", contingency_id=eligible.contingency_id if eligible else None)
                event = self._audit(current)
                if cmd.kind == "LAND":
                    result = (self._land(executor) if eligible is not None and eligible.kind != ContingencyKind.EMERGENCY
                              else self.emergency(executor))
                else:
                    part = self._plan(current.request.pose, (cmd,), plan.checkpoint_id)
                    result = executor.return_home(part) if current.transition == "RETURN_NOW" else executor.execute(part)
                self._record_completion(result, current, event, executor)
                if isinstance(result, TerminalOutcome):
                    return result
                states.extend(result.command_states)
                observations.extend(result.observation_ids)
                if result.status != "COMPLETED" or not result.completion_known:
                    return replace(result, command_states=tuple(states), observation_ids=tuple(observations))
                if not remaining:
                    return replace(result, command_states=tuple(states), observation_ids=tuple(observations))
                refreshed = self.prepare(snapshot_version=self.store.session_version(self.session_id),
                    remaining_commands=remaining, fallback=current)
                if refreshed.transition != current.transition:
                    return self.dispatch(refreshed, executor)
                current = refreshed
