"""Calibrated pose propagation and pre-command admission, without aircraft I/O.

Times here are seconds on one injected monotonic clock. A profile is calibration
evidence, not an authorization to fly; the room capability gate remains separate.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, replace
from enum import StrEnum
import math
from typing import Callable

from sentinel.models import Position
from .checkpoint import CheckpointPlan, PlannedCommand


class LocalizationState(StrEnum):
    ANCHORED = "ANCHORED"
    COASTING = "COASTING"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    LOST = "LOST"


@dataclass(frozen=True)
class DriftProfile:
    schema_version: str
    profile_id: str
    validated: bool
    evidence_refs: tuple[str, ...]
    max_camera_age_s: float
    max_fix_age_s: float
    max_estimator_failures: int
    max_look_away_s: float
    max_translation_cm: float
    max_yaw_deg: float
    max_xy_uncertainty_cm: float
    max_heading_uncertainty_deg: float
    xy_drift_cm_s: float
    xy_drift_per_cm: float
    xy_drift_per_yaw_deg: float
    heading_drift_deg_s: float
    heading_drift_per_cm: float
    heading_drift_per_yaw_deg: float
    max_fix_age_on_arrival_s: float
    max_fix_jump_cm: float
    max_fix_heading_jump_deg: float
    recovery_deadline_s: float
    max_recovery_attempts: int
    min_burst_reports: int
    max_burst_spread_cm: float
    max_burst_heading_spread_deg: float
    max_height_error_cm: float
    rotation_radius_cm: float
    recovery_action_upper_bound_s: float
    canonical_heading_tolerance_deg: float

    def __post_init__(self):
        if self.schema_version != "pose-drift-v1" or not self.profile_id:
            raise ValueError("unsupported or unidentified drift profile")
        if type(self.validated) is not bool or any(not isinstance(ref, str) or not ref.strip() for ref in self.evidence_refs):
            raise ValueError("profile validation and evidence must be explicit")
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name in {"schema_version", "profile_id", "validated", "evidence_refs"}:
                continue
            if isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid drift profile {field.name}")
            if "drift" not in field.name and value == 0:
                raise ValueError(f"drift limit {field.name} must be positive")
        if any(type(getattr(self, name)) is not int for name in
               ("max_estimator_failures", "max_recovery_attempts", "min_burst_reports")):
            raise ValueError("report and attempt limits must be integers")
        if self.min_burst_reports < 2:
            raise ValueError("recovery needs distinct reports")


@dataclass(frozen=True)
class PoseFix:
    position: Position
    heading_deg: float
    measured_at: float
    xy_uncertainty_cm: float
    heading_uncertainty_deg: float
    tag_ids: tuple[int, ...]
    source: str = "mapped_joint_tags"


@dataclass(frozen=True)
class PoseHealth:
    state: LocalizationState
    position: Position
    heading_deg: float
    measured_at: float
    camera_at: float
    propagated_at: float
    camera_age_s: float = 0
    fix_age_s: float = 0
    estimator_failures: int = 0
    look_away_s: float = 0
    translation_cm: float = 0
    yaw_deg: float = 0
    xy_uncertainty_cm: float = 0
    heading_uncertainty_deg: float = 0
    pose_revision: int = 0
    source: str = "uninitialized"
    requires_route_validation: bool = False
    requires_energy_validation: bool = False
    intentional_look_away: bool = False


@dataclass(frozen=True)
class PoseForecast:
    admitted: bool
    projected: PoseHealth
    reasons: tuple[str, ...]


def heading_distance(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def finite_number(value: object) -> bool:
    """Reject malformed sensor numerics without coercing strings or booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def valid_fix(fix: PoseFix) -> bool:
    if not isinstance(fix, PoseFix) or not isinstance(fix.position, Position):
        return False
    values = (fix.position.x_cm, fix.position.y_cm, fix.position.z_cm,
              fix.heading_deg, fix.measured_at, fix.xy_uncertainty_cm, fix.heading_uncertainty_deg)
    return (all(finite_number(value) for value in values)
            and fix.xy_uncertainty_cm >= 0 and fix.heading_uncertainty_deg >= 0
            and fix.measured_at >= 0 and isinstance(fix.source, str) and bool(fix.source.strip())
            and isinstance(fix.tag_ids, tuple)
            and all(type(tag) is int and tag >= 0 for tag in fix.tag_ids))


