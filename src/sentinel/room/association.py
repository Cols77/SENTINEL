"""Pure one-to-one static-object matching over declared support-aware
spatial estimates.

Association here is deliberately narrower than ``sentinel.survey.merge``'s
mutual-nearest-neighbour clustering: every candidate edge additionally
requires the *same declared support plane*, so a floor phone and a table
computer -- or a floor phone and a table phone -- can never be fused just
because their (x, y) happen to coincide (design precedent: rev 3 R1 of
``survey.merge``, which the module docstring there explains at length).

Clustering is a genuine union-find over every compatible edge (both
"update" edges against an existing object and "new-new" edges between two
fresh estimates), processed globally best-edge-first, so a group can grow
past a single pair in one call: three sightings of the same object offered
together end up as one three-member object, not a pair plus a leftover
singleton. Two previous objects are never fused into each other through a
shared estimate -- at most one bound object identity survives per merged
component. A "same instant" guard is checked against *every* member
already in a component before it is allowed to grow (not just the anchor
object's own displayed ``captured_at``), since ``SpatialEstimate`` carries
no separate frame ID of its own to key on directly.

Every object keeps its ``object_id`` for as long as later estimates keep
revising it -- a revision is a new immutable ``ObjectRevision`` value, never
a mutation of the previous one -- so a hypothesis stays stably identified
across calls even as its member estimates and merged geometry grow. An
estimate whose ``estimate_id`` is already a member of a previous object is
never reprocessed: replaying the same estimate through this function twice
must not grow or re-fuse an object a second time.
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
    #: Every distinct capture instant any member estimate was taken at --
    #: used to reject a new estimate from an instant already represented in
    #: this object (see module docstring), not just the latest one.
    member_captured_ats: tuple[str, ...] = ()
    #: Every distinct *view* any member estimate was taken from: a source
    #: estimate's ``view_id`` when it declared one (see spatial.py), else
    #: its capture time as a fallback identity. "Confirmed" (counting.py)
    #: requires more than one distinct entry here, not merely more than one
    #: member estimate.
    member_view_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "z_interval_cm", tuple(self.z_interval_cm))
        object.__setattr__(self, "xy_covariance_cm2", tuple(self.xy_covariance_cm2))
        object.__setattr__(self, "member_estimate_ids", tuple(self.member_estimate_ids))
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        object.__setattr__(self, "member_captured_ats",
                           tuple(self.member_captured_ats) or (self.captured_at,))
        object.__setattr__(self, "member_view_keys",
                           tuple(self.member_view_keys) or (self.captured_at,))
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


def _view_key(estimate) -> str:
    view_id = getattr(estimate, "view_id", None)
    return view_id if view_id else estimate.captured_at


def _compatible(a_class, a_support, a_plane, b_class, b_support, b_plane) -> bool:
    return a_class == b_class and a_support == b_support and a_plane == b_plane


def _separation(ax, ay, avar, bx, by, bvar) -> float:
    """Normalized distance: 1.0 is the edge of being the same object."""
    dx, dy = bx - ax, by - ay
    sigma_x = math.sqrt(max(avar[0] + bvar[0], 1e-6))
    sigma_y = math.sqrt(max(avar[1] + bvar[1], 1e-6))
    return math.hypot(dx / sigma_x, dy / sigma_y)


def _fuse(a_x, a_y, a_var, b_x, b_y, b_var):
    """Inverse-variance-weighted position fusion, applied per axis. This
    ignores cross-covariance in the fusion (a documented simplification;
    see the task report) but always produces a tighter or equal variance.
    Associative under repeated application, so folding more than two
    estimates together in any stable order gives the same result."""
    def axis(a_mean, a_v, b_mean, b_v):
        wa, wb = 1.0 / max(a_v, 1e-6), 1.0 / max(b_v, 1e-6)
        merged_var = 1.0 / (wa + wb)
        merged_mean = (a_mean * wa + b_mean * wb) * merged_var
        return merged_mean, merged_var
    x_cm, var_x = axis(a_x, a_var[0], b_x, b_var[0])
    y_cm, var_y = axis(a_y, a_var[1], b_y, b_var[1])
    return x_cm, y_cm, (var_x, var_y, 0.0)


class _UnionFind:
    def __init__(self, keys):
        self.parent = {key: key for key in keys}

    def find(self, key):
        while self.parent[key] != key:
            self.parent[key] = self.parent[self.parent[key]]
            key = self.parent[key]
        return key

    def union(self, a, b):
        """Merges ``a``'s component into ``b``'s; always returns ``b``'s root."""
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb
        return rb


