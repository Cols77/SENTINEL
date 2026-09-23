"""Pure one-to-one static-object matching over declared support-aware
spatial estimates.

Association here is deliberately narrower than ``sentinel.survey.merge``'s
mutual-nearest-neighbour clustering: every candidate edge additionally
requires the *same declared support plane*, so a floor phone and a table
computer -- or a floor phone and a table phone -- can never be fused just
because their (x, y) happen to coincide (design precedent: rev 3 R1 of
``survey.merge``, which the module docstring there explains at length).
Two estimates captured at the same instant are always two objects: that is
the one piece of ``survey.merge``'s frame-disjoint discipline this module
keeps outright, since ``SpatialEstimate`` carries no explicit frame ID of its
own to key on.

Every object keeps its ``object_id`` for as long as later estimates keep
revising it -- a revision is a new immutable ``ObjectRevision`` value, never
a mutation of the previous one -- so a hypothesis stays stably identified
across calls even as its member estimates and merged geometry grow.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math

from sentinel.room.coverage import SUPPORTED_CLASSES
from sentinel.room.spatial import SupportKind


@dataclass(frozen=True)
class ObjectRevision:
    object_id: str
    class_name: str
    support: SupportKind
    support_plane_id: str | None
    x_cm: float
    y_cm: float
    z_interval_cm: tuple[float, float]
    xy_covariance_cm2: tuple[float, float, float]
    revision: int
    captured_at: str
    member_estimate_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "z_interval_cm", tuple(self.z_interval_cm))
        object.__setattr__(self, "xy_covariance_cm2", tuple(self.xy_covariance_cm2))
        object.__setattr__(self, "member_estimate_ids", tuple(self.member_estimate_ids))
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        if not self.object_id or not self.class_name:
            raise ValueError("object revision requires identity and class")
        if self.class_name not in SUPPORTED_CLASSES:
            raise ValueError("object revision requires a supported class")
        SupportKind(self.support)
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("object revision must be a positive integer")
        if not self.member_estimate_ids:
            raise ValueError("object revision requires at least one member estimate")


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode("utf-8")).hexdigest()


def _new_object_id(estimate_ids: tuple[str, ...]) -> str:
    """Pure function of the founding member estimates -- no uuid, no clock,
    so the same round of estimates always spawns the same object identity."""
    return "object:" + _digest({"member_estimate_ids": sorted(estimate_ids)})


def _compatible(a_class, a_support, a_plane, a_captured_at,
                b_class, b_support, b_plane, b_captured_at) -> bool:
    if a_class != b_class or a_support != b_support or a_plane != b_plane:
        return False
    # Two sightings at the same instant are two objects, whatever the
    # geometry says about how close they are (survey.merge's frame-disjoint
    # rule; SpatialEstimate has no separate frame ID to key on directly).
    return a_captured_at != b_captured_at


def _separation(ax, ay, avar, bx, by, bvar) -> float:
    """Normalized distance: 1.0 is the edge of being the same object."""
    dx, dy = bx - ax, by - ay
    sigma_x = math.sqrt(max(avar[0] + bvar[0], 1e-6))
    sigma_y = math.sqrt(max(avar[1] + bvar[1], 1e-6))
    return math.hypot(dx / sigma_x, dy / sigma_y)


def _fuse(a_x, a_y, a_var, a_weight, b_x, b_y, b_var, b_weight):
    """Inverse-variance-weighted position fusion, applied per axis. This
    ignores cross-covariance in the fusion (a documented simplification;
    see the task report) but always produces a tighter or equal variance."""
    def axis(a_mean, a_v, b_mean, b_v):
        wa, wb = 1.0 / max(a_v, 1e-6), 1.0 / max(b_v, 1e-6)
        merged_var = 1.0 / (wa + wb)
        merged_mean = (a_mean * wa + b_mean * wb) * merged_var
        return merged_mean, merged_var
    x_cm, var_x = axis(a_x, a_var[0], b_x, b_var[0])
    y_cm, var_y = axis(a_y, a_var[1], b_y, b_var[1])
    return x_cm, y_cm, (var_x, var_y, 0.0)


def associate_static(previous, estimates):
    """Match new spatial estimates against existing objects, one-to-one.

    Every previous object and every new estimate is consumed at most once
    per call, via the globally best (smallest normalized separation, then
    stable ID) compatible edge first -- the same greedy discipline as
    ``survey.merge.associate``, restricted to genuinely compatible pairs.
    Objects untouched this round are carried forward unchanged (same
    identity, same revision); a touched object is replaced by a new,
    higher-revision ``ObjectRevision`` with the same ``object_id``.
    """
    previous = tuple(previous)
    estimates = tuple(estimates)

    anchors: dict[str, dict] = {}
    for obj in previous:
        anchors[obj.object_id] = {
            "kind": "previous", "object": obj, "class": obj.class_name,
            "support": obj.support, "plane": obj.support_plane_id,
            "x": obj.x_cm, "y": obj.y_cm, "var": obj.xy_covariance_cm2,
            "captured_at": obj.captured_at, "weight": len(obj.member_estimate_ids),
        }
    for estimate in estimates:
        anchors[estimate.estimate_id] = {
            "kind": "estimate", "estimate": estimate, "class": estimate.class_name,
            "support": estimate.support, "plane": estimate.support_plane_id,
            "x": estimate.x_cm, "y": estimate.y_cm, "var": estimate.xy_covariance_cm2,
            "captured_at": estimate.captured_at, "weight": 1,
        }

    estimate_keys = [estimate.estimate_id for estimate in estimates]
    object_keys = [obj.object_id for obj in previous]

    edges = []
    for object_key in object_keys:
        anchor = anchors[object_key]
        for estimate_key in estimate_keys:
            other = anchors[estimate_key]
            if not _compatible(anchor["class"], anchor["support"], anchor["plane"], anchor["captured_at"],
                               other["class"], other["support"], other["plane"], other["captured_at"]):
                continue
            distance = _separation(anchor["x"], anchor["y"], anchor["var"], other["x"], other["y"], other["var"])
            if distance <= 1.0:
                edges.append((distance, object_key, estimate_key, "update"))
    for i, a_key in enumerate(estimate_keys):
        for b_key in estimate_keys[i + 1:]:
            a, b = anchors[a_key], anchors[b_key]
            if not _compatible(a["class"], a["support"], a["plane"], a["captured_at"],
                               b["class"], b["support"], b["plane"], b["captured_at"]):
                continue
            distance = _separation(a["x"], a["y"], a["var"], b["x"], b["y"], b["var"])
            if distance <= 1.0:
                edges.append((distance, a_key, b_key, "new"))
    edges.sort(key=lambda edge: (edge[0], edge[1], edge[2]))

    claimed_objects: set[str] = set()
    claimed_estimates: set[str] = set()
    updates: dict[str, list[str]] = {}
    new_groups: list[list[str]] = []
    new_group_by_key: dict[str, list[str]] = {}

    for distance, left_key, right_key, kind in edges:
        if kind == "update":
            if left_key in claimed_objects or right_key in claimed_estimates:
                continue
            claimed_objects.add(left_key)
            claimed_estimates.add(right_key)
            updates[left_key] = [right_key]
        else:
            if left_key in claimed_estimates or right_key in claimed_estimates:
                continue
            group = new_group_by_key.get(left_key) or new_group_by_key.get(right_key)
            if group is None:
                group = []
                new_groups.append(group)
            for key in (left_key, right_key):
                if key not in group:
                    group.append(key)
                new_group_by_key[key] = group
            claimed_estimates.add(left_key)
            claimed_estimates.add(right_key)

    result: list[ObjectRevision] = []
    for obj in previous:
        if obj.object_id not in updates:
            result.append(obj)
            continue
        estimate = next(e for e in estimates if e.estimate_id == updates[obj.object_id][0])
        x_cm, y_cm, var = _fuse(obj.x_cm, obj.y_cm, obj.xy_covariance_cm2, len(obj.member_estimate_ids),
                                estimate.x_cm, estimate.y_cm, estimate.xy_covariance_cm2, 1)
        captured_at = max(obj.captured_at, estimate.captured_at)
        result.append(ObjectRevision(
            obj.object_id, obj.class_name, obj.support, obj.support_plane_id, x_cm, y_cm,
            estimate.z_interval_cm, var, obj.revision + 1, captured_at,
            obj.member_estimate_ids + (estimate.estimate_id,), obj.evidence_refs + estimate.evidence_refs,
        ))

    for group in new_groups:
        group = sorted(group)
        members = [anchors[key]["estimate"] for key in group]
        first = members[0]
        x_cm, y_cm, var = first.x_cm, first.y_cm, first.xy_covariance_cm2
        for other in members[1:]:
            x_cm, y_cm, var = _fuse(x_cm, y_cm, var, 1, other.x_cm, other.y_cm, other.xy_covariance_cm2, 1)
        captured_at = max(member.captured_at for member in members)
        evidence_refs = tuple(ref for member in members for ref in member.evidence_refs)
        result.append(ObjectRevision(
            _new_object_id(tuple(group)), first.class_name, first.support, first.support_plane_id,
            x_cm, y_cm, first.z_interval_cm, var, 1, captured_at, tuple(group), evidence_refs,
        ))

    for estimate in estimates:
        if estimate.estimate_id in claimed_estimates:
            continue
        result.append(ObjectRevision(
            _new_object_id((estimate.estimate_id,)), estimate.class_name, estimate.support,
            estimate.support_plane_id, estimate.x_cm, estimate.y_cm, estimate.z_interval_cm,
            estimate.xy_covariance_cm2, 1, estimate.captured_at, (estimate.estimate_id,), estimate.evidence_refs,
        ))

    return tuple(sorted(result, key=lambda obj: obj.object_id))