class LocalizationTracker:
    def __init__(self, profile: DriftProfile | None, *, clock: Callable[[], float],
                 real_mode: bool = True, canonical_heading_deg: float = 90):
        self.profile = profile
        self.clock = clock
        self.real_mode = real_mode
        self.canonical_heading_deg = canonical_heading_deg
        now = clock()
        self._health = PoseHealth(LocalizationState.LOST, Position(), canonical_heading_deg,
                                  now, now, now)
        self._initialized = False

    @property
    def initialized(self) -> bool:
        """True once any fix has anchored the tracker (a later LOST stays latched)."""
        return self._initialized

    @property
    def health(self) -> PoseHealth:
        self._health = self._advance(self._health, self.clock())
        return self._health

    def mark_lost(self) -> PoseHealth:
        """Latch a verified integrity contradiction until a new tracker is bootstrapped."""
        self._health = replace(self.health, state=LocalizationState.LOST)
        return self._health

    def acknowledge_validation(self, pose_revision: int, *, route_validated: bool,
                               energy_validated: bool) -> PoseHealth:
        """Clear reanchor prerequisites only for the freshly admitted revision."""
        current = self.health
        if (route_validated is not True or energy_validated is not True
                or pose_revision != current.pose_revision
                or current.state not in {LocalizationState.ANCHORED, LocalizationState.COASTING}):
            raise ValueError("fresh route and energy validation must match the current pose")
        self._health = replace(current, requires_route_validation=False, requires_energy_validation=False)
        return self._health

    def _advance(self, health: PoseHealth, now: float) -> PoseHealth:
        if not math.isfinite(now) or now < health.propagated_at:
            return replace(health, state=LocalizationState.LOST)
        dt = now - health.propagated_at
        profile = self.profile
        health = replace(health, propagated_at=now, camera_age_s=now - health.camera_at,
            fix_age_s=now - health.measured_at,
            look_away_s=health.look_away_s + (dt if health.intentional_look_away
                or heading_distance(health.heading_deg, self.canonical_heading_deg) > 1e-6 else 0),
            xy_uncertainty_cm=health.xy_uncertainty_cm + (dt * profile.xy_drift_cm_s if profile else 0),
            heading_uncertainty_deg=health.heading_uncertainty_deg + (dt * profile.heading_drift_deg_s if profile else 0))
        if health.state != LocalizationState.LOST and self._limits(health):
            health = replace(health, state=LocalizationState.RECOVERY_REQUIRED)
        return health

    def _limits(self, health: PoseHealth) -> tuple[str, ...]:
        if self.profile is None:
            return ()
        dimensions = ("camera_age_s", "fix_age_s", "estimator_failures", "look_away_s",
                      "translation_cm", "yaw_deg", "xy_uncertainty_cm", "heading_uncertainty_deg")
        names = ("camera_age", "fix_age", "estimator_failures", "look_away", "translation", "yaw",
                 "xy_uncertainty", "heading_uncertainty")
        return tuple(name for field, name in zip(dimensions, names)
                     if getattr(health, field) >= getattr(self.profile, "max_" + field))

    def accept_fix(self, fix: PoseFix) -> PoseHealth:
        now = self.clock()
        health = self._advance(self._health, now)
        profile = self.profile
        if self._initialized and health.state == LocalizationState.LOST:
            self._health = health
            return health
        age_limit = profile.max_fix_age_on_arrival_s if profile else 0
        if (not valid_fix(fix) or fix.source != "mapped_joint_tags" or len(set(fix.tag_ids)) < 2
                or fix.measured_at > now or now - fix.measured_at > age_limit
                or (self._initialized and fix.measured_at < health.measured_at)):
            self._health = replace(health, state=LocalizationState.RECOVERY_REQUIRED)
            return self._health
        if self._initialized and profile and (
                math.dist((health.position.x_cm, health.position.y_cm),
                          (fix.position.x_cm, fix.position.y_cm)) > profile.max_fix_jump_cm
                or abs(fix.position.z_cm - health.position.z_cm) > profile.max_height_error_cm
                or heading_distance(health.heading_deg, fix.heading_deg) > profile.max_fix_heading_jump_deg):
            self._health = replace(health, state=LocalizationState.LOST)
            return self._health
        self._health = self._anchored(fix, now, invalidate=self._initialized)
        self._initialized = True
        return self._health

    def _anchored(self, fix: PoseFix, now: float, *, invalidate: bool) -> PoseHealth:
        health = PoseHealth(LocalizationState.ANCHORED, fix.position, fix.heading_deg % 360,
            fix.measured_at, fix.measured_at, fix.measured_at,
            xy_uncertainty_cm=fix.xy_uncertainty_cm, heading_uncertainty_deg=fix.heading_uncertainty_deg,
            pose_revision=self._health.pose_revision + 1, source=fix.source,
            requires_route_validation=invalidate, requires_energy_validation=invalidate)
        return self._advance(health, now)

    def note_camera(self, captured_at: float, *, estimator_failed: bool = False,
                    intentional_look_away: bool = False) -> PoseHealth:
        health = self.health
        if not math.isfinite(captured_at) or not health.camera_at <= captured_at <= self.clock():
            return health
        state = health.state if health.state in {LocalizationState.LOST, LocalizationState.RECOVERY_REQUIRED} else LocalizationState.COASTING
        health = replace(health, state=state, camera_at=captured_at,
                         camera_age_s=self.clock() - captured_at,
                         intentional_look_away=intentional_look_away,
                         estimator_failures=health.estimator_failures + int(estimator_failed and not intentional_look_away))
        self._health = self._advance(health, self.clock())
        return self._health

    def _command(self, health: PoseHealth, command: PlannedCommand) -> PoseHealth:
        args = dict(command.arguments)
        distance = yaw = 0.0
        position, heading = health.position, health.heading_deg
        if command.kind == "MOVE":
            distance = float(args["distance_cm"])
            direction = str(args["direction"])
            if not math.isfinite(distance) or distance < 0:
                raise ValueError("invalid command distance")
            if direction in {"up", "down"}:
                position = Position(position.x_cm, position.y_cm,
                                    position.z_cm + distance * (1 if direction == "up" else -1))
            else:
                offset = {"forward": 0, "back": 180, "right": -90, "left": 90}[direction]
                angle = math.radians(heading + offset)
                position = Position(position.x_cm + distance * math.cos(angle),
                                    position.y_cm + distance * math.sin(angle), position.z_cm)
        elif command.kind == "ROTATE":
            yaw = float(args["degrees"])
            if not math.isfinite(yaw) or yaw < 0 or args["direction"] not in {"cw", "ccw"}:
                raise ValueError("invalid rotation")
            heading = (heading + yaw * (1 if args["direction"] == "ccw" else -1)) % 360
        elif command.kind == "TRIM_ALLOWANCE":
            distance = 2 * float(args["max_rounds"]) * float(args["max_distance_cm"])
            yaw = float(args["max_rounds"]) * float(args["max_yaw_deg"])
        profile = self.profile
        away = (args.get("phase") == "look" or heading_distance(heading, self.canonical_heading_deg) > 1e-6
                or heading_distance(health.heading_deg, self.canonical_heading_deg) > 1e-6)
        already_away = health.intentional_look_away or heading_distance(health.heading_deg, self.canonical_heading_deg) > 1e-6
        # A heading error rotates the commanded displacement. Bound the chord
        # at the largest heading error over the leg, in addition to measured drift.
        heading_bound = health.heading_uncertainty_deg + (distance * profile.heading_drift_per_cm if profile else 0)
        # A heading error rotates only a horizontal displacement: a vertical
        # climb or descent keeps its x/y whatever the yaw (Task B2).
        horizontal = 0.0 if command.kind == "MOVE" and args.get("direction") in {"up", "down"} else distance
        bearing_error = 2 * horizontal * math.sin(math.radians(min(180, heading_bound)) / 2)
        state = health.state if health.state in {LocalizationState.LOST, LocalizationState.RECOVERY_REQUIRED} else LocalizationState.COASTING
        return replace(health, state=state, position=position, heading_deg=heading,
            translation_cm=health.translation_cm + distance, yaw_deg=health.yaw_deg + yaw,
            look_away_s=health.look_away_s + (command.upper_bound_s if away and not already_away else 0),
            xy_uncertainty_cm=health.xy_uncertainty_cm + bearing_error + (distance * profile.xy_drift_per_cm + yaw * profile.xy_drift_per_yaw_deg if profile else 0),
            heading_uncertainty_deg=health.heading_uncertainty_deg + (distance * profile.heading_drift_per_cm + yaw * profile.heading_drift_per_yaw_deg if profile else 0))

    def note_command(self, command: PlannedCommand, completed_at: float) -> PoseHealth:
        health = self._advance(self._health, completed_at)
        if health.state == LocalizationState.LOST and command.kind in {"MOVE", "ROTATE", "TRIM_ALLOWANCE"}:
            self._health = health
            raise ValueError("LOST prohibits translation and exploratory yaw")
        self._health = self._advance(self._command(health, command), completed_at)
        return self._health

    def forecast(self, plan: CheckpointPlan, now: float) -> PoseForecast:
        self._health = self._advance(self._health, now)
        projected = self._health
        reasons = set(self._limits(projected))
        if projected.state == LocalizationState.LOST:
            reasons.add("lost")
        elif projected.state == LocalizationState.RECOVERY_REQUIRED:
            reasons.add("recovery_required")
        if self.profile is None or (self.real_mode and not (self.profile.validated and self.profile.evidence_refs)):
            reasons.add("validated_drift_profile")
        if projected.requires_route_validation:
            reasons.add("fresh_route_validation")
        if projected.requires_energy_validation:
            reasons.add("fresh_energy_validation")
        for command in plan.commands:
            if not math.isfinite(command.upper_bound_s) or command.upper_bound_s < 0:
                reasons.add("invalid_duration")
                break
            now += command.upper_bound_s
            projected = self._advance(self._command(self._advance(projected, now), command), now)
            reasons.update(self._limits(projected))
        recovery_reasons = reasons - {"fresh_route_validation", "fresh_energy_validation"}
        if recovery_reasons and self._health.state != LocalizationState.LOST:
            self._health = replace(self._health, state=LocalizationState.RECOVERY_REQUIRED)
            projected = replace(projected, state=LocalizationState.RECOVERY_REQUIRED)
        return PoseForecast(not reasons, projected, tuple(sorted(reasons)))