def associate_static(previous, estimates):
    """Match new spatial estimates against existing objects, one-to-one.

    Every previous object and every new estimate joins at most one final
    group, built by unioning every compatible edge (globally best distance
    first) rather than consuming and discarding edges one at a time -- so a
    group can grow past a single pair, and a new estimate is never stolen
    away from an object it is genuinely compatible with just because some
    other, unrelated edge happened to be processed first. Two previous
    objects are never merged into each other. An estimate already recorded
    as a member of some previous object is skipped outright, so replaying
    the same round of estimates twice is a no-op the second time.
    """
    previous = tuple(previous)
    known_estimate_ids = {estimate_id for obj in previous for estimate_id in obj.member_estimate_ids}
    estimates = tuple(e for e in estimates if e.estimate_id not in known_estimate_ids)

    anchors: dict[str, dict] = {}
    for obj in previous:
        key = "obj:" + obj.object_id
        anchors[key] = {
            "kind": "object", "object": obj, "class": obj.class_name,
            "support": obj.support, "plane": obj.support_plane_id,
            "x": obj.x_cm, "y": obj.y_cm, "var": obj.xy_covariance_cm2,
            "captured_ats": set(obj.member_captured_ats), "view_keys": set(obj.member_view_keys),
        }
    for estimate in estimates:
        key = "est:" + estimate.estimate_id
        anchors[key] = {
            "kind": "estimate", "estimate": estimate, "class": estimate.class_name,
            "support": estimate.support, "plane": estimate.support_plane_id,
            "x": estimate.x_cm, "y": estimate.y_cm, "var": estimate.xy_covariance_cm2,
            "captured_ats": {estimate.captured_at}, "view_keys": {_view_key(estimate)},
        }

    object_keys = ["obj:" + obj.object_id for obj in previous]
    estimate_keys = ["est:" + e.estimate_id for e in estimates]

    edges: list[tuple[float, str, str]] = []
    for object_key in object_keys:
        a = anchors[object_key]
        for estimate_key in estimate_keys:
            b = anchors[estimate_key]
            if not _compatible(a["class"], a["support"], a["plane"], b["class"], b["support"], b["plane"]):
                continue
            if next(iter(b["captured_ats"])) in a["captured_ats"]:
                continue
            distance = _separation(a["x"], a["y"], a["var"], b["x"], b["y"], b["var"])
            if distance <= 1.0:
                edges.append((distance, object_key, estimate_key))
    for i, a_key in enumerate(estimate_keys):
        for b_key in estimate_keys[i + 1:]:
            a, b = anchors[a_key], anchors[b_key]
            if not _compatible(a["class"], a["support"], a["plane"], b["class"], b["support"], b["plane"]):
                continue
            if a["captured_ats"] & b["captured_ats"]:
                continue
            distance = _separation(a["x"], a["y"], a["var"], b["x"], b["y"], b["var"])
            if distance <= 1.0:
                edges.append((distance, a_key, b_key))
    edges.sort(key=lambda edge: (edge[0], edge[1], edge[2]))

    uf = _UnionFind(anchors.keys())
    component_object: dict[str, str | None] = {}
    component_captured_ats: dict[str, set[str]] = {}
    component_view_keys: dict[str, set[str]] = {}
    component_members: dict[str, set[str]] = {}
    for key, info in anchors.items():
        component_object[key] = key if info["kind"] == "object" else None
        component_captured_ats[key] = set(info["captured_ats"])
        component_view_keys[key] = set(info["view_keys"])
        component_members[key] = {key}

    for _distance, left, right in edges:
        left_root, right_root = uf.find(left), uf.find(right)
        if left_root == right_root:
            continue
        left_object, right_object = component_object[left_root], component_object[right_root]
        if left_object is not None and right_object is not None:
            # Never fuse two distinct previous objects into one, however an
            # intermediate chain of shared estimates might connect them.
            continue
        if component_captured_ats[left_root] & component_captured_ats[right_root]:
            # Same-instant guard, checked against every current member of
            # both sides, not just one anchor's displayed captured_at.
            continue
        new_root = uf.union(left, right)
        old_root = left_root if new_root == right_root else right_root
        component_object[new_root] = left_object if left_object is not None else right_object
        component_captured_ats[new_root] = component_captured_ats[left_root] | component_captured_ats[right_root]
        component_view_keys[new_root] = component_view_keys[left_root] | component_view_keys[right_root]
        component_members[new_root] = component_members[left_root] | component_members[right_root]
        if old_root != new_root:
            for bucket in (component_object, component_captured_ats, component_view_keys, component_members):
                bucket.pop(old_root, None)

    result: list[ObjectRevision] = []
    for root, members in component_members.items():
        bound_object_key = component_object[root]
        new_estimate_keys = sorted(key for key in members if key.startswith("est:"))
        if bound_object_key is not None:
            obj = anchors[bound_object_key]["object"]
            if not new_estimate_keys:
                result.append(obj)
                continue
            new_estimates = sorted((anchors[key]["estimate"] for key in new_estimate_keys),
                                   key=lambda e: e.estimate_id)
            x_cm, y_cm, var = obj.x_cm, obj.y_cm, obj.xy_covariance_cm2
            for estimate in new_estimates:
                x_cm, y_cm, var = _fuse(x_cm, y_cm, var, estimate.x_cm, estimate.y_cm, estimate.xy_covariance_cm2)
            captured_at = max([obj.captured_at] + [e.captured_at for e in new_estimates])
            newest = max(new_estimates, key=lambda e: e.captured_at)
            result.append(ObjectRevision(
                obj.object_id, obj.class_name, obj.support, obj.support_plane_id, x_cm, y_cm,
                newest.z_interval_cm, var, obj.revision + 1, captured_at,
                obj.member_estimate_ids + tuple(e.estimate_id for e in new_estimates),
                obj.evidence_refs + tuple(ref for e in new_estimates for ref in e.evidence_refs),
                tuple(sorted(component_captured_ats[root])), tuple(sorted(component_view_keys[root])),
            ))
        else:
            members_est = sorted((anchors[key]["estimate"] for key in new_estimate_keys),
                                 key=lambda e: e.estimate_id)
            first = members_est[0]
            x_cm, y_cm, var = first.x_cm, first.y_cm, first.xy_covariance_cm2
            for other in members_est[1:]:
                x_cm, y_cm, var = _fuse(x_cm, y_cm, var, other.x_cm, other.y_cm, other.xy_covariance_cm2)
            captured_at = max(e.captured_at for e in members_est)
            newest = max(members_est, key=lambda e: e.captured_at)
            evidence_refs = tuple(ref for e in members_est for ref in e.evidence_refs)
            group = tuple(sorted(e.estimate_id for e in members_est))
            result.append(ObjectRevision(
                _new_object_id(group), first.class_name, first.support, first.support_plane_id,
                x_cm, y_cm, newest.z_interval_cm, var, 1, captured_at, group, evidence_refs,
                tuple(sorted(component_captured_ats[root])), tuple(sorted(component_view_keys[root])),
            ))

    return tuple(sorted(result, key=lambda obj: obj.object_id))
