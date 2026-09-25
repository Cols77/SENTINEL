"""Bounded coverage cells: whether a required class has actually been
resolved in a declared region of the room, not merely guessed at.

A cell is a small, bounded volume the site declares once (a polygon plus a
height band) together with the classes that must be resolved inside it for a
room-wide count to be complete. Each *sighting* of a cell for a class is a
``CellObservation``: at most one governs the final status of that
``(cell_id, class_name)`` pair, chosen by the precedence in ``_RANK`` below.
Nothing here reads the room's evidence itself -- callers project detections,
frame ingestion state and declared occlusion into ``CellObservation`` rows;
this module only resolves the bookkeeping honestly.

``OCCLUDED`` is a *claim*, not a default: without at least one evidence
reference it is worth nothing more than never having looked, exactly like an
unvisited cell. ``UNANALYSED`` means a frame exists but has not finished
being scored -- distinct from a cell nobody has pointed a camera at yet.
``NOT_RESOLVABLE`` is terminal in the other direction: the declared geometry
can never resolve this class here, so a room-wide count can be honestly
complete without it ever turning ``OBSERVED``. Because it is a *geometry*
verdict and not a single sighting's opinion, one evidenced ``NOT_RESOLVABLE``
observation is never enough on its own in ``project_coverage``: it becomes
terminal only once a second, independent pose corroborates it, or once the
caller's declared view geometry (``declared_views_by_cell``) says every pose
the site declares able to view the cell has agreed (which can mean a single
report is definitive, when the site declares only one such pose, or even
zero poses at all -- see ``project_coverage``).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import math
from typing import Literal

SUPPORTED_CLASSES = ("computer", "person", "phone", "mug")


class CoverageStatus(StrEnum):
    UNVISITED = "UNVISITED"
    OBSERVED = "OBSERVED"
    OCCLUDED = "OCCLUDED"
    UNANALYSED = "UNANALYSED"
    NOT_RESOLVABLE = "NOT_RESOLVABLE"


#: Precedence when more than one observation exists for the same
#: (cell_id, class_name) pair -- the most informative status wins, and a
#: duplicate sighting at the same or a lesser status changes nothing.
_RANK = {
    CoverageStatus.UNVISITED: 0,
    CoverageStatus.UNANALYSED: 1,
    CoverageStatus.OCCLUDED: 2,
    CoverageStatus.NOT_RESOLVABLE: 3,
    CoverageStatus.OBSERVED: 4,
}

#: A pair resolved to one of these statuses needs no further observation --
#: either it has been, or it provably never can be. Every other status
#: (including a plain absence, i.e. UNVISITED) leaves the room incomplete.
_TERMINAL = frozenset({CoverageStatus.OBSERVED, CoverageStatus.NOT_RESOLVABLE})


def _polygon(points: tuple[tuple[float, float], ...]) -> None:
    if len(points) < 3 or len(set(points)) != len(points) or any(
        len(point) != 2 or not all(math.isfinite(v) for v in point) for point in points
    ):
        raise ValueError("invalid coverage cell polygon")


def _band(values: tuple[float, float]) -> None:
    if len(values) != 2 or not all(math.isfinite(v) for v in values) or values[0] >= values[1]:
        raise ValueError("invalid coverage cell height band")


@dataclass(frozen=True)
class CoverageCell:
    cell_id: str
    polygon_xy_cm: tuple[tuple[float, float], ...]
    z_band_cm: tuple[float, float]
    required_classes: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "polygon_xy_cm", tuple(tuple(p) for p in self.polygon_xy_cm))
        object.__setattr__(self, "z_band_cm", tuple(self.z_band_cm))
        object.__setattr__(self, "required_classes", tuple(self.required_classes))
        if not self.cell_id:
            raise ValueError("coverage cell needs an identity")
        _polygon(self.polygon_xy_cm)
        _band(self.z_band_cm)
        if not self.required_classes or len(set(self.required_classes)) != len(self.required_classes):
            raise ValueError("coverage cell needs distinct required classes")
        if any(name not in SUPPORTED_CLASSES for name in self.required_classes):
            raise ValueError("coverage cell requires an unsupported class")


@dataclass(frozen=True)
class CellObservation:
    """One sighting's verdict for a single (cell, class) pair.

    ``OCCLUDED`` without ``evidence_refs`` is not a valid positive claim (see
    module docstring) and is demoted to absence during projection, not
    rejected here -- a caller may legitimately hold a typed refusal record
    that carries no evidence and still wants to pass it through unchanged.
    """
    cell_id: str
    class_name: str
    pose_id: str
    status: Literal["OBSERVED", "OCCLUDED", "UNANALYSED", "NOT_RESOLVABLE"]
    observed_at: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        if not self.cell_id or not self.class_name or not self.pose_id or not self.observed_at:
            raise ValueError("cell observation requires identity, class, pose and time")
        CoverageStatus(self.status)
        if self.status == CoverageStatus.UNVISITED:
            raise ValueError("an observation cannot itself claim UNVISITED")


@dataclass(frozen=True)
class CoverageProjection:
    by_cell_and_class: tuple[tuple[str, str, str], ...]
    complete: bool
    uncovered: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    #: Grounded-answer fields (design spec "Goals, evidence and Jev boundary"):
    #: the most recent observation time behind this projection, and how many
    #: distinct frames (keyed by (pose_id, observed_at)) have finished being
    #: scored versus are still pending. ``None``/``0``/``0`` when nothing has
    #: been observed at all. Defaulted so pre-existing positional callers
    #: (and old replayed digests) are unaffected.
    latest_capture_at: str | None = None
    analysed_frames: int = 0
    unanalysed_frames: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "by_cell_and_class", tuple(tuple(row) for row in self.by_cell_and_class))
        object.__setattr__(self, "uncovered", tuple(self.uncovered))
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))


def _frame_key(observation: CellObservation) -> tuple[str, str]:
    return (observation.pose_id, observation.observed_at)


def _capture_summary(observations: tuple[CellObservation, ...]) -> tuple[str | None, int, int]:
    """Latest capture time plus analysed/unanalysed frame counts.

    A "frame" here is the (pose_id, observed_at) pair a sighting was taken
    from -- the only frame-identifying data ``CellObservation`` carries. A
    frame counts as unanalysed only while *every* observation it produced is
    still ``UNANALYSED``; as soon as any of its observations resolved to a
    real verdict, the frame is analysed.
    """
    if not observations:
        return None, 0, 0
    latest = max(observation.observed_at for observation in observations)
    statuses_by_frame: dict[tuple[str, str], set[str]] = {}
    for observation in observations:
        statuses_by_frame.setdefault(_frame_key(observation), set()).add(observation.status)
    unanalysed = sum(1 for statuses in statuses_by_frame.values() if statuses == {CoverageStatus.UNANALYSED.value})
    analysed = len(statuses_by_frame) - unanalysed
    return latest, analysed, unanalysed


def project_coverage(cells: tuple[CoverageCell, ...],
                      observations: tuple[CellObservation, ...],
                      *,
                      declared_views_by_cell: Mapping[str, tuple[str, ...]] | None = None) -> CoverageProjection:
    """Resolve every (cell_id, class_name) pair the declared cells require.

    Both positional arguments are copied into local tuples immediately, and
    every field of the result is itself an immutable tuple built fresh here,
    so no later mutation of a caller's list can reach back into an
    already-returned projection (or the digest computed from it).
    ``declared_views_by_cell`` is only read during this call and never kept
    on the returned projection, so mutating it afterwards cannot reach back
    either.

    ``declared_views_by_cell`` maps a cell_id to the pose_ids the site
    declares can ever view it (``RoomSite.pose_visibility``, inverted from
    pose -> cells into cell -> poses). It is the one place this otherwise
    site-independent module is told about declared geometry, and it exists
    to keep ``NOT_RESOLVABLE`` the geometry verdict the module docstring
    promises rather than a lone sighting's opinion:

    * A cell present in the mapping is resolved by its declared geometry --
      terminal once every pose the mapping names for it has (evidenced)
      reported ``NOT_RESOLVABLE``. An empty tuple for a cell means the site
      declares *no* pose can ever view it, so a single evidenced claim from
      any pose is immediately definitive (the declared set is trivially a
      subset of any report).
    * A cell absent from the mapping (including when the mapping itself is
      omitted, for every caller and fixture that predates declared-geometry
      wiring) has no known declared geometry at all, so one sighting's claim
      is not enough by itself -- it becomes terminal only once a second,
      independent pose corroborates it, exactly the same "more than one
      distinct view" bar ``counting._static_counts`` already applies before
      calling a static object confirmed.
    """
    cells = tuple(cells)
    observations = tuple(observations)
    declared_views_by_cell = declared_views_by_cell or {}
    required = sorted({(cell.cell_id, class_name)
                        for cell in cells for class_name in cell.required_classes})
    required_set = set(required)
    latest_capture_at, analysed_frames, unanalysed_frames = _capture_summary(observations)

    evidenced_by_key: dict[tuple[str, str], list[CellObservation]] = {}
    for observation in observations:
        key = (observation.cell_id, observation.class_name)
        if key not in required_set:
            continue
        status = CoverageStatus(observation.status)
        if status in (CoverageStatus.OCCLUDED, CoverageStatus.OBSERVED, CoverageStatus.NOT_RESOLVABLE) \
                and not observation.evidence_refs:
            # A claimed occlusion, observation or unresolvability with no
            # supporting evidence is worth nothing more than never having
            # looked at all.
            continue
        evidenced_by_key.setdefault(key, []).append(observation)

    best: dict[tuple[str, str], tuple[int, CoverageStatus]] = {}
    evidence_by_pair: dict[tuple[str, str], list[str]] = {}
    for key, obs_list in evidenced_by_key.items():
        cell_id, _class_name = key
        not_resolvable_poses = {o.pose_id for o in obs_list
                                if CoverageStatus(o.status) == CoverageStatus.NOT_RESOLVABLE}
        declared_views = declared_views_by_cell.get(cell_id)
        if declared_views is not None:
            not_resolvable_ready = bool(not_resolvable_poses) and set(declared_views) <= not_resolvable_poses
        else:
            not_resolvable_ready = len(not_resolvable_poses) > 1
        for observation in obs_list:
            status = CoverageStatus(observation.status)
            if observation.evidence_refs:
                evidence_by_pair.setdefault(key, []).extend(observation.evidence_refs)
            if status == CoverageStatus.NOT_RESOLVABLE and not not_resolvable_ready:
                # Not yet decisive: its evidence is still recorded above, but
                # it must not win the pair (see docstring) -- falls through
                # to whatever else has evidenced this pair, or UNVISITED.
                continue
            rank = _RANK[status]
            current = best.get(key)
            if current is None or rank > current[0]:
                best[key] = (rank, status)
    by_cell_and_class = tuple(
        (cell_id, class_name, best.get((cell_id, class_name), (0, CoverageStatus.UNVISITED))[1].value)
        for cell_id, class_name in required
    )
    uncovered = tuple(f"{cell_id}:{class_name}" for cell_id, class_name, status in by_cell_and_class
                      if CoverageStatus(status) not in _TERMINAL)
    all_evidence = sorted({ref for refs in evidence_by_pair.values() for ref in refs})
    # A room with no declared coverage cells at all has nothing to be
    # "complete" about -- it is undeclared, never a vacuous completeness
    # claim (see render_coverage_note).
    complete = bool(required) and not uncovered
    return CoverageProjection(by_cell_and_class, complete, uncovered, tuple(all_evidence),
                              latest_capture_at, analysed_frames, unanalysed_frames)


def digest_coverage(projection: CoverageProjection) -> str:
    """A stable replay digest: identical projections hash identically, and
    the digest is computed purely from the immutable projection fields, so
    no source-side mutation performed after ``project_coverage`` returned
    can move it."""
    body = {
        "by_cell_and_class": [list(row) for row in projection.by_cell_and_class],
        "complete": projection.complete,
        "uncovered": list(projection.uncovered),
        "evidence_refs": list(projection.evidence_refs),
        "latest_capture_at": projection.latest_capture_at,
        "analysed_frames": projection.analysed_frames,
        "unanalysed_frames": projection.unanalysed_frames,
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _capture_line(projection: CoverageProjection) -> str:
    """The grounded-answer requirement (design spec "Goals, evidence and Jev
    boundary"): every rendered answer states when the evidence is from and
    how much of it has actually finished being analysed."""
    if projection.latest_capture_at is None:
        return "Latest capture: none recorded; frames analysed: 0, unanalysed: 0."
    return (f"Latest capture: {projection.latest_capture_at}; frames analysed: "
            f"{projection.analysed_frames}, unanalysed: {projection.unanalysed_frames}.")


def render_coverage_note(projection: CoverageProjection, *, scope: Literal["ROOM", "VIEWPOINT"]) -> str:
    """Honest wording for what coverage does and does not establish.

    Never claims completeness it does not have, and never lets a scope word
    leak from the other wording: a viewpoint-scoped caveat says "viewpoint",
    a room-scoped one says "room" -- callers must not swap them. Always
    states the latest capture time and the analysed/unanalysed frame split,
    per the brief's grounded-answer requirement.
    """
    if scope not in ("ROOM", "VIEWPOINT"):
        raise ValueError("coverage scope must be ROOM or VIEWPOINT")
    subject = "the room" if scope == "ROOM" else "this viewpoint"
    capture_line = _capture_line(projection)
    if not projection.by_cell_and_class:
        return (f"{capture_line} Coverage for {subject} is undeclared: no coverage cells are "
                f"configured; a zero here does not mean none are present.")
    if projection.complete:
        return f"{capture_line} Coverage for {subject} is complete: every required cell was resolved."
    return (f"{capture_line} Coverage for {subject} is incomplete: {', '.join(projection.uncovered)} "
            f"not yet resolved; a zero here does not mean none are present.")
