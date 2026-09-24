"""Finite stationary recovery. This module deliberately has no room dependency.

Actions describe work for a supervised executor; they never issue aircraft I/O.
Yaw admission uses a complete swept disk, including propagated position error.
"""
from __future__ import annotations

from copy import copy
from dataclasses import asdict, dataclass, replace
import math
from typing import Callable
import uuid

from sentinel.models import Position
from .checkpoint import CheckpointPlan, PlannedCommand
from .localization import (
    LocalizationState, LocalizationTracker, PoseFix, PoseForecast, PoseHealth, finite_number, heading_distance, valid_fix,
)


def normalize_mapped_heading_tag_ids(values: object) -> tuple[tuple[float, tuple[int, ...]], ...]:
    """Validate the map before hashing/arithmetic and freeze one canonical form.

    A tag can be visible from several headings, but identities within a heading
    and headings modulo one revolution must be unique.
    """
    if type(values) is not tuple or not values:
        raise ValueError("mapped recovery headings require a nonempty tuple")
    normalized = []
    headings = set()
    for item in values:
        if type(item) is not tuple or len(item) != 2:
            raise ValueError("mapped recovery heading must be a heading/tag tuple")
        heading, tags = item
        if type(heading) not in (int, float) or not finite_number(heading):
            raise ValueError("mapped recovery heading must be a finite number")
        # Reduce integers before conversion to preserve their exact angle.
        # A tiny negative float can round modulo to 360; fold that back to zero.
        heading = float(heading % 360) % 360.0
        if heading in headings:
            raise ValueError("duplicate mapped recovery heading")
        if (type(tags) is not tuple or not tags
                or any(type(tag) is not int or tag < 0 for tag in tags)):
            raise ValueError("mapped recovery tags require nonnegative integer identities")
        if len(set(tags)) != len(tags):
            raise ValueError("duplicate mapped recovery tag identity")
        headings.add(heading)
        normalized.append((heading, tuple(sorted(tags))))
    return tuple(sorted(normalized))


#: A single-tag fix (``PoseFix.source``) re-anchors only an estimate it agrees
#: with, within these; anchoring from nothing still needs two tags.
SINGLE_TAG_SOURCE = "mapped_single_tag"
SINGLE_TAG_MAX_XY_CM = 40.0
SINGLE_TAG_MAX_HEADING_DEG = 15.0


@dataclass(frozen=True)
class RecoveryVolume:
    volume_id: str
    polygon_xy_cm: tuple[tuple[float, float], ...]
    z_interval_cm: tuple[float, float]


@dataclass(frozen=True)
class RecoveryEnvironment:
    session_id: str
    clock_domain_id: str
    mapped_heading_tag_ids: tuple[tuple[float, tuple[int, ...]], ...]
    rotation_clearance_volumes: tuple[RecoveryVolume, ...]
    navigation_contingency_id: str
    navigation_contingency_expires_monotonic_ms: int

    def __post_init__(self):
        object.__setattr__(self, "mapped_heading_tag_ids",
                           normalize_mapped_heading_tag_ids(self.mapped_heading_tag_ids))


def _canonical_report_id(value: object) -> str:
    if type(value) is not str:
        raise ValueError("report identity must be a string")
    canonical = value.strip()
    if not canonical or canonical != value:
        raise ValueError("report identity must be nonblank and normalized")
    return canonical


@dataclass(frozen=True)
class PoseReport:
    report_id: str
    fix: PoseFix

    def __post_init__(self):
        object.__setattr__(self, "report_id", _canonical_report_id(self.report_id))


@dataclass(frozen=True)
class RecoveryAction:
    action_id: str
    kind: str
    heading_deg: float | None = None
    contingency_id: str | None = None
    reason: str = ""
    upper_bound_s: float = 0.0
    pose_revision: int | None = None
    measured_at: float | None = None


