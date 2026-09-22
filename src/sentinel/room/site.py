"""Reusable surveyed geometry and session-local terminal-clearance assurances.

Site documents contain durations, never absolute monotonic instants. A new
process must supply a new clock-domain ID; wall-clock metadata cannot renew an
assurance. Clearance observations are appended by the room session, not the site.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from enum import StrEnum
import json
import math
import re
from typing import Callable, Literal
import uuid

from sentinel.cube.checkpoint import HomeDefinition
from sentinel.cube.localization import DriftProfile, LocalizationState, PoseHealth
from sentinel.cube.recovery import RecoveryEnvironment, RecoveryVolume, contains_disk
from sentinel.models import Position


def _polygon(points):
    if (len(points) < 3 or len(set(points)) != len(points)
            or any(len(point) != 2 or not all(math.isfinite(v) for v in point) for point in points)):
        raise ValueError("invalid surveyed polygon")
    cross = lambda a, b, c: (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    # Restrict base survey volumes to convex simple polygons. More complex rooms
    # are represented by several volumes, so swept containment remains exact.
    turns = [cross(points[i - 1], points[i], points[(i + 1) % len(points)]) for i in range(len(points))]
    if not (all(turn > 0 for turn in turns) or all(turn < 0 for turn in turns)):
        raise ValueError("survey polygons must be convex with nonzero area")
    for i in range(len(points)):
        a, b = points[i], points[(i + 1) % len(points)]
        for j in range(i + 2, len(points)):
            if i == 0 and j == len(points) - 1:
                continue
            c, d = points[j], points[(j + 1) % len(points)]
            if cross(a, b, c) * cross(a, b, d) <= 0 and cross(c, d, a) * cross(c, d, b) <= 0:
                raise ValueError("survey polygon self-intersects")


def _band(values):
    if len(values) != 2 or not all(math.isfinite(v) for v in values) or values[0] >= values[1]:
        raise ValueError("invalid surveyed height band")


@dataclass(frozen=True)
class Volume3D:
    volume_id: str
    polygon_xy_cm: tuple[tuple[float, float], ...]
    z_interval_cm: tuple[float, float]

    def __post_init__(self):
        if not self.volume_id:
            raise ValueError("volume needs an identity")
        _polygon(self.polygon_xy_cm)
        _band(self.z_interval_cm)


@dataclass(frozen=True)
class RouteDefinition:
    route_id: str
    checkpoints: tuple[Position, ...]
    corridor_volume_ids: tuple[str, ...]

    def __post_init__(self):
        if not self.route_id or not self.checkpoints or not self.corridor_volume_ids:
            raise ValueError("route needs checkpoints and concrete corridors")
        if any(not all(math.isfinite(value) for value in asdict(point).values()) for point in self.checkpoints):
            raise ValueError("invalid route checkpoint")


class ContingencyTrigger(StrEnum):
    NAVIGATION_UNAVAILABLE = "NAVIGATION_UNAVAILABLE"
    RETURN_ENERGY_UNAVAILABLE = "RETURN_ENERGY_UNAVAILABLE"


class ContingencyKind(StrEnum):
    LAND_IN_PLACE = "LAND_IN_PLACE"
    CONTROLLED_DESCENT = "CONTROLLED_DESCENT"
    EMERGENCY = "EMERGENCY"


@dataclass(frozen=True)
class TerminalContingency:
    contingency_id: str
    trigger: ContingencyTrigger
    kind: ContingencyKind
    landing_polygon_xy_cm: tuple[tuple[float, float], ...]
    z_band_cm: tuple[float, float]
    required_pose_state: Literal["ANCHORED", "COASTING", "ANY"]
    clearance_source: str
    assurance_policy_id: str
    max_assurance_age_ms: int

    def __post_init__(self):
        ContingencyTrigger(self.trigger)
        ContingencyKind(self.kind)  # RETURN_VIA_ROUTE is never a navigation contingency.
        if (not self.contingency_id or self.required_pose_state not in {"ANCHORED", "COASTING", "ANY"}
                or type(self.max_assurance_age_ms) is not int or self.max_assurance_age_ms <= 0):
            raise ValueError("invalid terminal contingency policy")
        if self.kind != ContingencyKind.EMERGENCY:
            _polygon(self.landing_polygon_xy_cm)
            _band(self.z_band_cm)
            if not self.clearance_source.strip() or not self.assurance_policy_id.strip():
                raise ValueError("landing/descent requires clearance source and assurance policy")


@dataclass(frozen=True)
class ContingencyAssurance:
    assurance_id: str
    contingency_id: str
    session_id: str
    clock_domain_id: str
    state: Literal["CURRENT", "REVOKED"]
    assured_at: str
    assured_monotonic_ms: int
    expires_monotonic_ms: int
    evidence_refs: tuple[str, ...]

    def __post_init__(self):
        if not all((self.assurance_id, self.contingency_id, self.session_id, self.clock_domain_id, self.assured_at)):
            raise ValueError("assurance requires session and clock identity")
        if (self.state not in {"CURRENT", "REVOKED"} or not self.evidence_refs
                or type(self.assured_monotonic_ms) is not int or type(self.expires_monotonic_ms) is not int
                or not 0 <= self.assured_monotonic_ms < self.expires_monotonic_ms):
            raise ValueError("invalid assurance lifetime or evidence")


@dataclass(frozen=True)
class RoomSite:
    site_id: str
    cube_site_sha256: str
    tag_map_sha256: str
    free_space_volumes: tuple[Volume3D, ...]
    forbidden_volumes: tuple[Volume3D, ...]
    corridors: tuple[Volume3D, ...]
    routes: tuple[RouteDefinition, ...]
    home: HomeDefinition
    terminal_contingencies: tuple[TerminalContingency, ...]
    clearance_provenance: str

    def validate(self, *, real_mode: bool = True) -> None:
        if not self.site_id or not self.clearance_provenance.strip() or not self.free_space_volumes:
            raise ValueError("site needs surveyed free space and clearance provenance")
        if any(re.fullmatch(r"[0-9a-f]{64}", digest) is None for digest in (self.cube_site_sha256, self.tag_map_sha256)):
            raise ValueError("site needs cube/tag map SHA256 bindings")
        volumes = self.free_space_volumes + self.forbidden_volumes + self.corridors
        if len({v.volume_id for v in volumes}) != len(volumes):
            raise ValueError("duplicate volume identity")
        if len({r.route_id for r in self.routes}) != len(self.routes):
            raise ValueError("duplicate route identity")
        if self.home.route_id not in {route.route_id for route in self.routes}:
            raise ValueError("home references missing route")
        if not self.home.pad_id or not math.isfinite(self.home.heading_deg):
            raise ValueError("invalid static home")
        if not any(_contains(volume, self.home.nominal_pose) for volume in self.free_space_volumes):
            raise ValueError("home outside surveyed volume")
        corridors = {volume.volume_id: volume for volume in self.corridors}
        for route in self.routes:
            if not set(route.corridor_volume_ids) <= corridors.keys():
                raise ValueError("route references missing corridor")
            for point in route.checkpoints:
                if not any(_contains(corridors[key], point) for key in route.corridor_volume_ids):
                    raise ValueError("route checkpoint outside corridor")
        ids = [entry.contingency_id for entry in self.terminal_contingencies]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate terminal contingency identity")
        if real_mode and {entry.trigger for entry in self.terminal_contingencies} != set(ContingencyTrigger):
            raise ValueError("real mode requires both condition-specific terminal contingencies")


def _contains(volume, position):
    return contains_disk(RecoveryVolume(volume.volume_id, volume.polygon_xy_cm, volume.z_interval_cm), position, 0)


def _overlaps(a, b):
    """Conservative AABB overlap; never use a volume that might contain a hazard."""
    if a.z_interval_cm[1] < b.z_interval_cm[0] or b.z_interval_cm[1] < a.z_interval_cm[0]:
        return False
    for axis in (0, 1):
        if max(point[axis] for point in a.polygon_xy_cm) < min(point[axis] for point in b.polygon_xy_cm):
            return False
        if max(point[axis] for point in b.polygon_xy_cm) < min(point[axis] for point in a.polygon_xy_cm):
            return False
    return True


def room_site_document(site: RoomSite) -> dict:
    return json.loads(json.dumps({"schema_version": "room-site-base-v1", **asdict(site)}, allow_nan=False))


def _keys(doc, cls):
    if not isinstance(doc, dict) or set(doc) != {field.name for field in fields(cls)}:
        raise ValueError(f"incorrect {cls.__name__} contract fields")
    return doc


def parse_room_site(doc: dict, *, real_mode: bool = True) -> RoomSite:
    """Parse the strict base contract; reject runtime timestamps and extra fields."""
    try:
        if doc.get("schema_version") != "room-site-base-v1":
            raise ValueError("unsupported room site version")
        raw = _keys({key: value for key, value in doc.items() if key != "schema_version"}, RoomSite)

        def volume(item):
            item = _keys(item, Volume3D)
            return Volume3D(item["volume_id"], tuple(tuple(p) for p in item["polygon_xy_cm"]), tuple(item["z_interval_cm"]))

        def position(item):
            return Position(**_keys(item, Position))

        def route(item):
            item = _keys(item, RouteDefinition)
            return RouteDefinition(item["route_id"], tuple(position(point) for point in item["checkpoints"]),
                                   tuple(item["corridor_volume_ids"]))

        def contingency(item):
            item = _keys(item, TerminalContingency)
            return TerminalContingency(**{**item, "trigger": ContingencyTrigger(item["trigger"]),
                "kind": ContingencyKind(item["kind"]), "landing_polygon_xy_cm": tuple(tuple(p) for p in item["landing_polygon_xy_cm"]),
                "z_band_cm": tuple(item["z_band_cm"])})

        home = _keys(raw["home"], HomeDefinition)
        result = RoomSite(**{**raw,
            "free_space_volumes": tuple(volume(item) for item in raw["free_space_volumes"]),
            "forbidden_volumes": tuple(volume(item) for item in raw["forbidden_volumes"]),
            "corridors": tuple(volume(item) for item in raw["corridors"]),
            "routes": tuple(route(item) for item in raw["routes"]),
            "home": HomeDefinition(**{**home, "nominal_pose": position(home["nominal_pose"])}),
            "terminal_contingencies": tuple(contingency(item) for item in raw["terminal_contingencies"])})
        result.validate(real_mode=real_mode)
        return result
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise ValueError("malformed room site") from exc


def adapt_recovery_environment(site: RoomSite, assurances: tuple[ContingencyAssurance, ...], *,
        session_id: str, clock_domain_id: str, navigation_contingency_id: str,
        now_monotonic_ms: int, mapped_heading_tag_ids: tuple[tuple[int, tuple[int, ...]], ...]) -> RecoveryEnvironment:
    """Resolve exactly one live assurance. Revocation is irreversible per ID.

    The caller supplies observations already qualified by the site's named
    clearance policy. Their persisted evidence is required here; this adapter
    never manufactures an assurance from static survey data.
    """
    site.validate(real_mode=True)
    entries = [entry for entry in site.terminal_contingencies if entry.contingency_id == navigation_contingency_id]
    if len(entries) != 1 or entries[0].trigger != ContingencyTrigger.NAVIGATION_UNAVAILABLE:
        raise ValueError("navigation contingency must resolve uniquely with the matching trigger")
    entry = entries[0]
    matching = [item for item in assurances if item.contingency_id == entry.contingency_id
                and item.session_id == session_id and item.clock_domain_id == clock_domain_id]
    revoked = {item.assurance_id for item in matching if item.state == "REVOKED"}
    current = [item for item in matching if item.state == "CURRENT" and item.assurance_id not in revoked]
    if len(current) != 1:
        raise ValueError("navigation requires exactly one current session/clock assurance")
    assurance = current[0]
    deadline = min(assurance.expires_monotonic_ms, assurance.assured_monotonic_ms + entry.max_assurance_age_ms)
    if not assurance.assured_monotonic_ms <= now_monotonic_ms < deadline:
        raise ValueError("assurance expired or monotonic clock reset")
    if len({heading for heading, _ in mapped_heading_tag_ids}) != len(mapped_heading_tag_ids):
        raise ValueError("duplicate mapped recovery heading")
    volumes = tuple(RecoveryVolume(volume.volume_id, volume.polygon_xy_cm, volume.z_interval_cm)
                    for volume in site.free_space_volumes
                    if not any(_overlaps(volume, forbidden) for forbidden in site.forbidden_volumes))
    return RecoveryEnvironment(session_id, clock_domain_id, mapped_heading_tag_ids, volumes,
                               navigation_contingency_id, deadline)


class SessionClockGuard:
    """One session's monotonic history, shared by every clearance consumer.

    The owner creates one guard at session setup. A new guard always mints a new
    clock domain, so restarting cannot import assurances from an old process.
    A regression permanently poisons the existing guard, even after expiry.
    """
    def __init__(self, *, session_id: str):
        if not session_id:
            raise ValueError("clock guard requires a session identity")
        self._session_id = session_id
        self._clock_domain_id = str(uuid.uuid4())
        self._last_now_ms: float | None = None
        self._failed = False

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def clock_domain_id(self) -> str:
        return self._clock_domain_id

    def observe(self, now_monotonic_ms: float, *, session_id: str, clock_domain_id: str) -> bool:
        if (session_id, clock_domain_id) != (self.session_id, self.clock_domain_id):
            return False
        if (isinstance(now_monotonic_ms, bool) or not isinstance(now_monotonic_ms, (int, float))
                or not math.isfinite(now_monotonic_ms) or now_monotonic_ms < 0
                or (self._last_now_ms is not None and now_monotonic_ms < self._last_now_ms)):
            self._failed = True
        if self._failed:
            return False
        self._last_now_ms = now_monotonic_ms
        return True


class RecoveryEnvironmentAdapter:
    """Session-owned adapter; creating/restarting it always creates a new domain.

    Use this stateful boundary during a running session. The pure one-shot
    adapter above is also useful for validating recorded observations in replay.
    A detected clock reset permanently invalidates this instance.
    """
    def __init__(self, *, session_id: str, clock: Callable[[], float],
                 clock_guard: SessionClockGuard | None = None):
        self.clock_guard = clock_guard if clock_guard is not None else SessionClockGuard(session_id=session_id)
        if self.clock_guard.session_id != session_id:
            raise ValueError("clock guard belongs to another session")
        self.session_id = self.clock_guard.session_id
        self.clock_domain_id = self.clock_guard.clock_domain_id
        self.clock = clock
        if not self.clock_guard.observe(clock() * 1000, session_id=self.session_id, clock_domain_id=self.clock_domain_id):
            raise ValueError("clock guard is invalid")

    def adapt(self, site: RoomSite, assurances: tuple[ContingencyAssurance, ...], *,
              navigation_contingency_id: str, mapped_heading_tag_ids) -> RecoveryEnvironment:
        now = self.clock()
        if not self.clock_guard.observe(now * 1000, session_id=self.session_id, clock_domain_id=self.clock_domain_id):
            raise ValueError("clock reset invalidated this session clock domain")
        return adapt_recovery_environment(site, assurances, session_id=self.session_id,
            clock_domain_id=self.clock_domain_id, navigation_contingency_id=navigation_contingency_id,
            now_monotonic_ms=math.floor(now * 1000), mapped_heading_tag_ids=mapped_heading_tag_ids)


def admit_terminal_contingency(site: RoomSite, assurances: tuple[ContingencyAssurance, ...], *,
        contingency_id: str, trigger: ContingencyTrigger, session_id: str, clock_domain_id: str,
        now_monotonic_ms: int, pose: PoseHealth, energy_available: bool,
        drift_profile: DriftProfile | None = None,
        clock_guard: SessionClockGuard | None = None) -> TerminalContingency | None:
    """Resolve a usable policy, or leave escalation to the validated emergency path.

    Energy admission is supplied by the room reserve guard. Neither missing
    navigation nor an ID returned by recovery itself authorizes a landing. The
    validated drift profile supplies the complete aircraft clearance radius;
    the session-owned clock guard is mandatory even on expired observations.
    """
    if clock_guard is None or not clock_guard.observe(now_monotonic_ms,
            session_id=session_id, clock_domain_id=clock_domain_id):
        return None
    site.validate(real_mode=True)
    entries = [item for item in site.terminal_contingencies if item.contingency_id == contingency_id and item.trigger == trigger]
    if len(entries) != 1 or not energy_available:
        return None
    entry = entries[0]
    matching = [item for item in assurances if item.contingency_id == contingency_id
                and item.session_id == session_id and item.clock_domain_id == clock_domain_id]
    revoked = {item.assurance_id for item in matching if item.state == "REVOKED"}
    current = [item for item in matching if item.state == "CURRENT" and item.assurance_id not in revoked]
    if len(current) != 1:
        return None
    assurance = current[0]
    deadline = min(assurance.expires_monotonic_ms, assurance.assured_monotonic_ms + entry.max_assurance_age_ms)
    if not assurance.assured_monotonic_ms <= now_monotonic_ms < deadline:
        return None
    if entry.kind == ContingencyKind.EMERGENCY:
        return entry
    if drift_profile is None or not (drift_profile.validated and drift_profile.evidence_refs):
        return None
    allowed = ({LocalizationState.ANCHORED} if entry.required_pose_state == "ANCHORED"
               else {LocalizationState.ANCHORED, LocalizationState.COASTING})
    if pose.state not in allowed:
        return None
    radius_cm = drift_profile.rotation_radius_cm + pose.xy_uncertainty_cm
    landing = RecoveryVolume(entry.contingency_id, entry.landing_polygon_xy_cm, entry.z_band_cm)
    if not contains_disk(landing, pose.position, radius_cm):
        return None
    if any(_overlaps(landing, forbidden) for forbidden in site.forbidden_volumes):
        return None
    touchdown = Position(pose.position.x_cm, pose.position.y_cm, entry.z_band_cm[0])
    # One convex free-space prism must contain both ends of the descent. This
    # proves the vertical swept segment instead of checking only the hover.
    if not any(all(contains_disk(RecoveryVolume(volume.volume_id, volume.polygon_xy_cm, volume.z_interval_cm),
                                 point, radius_cm) for point in (pose.position, touchdown))
               for volume in site.free_space_volumes):
        return None
    return entry
