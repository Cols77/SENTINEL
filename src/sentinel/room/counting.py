"""Project associated static objects and per-frame people into honest,
citeable counts.

Static classes are grounded: a class is ``GROUNDED_STATIC`` from
``associate_static``'s objects, ``confirmed`` when an object was seen from
more than one distinct view and ``uncertain`` when only one view has ever
placed it (design precedent: ``sentinel.survey.report.class_counts``'s
confirmed/uncertain split, applied here to positions instead of stop
counts). A class the declared coverage geometry can never resolve at all is
reported ``NOT_OBSERVABLE`` rather than a confident zero.

People are never run through ``associate_static`` -- design spec 4.3, kept
from ``sentinel.survey.merge``'s ``MOBILE_CLASSES`` precedent: someone seen
from two viewpoints may not be the same person, so summing across views
would double-count and averaging positions would be meaningless. Instead the
frame with the most simultaneously visible people is a lower bound on how
many people are in the room, split into the confirmed count and any
additional, lower-confidence detections in that same frame that could not be
told apart with certainty.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from sentinel.room.coverage import CoverageProjection, CoverageStatus, SUPPORTED_CLASSES, render_coverage_note


@dataclass(frozen=True)
class FramePeople:
    """One frame's person detections, split by whether each was confidently
    distinguished from every other person in that same frame."""
    frame_id: str
    pose_id: str
    captured_at: str
    confirmed_count: int
    uncertain_count: int = 0

    def __post_init__(self) -> None:
        if not self.frame_id or not self.pose_id or not self.captured_at:
            raise ValueError("frame people requires identity, pose and time")
        if (type(self.confirmed_count) is not int or self.confirmed_count < 0
                or type(self.uncertain_count) is not int or self.uncertain_count < 0):
            raise ValueError("frame people counts must be non-negative integers")


@dataclass(frozen=True)
class ClassCount:
    class_name: str
    confirmed: int
    uncertain: int
    method: Literal["GROUNDED_STATIC", "MAX_SIMULTANEOUS_LOWER_BOUND", "NOT_OBSERVABLE"]
    evidence_refs: tuple[str, ...] = ()
    #: Residual F8: the cells whose declared geometry can never resolve this
    #: class. Whatever was counted, instances there may be missing, so the
    #: rendered line carries that caveat instead of reading as a resolved
    #: count.
    not_resolvable_cells: tuple[str, ...] = ()
    #: Residual F2: detections of this class that were seen but could not be
    #: placed in room coordinates (support undeclared, off the support
    #: plane, stale pose, ...). They cannot be associated, so they are never
    #: counted as objects -- but they are reported, never silently dropped.
    unplaced: int = 0
    #: Fix round 4 (Q3): true when the declared coverage geometry has no row
    #: at all for this class -- no cell anywhere ever required or resolved
    #: it. Distinct from ``not_resolvable_cells`` (which names cells that
    #: *do* declare this class but can never resolve it): here nothing was
    #: ever checked for it, so a zero must never read as a resolved count.
    no_declared_coverage: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        object.__setattr__(self, "not_resolvable_cells", tuple(self.not_resolvable_cells))
        if self.class_name not in SUPPORTED_CLASSES:
            raise ValueError("class count requires a supported class")
        if self.confirmed < 0 or self.uncertain < 0 or type(self.unplaced) is not int or self.unplaced < 0:
            raise ValueError("class count values must be non-negative")


def _static_class_names(objects, coverage: CoverageProjection, requested_classes: tuple[str, ...],
                        unplaced_by_class: Mapping[str, int]) -> tuple[str, ...]:
    names = {cell_class for _, cell_class, _ in coverage.by_cell_and_class if cell_class != "person"}
    names |= {obj.class_name for obj in objects if obj.class_name != "person"}
    names |= {name for name in requested_classes if name != "person"}
    names |= {name for name, count in unplaced_by_class.items() if name != "person" and count}
    return tuple(sorted(names))


def _not_resolvable(class_name: str, coverage: CoverageProjection) -> bool:
    rows = [status for _, cell_class, status in coverage.by_cell_and_class if cell_class == class_name]
    return bool(rows) and all(CoverageStatus(status) == CoverageStatus.NOT_RESOLVABLE for status in rows)


def _not_resolvable_cells(class_name: str, coverage: CoverageProjection) -> tuple[str, ...]:
    return tuple(sorted(cell_id for cell_id, cell_class, status in coverage.by_cell_and_class
                        if cell_class == class_name
                        and CoverageStatus(status) == CoverageStatus.NOT_RESOLVABLE))


def _view_count(obj, corroborating_views: Mapping[str, str] | None) -> int:
    """How many distinct views corroborate ``obj``. Without a
    ``corroborating_views`` mapping every member view counts; with one, only
    the member estimates it names do (residual F2: a low-confidence sighting
    is evidence the object may exist, never a confirming second view)."""
    if corroborating_views is None:
        return len(set(obj.member_view_keys))
    return len({corroborating_views[estimate_id] for estimate_id in obj.member_estimate_ids
                if estimate_id in corroborating_views})


def _static_counts(objects, coverage: CoverageProjection, requested_classes: tuple[str, ...],
                   corroborating_views: Mapping[str, str] | None = None,
                   unplaced_by_class: Mapping[str, int] | None = None) -> tuple[ClassCount, ...]:
    unplaced_by_class = dict(unplaced_by_class or {})
    counts = []
    for class_name in _static_class_names(objects, coverage, requested_classes, unplaced_by_class):
        matching = [obj for obj in objects if obj.class_name == class_name]
        unplaced = unplaced_by_class.get(class_name, 0)
        has_coverage_rows = any(cell_class == class_name for _, cell_class, _ in coverage.by_cell_and_class)
        if _not_resolvable(class_name, coverage) and not matching and not unplaced:
            # A NOT_RESOLVABLE verdict with no associated evidence at all is
            # a pure geometry fact: this class can never be resolved here,
            # so an honest zero is reported instead of "0 confirmed".
            counts.append(ClassCount(class_name, 0, 0, "NOT_OBSERVABLE"))
            continue
        # Fix round 3 (F8): a NOT_RESOLVABLE verdict is a caveat about
        # *remaining* coverage completeness, never a license to discard
        # objects evidence has already associated -- those objects are
        # reported through the ordinary confirmed/uncertain split below,
        # exactly as they would be for a class whose coverage is fully
        # resolved. "Confirmed" means seen from more than one distinct view
        # (module docstring), not merely more than once -- two estimates
        # from the same unmoved pose are one view. ``member_view_keys``
        # distinguishes them when the source estimates carried a view
        # identity; it falls back to capture time for estimates that did
        # not (see association.py).
        confirmed = [obj for obj in matching if _view_count(obj, corroborating_views) > 1]
        uncertain = [obj for obj in matching if _view_count(obj, corroborating_views) <= 1]
        evidence = tuple(sorted({ref for obj in matching for ref in obj.evidence_refs}))
        counts.append(ClassCount(class_name, len(confirmed), len(uncertain), "GROUNDED_STATIC", evidence,
                                 _not_resolvable_cells(class_name, coverage), unplaced, not has_coverage_rows))
    return tuple(counts)


def _people_count(people_by_frame: tuple[FramePeople, ...]) -> ClassCount:
    if not people_by_frame:
        return ClassCount("person", 0, 0, "MAX_SIMULTANEOUS_LOWER_BOUND")
    best = max(people_by_frame, key=lambda frame: (
        frame.confirmed_count + frame.uncertain_count, frame.confirmed_count, frame.frame_id,
    ))
    return ClassCount("person", best.confirmed_count, best.uncertain_count,
                      "MAX_SIMULTANEOUS_LOWER_BOUND", (best.frame_id,))


def project_counts(objects, people_by_frame, coverage: CoverageProjection, *,
                   requested_classes: tuple[str, ...] = (),
                   corroborating_views: Mapping[str, str] | None = None,
                   unplaced_by_class: Mapping[str, int] | None = None) -> tuple[ClassCount, ...]:
    """Static classes from associated objects; people from the single frame
    with the most simultaneously visible detections. Never derives a person
    count from ``objects`` -- people never enter ``associate_static``.

    ``requested_classes`` is optional and additive: every class it names
    (other than "person", which always has its own line) gets a rendered
    line even when neither the coverage cells nor any associated object
    mention it yet -- otherwise a class with no declared cells and no
    sightings silently has no line at all instead of an honest zero.

    Fix round 3 (F8): the person line is emitted only when "person" was
    actually requested or at least one person frame was actually observed
    -- never manufactured out of nothing for a request that never mentioned
    people and a session that never saw any.

    Residual F2: ``corroborating_views`` (estimate_id -> view key), when
    given, restricts which member estimates may count toward an object's
    "more than one distinct view" confirmation -- the session projection
    omits low-confidence sightings from it, so uncertain detections stay
    uncertain however many views repeat them. ``unplaced_by_class`` reports
    detections that were seen but could not be placed, per class."""
    static = _static_counts(objects, coverage, tuple(requested_classes), corroborating_views, unplaced_by_class)
    if people_by_frame or "person" in requested_classes:
        return static + (_people_count(people_by_frame),)
    return static


def _render_line(count: ClassCount) -> str:
    if count.method == "NOT_OBSERVABLE":
        return f"{count.class_name}: not observable"
    if count.method == "MAX_SIMULTANEOUS_LOWER_BOUND":
        noun = "person" if count.confirmed == 1 and count.uncertain == 0 else "people"
        line = f"at least {count.confirmed} {noun} visible"
        if count.uncertain:
            detail = "detection is" if count.uncertain == 1 else "detections are"
            line += f"; {count.uncertain} additional {detail} uncertain"
        return line
    line = f"{count.class_name}: {count.confirmed} confirmed; {count.uncertain} uncertain"
    if count.unplaced:
        detail = ("detection could not be placed in room coordinates and is not counted" if count.unplaced == 1
                  else "detections could not be placed in room coordinates and are not counted")
        line += f"; {count.unplaced} {detail}"
    if count.no_declared_coverage:
        line += "; no declared coverage for this class; a zero does not mean none are present"
    if count.not_resolvable_cells:
        line += (f"; not resolvable from the declared views in {', '.join(count.not_resolvable_cells)}, "
                 f"so this class may be undercounted there")
    return line


def render_count_answer(counts: tuple[ClassCount, ...], coverage: CoverageProjection, *,
                        scope: Literal["ROOM", "VIEWPOINT"]) -> str:
    """One line per class plus an honest coverage caveat. Never lets a
    confirmed/uncertain interval or the people lower-bound wording imply
    more certainty than the underlying evidence supports."""
    if not counts:
        raise ValueError("a count answer requires at least one class count")
    lines = [_render_line(count) for count in counts]
    lines.append(render_coverage_note(coverage, scope=scope))
    return "\n".join(lines)