@dataclass(frozen=True)
class RecoveryOutcome:
    accepted: bool
    reason: str
    session_id: str
    clock_domain_id: str
    events: tuple[dict, ...] = ()
    health: PoseHealth | None = None


@dataclass(frozen=True)
class RecoveryForecastEvidence:
    """Audited supervision input; a forecast is not itself reset authority."""

    session_id: str
    clock_domain_id: str
    assessment_event_id: str
    assessed_at: float
    pose: PoseHealth
    plan: CheckpointPlan
    forecast: PoseForecast
    validated_pose_revision: int | None = None
    route_validated: bool = False
    energy_validated: bool = False


def contains_disk(volume: RecoveryVolume, position: Position, radius_cm: float) -> bool:
    """Contain the entire horizontal disk, not merely a sampled set of headings."""
    if (not math.isfinite(radius_cm) or radius_cm < 0
            or not volume.z_interval_cm[0] <= position.z_cm <= volume.z_interval_cm[1]):
        return False
    polygon = volume.polygon_xy_cm
    if len(polygon) < 3:
        return False
    x, y = position.x_cm, position.y_cm
    inside = False
    boundary = False
    for (ax, ay), (bx, by) in zip(polygon, polygon[1:] + polygon[:1]):
        dx, dy = bx - ax, by - ay
        length_sq = dx * dx + dy * dy
        if length_sq == 0:
            return False
        t = min(1.0, max(0.0, ((x - ax) * dx + (y - ay) * dy) / length_sq))
        distance = math.hypot(x - ax - t * dx, y - ay - t * dy)
        if distance + 1e-9 < radius_cm:
            return False
        boundary |= distance < 1e-9
        if (ay > y) != (by > y) and x < ax + (y - ay) * dx / dy:
            inside = not inside
    return inside or boundary


class RecoveryController:
    """Bounded recovery with an optional trusted supervision authorizer.

    ``forecast_authorized`` belongs to the supervising adapter. It must bind
    evidence to a durable admission and reject reconstructed or replayed input;
    callers cannot enable proactive recovery by supplying a forecast alone.
    """

    def __init__(self, tracker: LocalizationTracker, *, clock: Callable[[], float],
                 burst_source: Callable[[RecoveryAction], tuple[PoseReport, ...]],
                 energy_admits: Callable[[RecoveryAction], bool],
                 commit_reanchor: Callable[[RecoveryOutcome], object] | None = None,
                 forecast_authorized: Callable[[RecoveryForecastEvidence], bool] | None = None):
        self.tracker = tracker
        self.clock = clock
        self.burst_source = burst_source
        self.energy_admits = energy_admits
        self.commit_reanchor = commit_reanchor
        self.forecast_authorized = forecast_authorized
        self.max_attempts = tracker.profile.max_recovery_attempts if tracker.profile else 0
        #: Opt-in search sweep (live): after the mapped headings, yaw bursts at
        #: these offsets from the canonical heading, each re-anchoring on any
        #: mapped tag; its own attempt count and extra deadline (stationary
        #: rotations only -- a failed search still ends in the contingency).
        self.search_offsets_deg: tuple[float, ...] = ()
        self.search_extra_s: float = 0.0
        self._search_headings: set[float] = set()
        self._started_at: float | None = None
        self._last_now: float | None = None
        self._environment: RecoveryEnvironment | None = None
        self._pending: RecoveryAction | None = None
        self._pending_at: float | None = None
        self._pose: PoseHealth | None = None
        self._attempts = 0
        self._tried: set[float] = set()
        self._restore = False
        self._finished = False
        self._clock_failed = False
        self._accounted_rotations: set[str] = set()
        self._successful_revision: int | None = None
        self._resumed_revision: int | None = None
        self._completed_reanchors: set[str] = set()

    @property
    def episode_succeeded(self) -> bool:
        return self._finished and self._successful_revision is not None

    @property
    def resume_required(self) -> bool:
        return self.episode_succeeded and self._resumed_revision is None

    def confirm_resume(self, pose: PoseHealth, *, route_validated: bool, energy_validated: bool) -> PoseHealth:
        """Acknowledge fresh admission after success, before any later episode."""
        if self.episode_succeeded and self._resumed_revision is not None:
            raise ValueError("recovery episode already resumed")
        current = self.tracker.health
        self._clock()
        if (not self.episode_succeeded or self._clock_failed
                or route_validated is not True or energy_validated is not True
                or pose.pose_revision != current.pose_revision
                or current.pose_revision < self._successful_revision
                or current.state not in {LocalizationState.ANCHORED, LocalizationState.COASTING}):
            raise ValueError("recovery resume requires fresh route and energy validation")
        self.tracker.acknowledge_validation(current.pose_revision,
            route_validated=route_validated, energy_validated=energy_validated)
        self._resumed_revision = current.pose_revision
        return self.tracker.health

    def _valid_forecast_evidence(self, evidence, current, environment, now) -> bool:
        if not isinstance(evidence, RecoveryForecastEvidence) or self.forecast_authorized is None:
            return False
        try:
            if (self.forecast_authorized(evidence) is not True
                    or evidence.session_id != environment.session_id
                    or evidence.clock_domain_id != environment.clock_domain_id
                    or not evidence.assessment_event_id
                    or evidence.validated_pose_revision != current.pose_revision
                    or evidence.route_validated is not True or evidence.energy_validated is not True
                    or not finite_number(evidence.assessed_at)
                    or not 0 <= now - evidence.assessed_at < self.tracker.profile.recovery_action_upper_bound_s
                    or evidence.pose.state not in {LocalizationState.ANCHORED, LocalizationState.COASTING}
                    or any(getattr(evidence.pose, field) != getattr(current, field) for field in
                           ("pose_revision", "measured_at", "camera_at", "position", "heading_deg"))
                    or evidence.plan.start != evidence.pose.position
                    or evidence.plan.start_heading_deg != evidence.pose.heading_deg
                    or evidence.forecast.admitted
                    or evidence.forecast.projected.state != LocalizationState.RECOVERY_REQUIRED):
                return False
            tracker = copy(self.tracker)
            tracker._health = evidence.pose
            return tracker.forecast(evidence.plan, evidence.assessed_at) == evidence.forecast
        except Exception:
            return False

    def begin_episode(self, pose: PoseHealth, environment: RecoveryEnvironment, *,
                      route_validated: bool, energy_validated: bool,
                      forecast_evidence: RecoveryForecastEvidence | None = None) -> bool:
        """Reset only a successfully resumed episode on a later recovery need.

        Exhaustion, clock/session poisoning, and audit identities survive. An
        active or exhausted episode cannot acquire a fresh deadline or attempts.
        """
        now = self._clock()
        if self._environment is not None and (
                environment.session_id != self._environment.session_id
                or environment.clock_domain_id != self._environment.clock_domain_id):
            self._clock_failed = True
        current = self.tracker.health
        if (not self.episode_succeeded or self._clock_failed
                or route_validated is not True or energy_validated is not True
                or current.state == LocalizationState.LOST
                or pose.pose_revision != current.pose_revision
                or self._resumed_revision is None or current.pose_revision < self._resumed_revision
                or now * 1000 >= environment.navigation_contingency_expires_monotonic_ms):
            return False
        if current.state != LocalizationState.RECOVERY_REQUIRED:
            if not self._valid_forecast_evidence(forecast_evidence, current, environment, now):
                return False
            # The authorized, durable admission validates this exact revision.
            # Do not require an unrelated CONTINUE between a new fix and its
            # proactive recovery; reject a concurrent revision change instead.
            try:
                self.tracker.acknowledge_validation(forecast_evidence.validated_pose_revision,
                    route_validated=forecast_evidence.route_validated,
                    energy_validated=forecast_evidence.energy_validated)
            except ValueError:
                return False
        elif current.requires_route_validation or current.requires_energy_validation:
            return False
        environment = replace(environment)
        self._started_at = self._pending_at = self._pose = self._pending = None
        self._attempts = 0
        self._tried.clear()
        self._restore = self._finished = False
        self._successful_revision = self._resumed_revision = None
        self._environment = environment
        return True

    def _clock(self) -> float:
        now = self.clock()
        if not math.isfinite(now) or (self._last_now is not None and now < self._last_now):
            self._clock_failed = True
        self._last_now = now
        return now

    def _terminal(self, environment: RecoveryEnvironment, reason: str) -> RecoveryAction:
        self._pending = None
        self._finished = True
        self._successful_revision = self._resumed_revision = None
        current = (not self._clock_failed and self._last_now is not None
                   and self._last_now * 1000 < environment.navigation_contingency_expires_monotonic_ms)
        return RecoveryAction(str(uuid.uuid4()), "CONTINGENCY" if current else "EMERGENCY",
                              contingency_id=environment.navigation_contingency_id if current else None,
                              reason=reason)

    def _can_recover(self, pose: PoseHealth, environment: RecoveryEnvironment,
                     action: RecoveryAction) -> bool:
        profile = self.tracker.profile
        if profile is None:
            return False
        if action.kind == "STATIONARY_BURST":
            command = PlannedCommand.from_mapping("SETTLE", {}, upper_bound_s=action.upper_bound_s,
                                                  interruptible=True)
        else:
            delta = (action.heading_deg - pose.heading_deg + 180) % 360 - 180
            command = PlannedCommand.from_mapping("ROTATE", {"degrees": abs(delta), "direction": "ccw" if delta > 0 else "cw"},
                                                  upper_bound_s=action.upper_bound_s, interruptible=False)
        projected = self.tracker._advance(pose, self._last_now + action.upper_bound_s)
        projected = self.tracker._command(projected, command)
        # Estimator failures are why recovery was requested; they cannot veto
        # the attempt that resolves them. Physical drift limits still apply.
        limits = set(self.tracker._limits(projected)) - {"estimator_failures"}
        return bool(profile and pose.state != LocalizationState.LOST
                    and not limits
                    and any(volume.z_interval_cm[0] <= projected.position.z_cm - profile.max_height_error_cm
                            and projected.position.z_cm + profile.max_height_error_cm <= volume.z_interval_cm[1]
                            and contains_disk(volume, projected.position,
                            profile.rotation_radius_cm + projected.xy_uncertainty_cm)
                            for volume in environment.rotation_clearance_volumes)
                    and self.energy_admits(action) is True)

    def next_action(self, pose: PoseHealth, environment: RecoveryEnvironment,
                    attempted_headings: tuple[int, ...]) -> RecoveryAction:
        now = self._clock()
        if self._environment is not None and (
            environment.session_id != self._environment.session_id
            or environment.clock_domain_id != self._environment.clock_domain_id
        ):
            self._clock_failed = True
            return self._terminal(environment, "session_or_clock_changed")
        try:
            # Revalidate restored objects and keep a private immutable snapshot.
            environment = replace(environment)
        except ValueError:
            return self._terminal(environment, "invalid_mapped_heading_tags")
        self._environment = environment
        current = self.tracker.health
        if (pose.state == LocalizationState.LOST or current.state == LocalizationState.LOST
                or current.pose_revision != pose.pose_revision):
            return self._terminal(environment, "lost_or_stale_pose")
        # Age and accumulated uncertainty must come from the tracker at admission.
        pose = current
        superseded = (self._pending is not None and (
            self._pending.pose_revision != current.pose_revision
            or self._pending.measured_at != current.measured_at
            or now - self._pending_at >= self._pending.upper_bound_s
            or not self._can_recover(current, environment, self._pending)))
        if superseded:
            self._pending = None
            self._pending_at = None
            self._pose = None
            self._restore = False
            # Recheck a stationary attempt after any invalidated admission.
            # Retain the original deadline and attempt count: a changed pose,
            # expired action or revoked clearance cannot extend recovery.
        if self._started_at is None:
            self._started_at = now
        profile = self.tracker.profile
        if self._clock_failed or now * 1000 >= environment.navigation_contingency_expires_monotonic_ms:
            return self._terminal(environment, "clearance_expired_or_clock_reset")
        if pose.state == LocalizationState.LOST:
            return self._terminal(environment, "lost")
        if profile is None or (self.tracker.real_mode and not (profile.validated and profile.evidence_refs)):
            return self._terminal(environment, "validated_drift_profile_required")
        horizon = now + profile.recovery_action_upper_bound_s
        searching = bool(self.search_offsets_deg)
        deadline_s = profile.recovery_deadline_s + (self.search_extra_s if searching else 0.0)
        attempts = self.max_attempts + (len(self.search_offsets_deg) if searching else 0)
        if (self._finished or horizon - self._started_at >= deadline_s
                or horizon * 1000 >= environment.navigation_contingency_expires_monotonic_ms
                or self._attempts >= attempts):
            return self._terminal(environment, "recovery_exhausted")
        if self._pending is not None:
            return self._pending

        def make_action(kind, heading):
            return RecoveryAction(str(uuid.uuid4()), kind, heading,
                                  upper_bound_s=profile.recovery_action_upper_bound_s,
                                  pose_revision=pose.pose_revision, measured_at=pose.measured_at)

        if self._attempts == 0 or superseded:
            action = make_action("STATIONARY_BURST", pose.heading_deg)
            if not self._can_recover(pose, environment, action):
                return self._terminal(environment, "stationary_recovery_unavailable")
        elif self._restore:
            action = make_action("RESTORE_CANONICAL", self.tracker.canonical_heading_deg)
            if not self._can_recover(pose, environment, action):
                return self._terminal(environment, "canonical_restoration_unavailable")
        else:
            tried = self._tried | set(attempted_headings)
            ranked = sorted(environment.mapped_heading_tag_ids, key=lambda item: (-len(set(item[1])), item[0]))
            action = None
            for heading, tags in ranked:
                if len(set(tags)) < 2 or heading in tried:
                    continue
                candidate = make_action("YAW_BURST", heading)
                if self._can_recover(pose, environment, candidate):
                    action = candidate
                    break
            for offset in self.search_offsets_deg if action is None else ():
                heading = (self.tracker.canonical_heading_deg + offset) % 360
                if heading in tried:
                    continue
                candidate = make_action("YAW_BURST", heading)
                if self._can_recover(pose, environment, candidate):
                    action = candidate
                    self._search_headings.add(heading)
                    break
            if action is None:
                return self._terminal(environment, "no_admissible_heading")
        self._pending = action
        self._pose = pose
        self._pending_at = now
        self._attempts += 1
        if action.kind != "STATIONARY_BURST":
            self._tried.add(action.heading_deg)
        return action

    def acquire(self, action: RecoveryAction) -> RecoveryOutcome:
        return self.assess_burst(action, self.burst_source(action))

    def _account_verified_rotation(self, action: RecoveryAction, fixes: list[PoseFix],
                                   position: Position, heading: float, now: float) -> None:
        if action.kind == "STATIONARY_BURST" or action.action_id in self._accounted_rotations:
            return
        current = self.tracker.health
        # Task B2: reading ``health`` propagates the tracker to the clock's
        # *current* reading, which on a real monotonic clock is already later
        # than ``now`` (taken when the burst assessment began). Propagating
        # back to ``now`` is time running backwards, which the tracker latches
        # as LOST -- every real yaw recovery then failed as REANCHOR_SYNC_FAILED.
        now = max(now, current.propagated_at)
        # The executor may already have propagated some or all of this rotation.
        # Account only the remaining angular displacement; completed action IDs
        # also make a failed reanchor persistence retry unable to add yaw twice.
        delta = (action.heading_deg - current.heading_deg + 180) % 360 - 180
        command = PlannedCommand.from_mapping("ROTATE",
            {"degrees": abs(delta), "direction": "ccw" if delta > 0 else "cw"},
            upper_bound_s=action.upper_bound_s, interruptible=False)
        propagated = self.tracker.note_command(command, now)
        self.tracker._health = self.tracker._advance(replace(propagated,
            position=position, heading_deg=heading,
            xy_uncertainty_cm=max(propagated.xy_uncertainty_cm, *(fix.xy_uncertainty_cm for fix in fixes)),
            heading_uncertainty_deg=max(propagated.heading_uncertainty_deg, *(fix.heading_uncertainty_deg for fix in fixes))), now)
        self._accounted_rotations.add(action.action_id)

    def assess_burst(self, action: RecoveryAction, reports: tuple[PoseReport, ...]) -> RecoveryOutcome:
        now = self._clock()
        environment = self._environment
        session = environment.session_id if environment else ""
        clock_domain = environment.clock_domain_id if environment else ""

        def reject(reason):
            return RecoveryOutcome(False, reason, session, clock_domain)

        if action != self._pending or self._finished or environment is None:
            return reject("unknown_or_consumed_action")
        self._pending = None
        profile = self.tracker.profile
        pose = self._pose
        if now - self._pending_at > action.upper_bound_s:
            self._finished = True
            return reject("action_deadline_exhausted")
        if (self._clock_failed or profile is None or pose is None
                or now - self._started_at >= profile.recovery_deadline_s
                or now * 1000 >= environment.navigation_contingency_expires_monotonic_ms):
            return reject("expired")
        current = self.tracker.health
        if (action.pose_revision != current.pose_revision or action.measured_at != current.measured_at):
            self._finished = True
            return reject("superseded_pose")
        if pose.state == LocalizationState.LOST or current.state == LocalizationState.LOST:
            return reject("lost")
        if not isinstance(reports, (tuple, list)) or any(not isinstance(report, PoseReport) for report in reports):
            return reject("malformed_report")
        try:
            # Legacy decoders may bypass construction; never count raw aliases.
            report_ids = tuple(_canonical_report_id(report.report_id) for report in reports)
        except ValueError:
            return reject("malformed_report")
        if len(set(report_ids)) < profile.min_burst_reports:
            return reject("insufficient_distinct_reports")
        # Duplicate identities may not weight the aggregate or hide spread.
        if len(set(report_ids)) != len(reports):
            return reject("duplicate_reports")
        search = action.heading_deg in self._search_headings
        # A search burst looks off the mapped headings: any mapped tag it sees counts.
        mapped = set().union(*(set(tags) for heading, tags in environment.mapped_heading_tag_ids
                                  if search or heading_distance(heading, action.heading_deg)
                                  <= profile.max_fix_heading_jump_deg))
        for report in reports:
            fix = report.fix
            if (not valid_fix(fix) or not 0 <= now - fix.measured_at <= profile.max_fix_age_on_arrival_s
                    or fix.measured_at < max(self._pending_at, action.measured_at)
                    or not (fix.source == "mapped_joint_tags" and len(set(fix.tag_ids)) >= 2
                            or fix.source == SINGLE_TAG_SOURCE and len(set(fix.tag_ids)) >= 1)
                    or not set(fix.tag_ids) <= mapped
                    or fix.xy_uncertainty_cm >= profile.max_xy_uncertainty_cm
                    or fix.heading_uncertainty_deg >= profile.max_heading_uncertainty_deg):
                return reject("untrusted_report")
        fixes = [report.fix for report in reports]
        single = any(fix.source == SINGLE_TAG_SOURCE for fix in fixes)
        # One tag is noisier than a joint view: its burst may spread twice as far.
        spread_cm = profile.max_burst_spread_cm * (2 if single else 1)
        if any(math.dist((a.position.x_cm, a.position.y_cm), (b.position.x_cm, b.position.y_cm)) > spread_cm
               or abs(a.position.z_cm - b.position.z_cm) > profile.max_height_error_cm
               or heading_distance(a.heading_deg, b.heading_deg) > profile.max_burst_heading_spread_deg
               for a in fixes for b in fixes):
            return reject("burst_spread")
        count = len(fixes)
        position = Position(*(sum(getattr(fix.position, axis) for fix in fixes) / count
                              for axis in ("x_cm", "y_cm", "z_cm")))
        heading = math.degrees(math.atan2(sum(math.sin(math.radians(fix.heading_deg)) for fix in fixes),
                                         sum(math.cos(math.radians(fix.heading_deg)) for fix in fixes))) % 360
        # Fresh, mapped, distinct and internally coherent two-tag evidence is
        # the best pose there is: when it contradicts the motion history's
        # position, the history was wrong, so the burst re-anchors. A heading
        # far from the one this action rotated to rejects only this action
        # (the scan tries its next heading); it never latches LOST.
        if heading_distance(heading, action.heading_deg) > profile.max_fix_heading_jump_deg:
            return reject("contradictory_heading")
        # A single tag cannot rule out a flipped pose: it re-anchors only a
        # position it agrees with (never a jump), and never latches LOST.
        if single and (math.dist((pose.position.x_cm, pose.position.y_cm), (position.x_cm, position.y_cm))
                       > SINGLE_TAG_MAX_XY_CM
                       or heading_distance(heading, pose.heading_deg) > SINGLE_TAG_MAX_HEADING_DEG):
            return reject("single_tag_disagrees")
        if any(not any(contains_disk(volume, fix.position, fix.xy_uncertainty_cm)
                       for volume in environment.rotation_clearance_volumes) for fix in fixes):
            return reject("uncontained_report")
        self._account_verified_rotation(action, fixes, position, heading, now)
        # A search re-anchor may hold its off-canonical heading: the next
        # checkpoint's admission rotation restores canonical before any move.
        if (not search and heading_distance(heading, self.tracker.canonical_heading_deg)
                > profile.canonical_heading_tolerance_deg):
            self._restore = True
            return reject("restore_canonical_heading")
        accepted_fix = PoseFix(position, heading, min(fix.measured_at for fix in fixes),
                              max(fix.xy_uncertainty_cm for fix in fixes),
                              max(fix.heading_uncertainty_deg for fix in fixes),
                              tuple(sorted(set().union(*(set(fix.tag_ids) for fix in fixes)))),
                              source=SINGLE_TAG_SOURCE if single else "mapped_joint_tags")
        health = self.tracker._anchored(accepted_fix, now, invalidate=True)
        if health.state != LocalizationState.ANCHORED:
            return reject("reanchor_exceeds_profile")
        reanchor_id = action.action_id
        events = tuple({"event_type": event_type,
                        "payload": ({**asdict(health), "clock_domain_id": clock_domain}
                                    if index == 0 else {"pose_revision": health.pose_revision}),
                        "correlation_id": reanchor_id, "causation_id": None,
                        "idempotency_key": f"reanchor:{reanchor_id}:{event_type}"}
                       for index, event_type in enumerate(("POSE_REANCHORED", "ROUTES_INVALIDATED",
                                                           "CANDIDATES_INVALIDATED", "TARGET_LOCKS_INVALIDATED")))
        outcome = RecoveryOutcome(True, "reanchored", session, clock_domain, events, health)
        # Room integration supplies an atomic ledger append here. A failed
        # commit leaves the old pose/history intact and the same batch retryable.
        if self.commit_reanchor is not None:
            try:
                self.commit_reanchor(outcome)
            except Exception:
                self._pending = action
                raise
        self.tracker._health = health
        self.tracker._initialized = True
        self._finished = True
        self._successful_revision = health.pose_revision
        self._completed_reanchors.add(reanchor_id)
        return outcome
