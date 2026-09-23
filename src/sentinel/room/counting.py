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

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        if self.class_name not in SUPPORTED_CLASSES:
            raise ValueError("class count requires a supported class")
        if self.confirmed < 0 or self.uncertain < 0:
            raise ValueError("class count values must be non-negative")


def _static_class_names(objects, coverage: CoverageProjection) -> tuple[str, ...]:
    names = {cell_class for _, cell_class, _ in coverage.by_cell_and_class if cell_class != "person"}
    names |= {obj.class_name for obj in objects if obj.class_name != "person"}
    return tuple(sorted(names))


def _not_resolvable(class_name: str, coverage: CoverageProjection) -> bool:
    rows = [status for _, cell_class, status in coverage.by_cell_and_class if cell_class == class_name]
    return bool(rows) and all(CoverageStatus(status) == CoverageStatus.NOT_RESOLVABLE for status in rows)


def _static_counts(objects, coverage: CoverageProjection) -> tuple[ClassCount, ...]:
    counts = []
    for class_name in _static_class_names(objects, coverage):
        if _not_resolvable(class_name, coverage):
            counts.append(ClassCount(class_name, 0, 0, "NOT_OBSERVABLE"))
            continue
        matching = [obj for obj in objects if obj.class_name == class_name]
        confirmed = [obj for obj in matching if len(obj.member_estimate_ids) > 1]
        uncertain = [obj for obj in matching if len(obj.member_estimate_ids) <= 1]
        evidence = tuple(sorted({ref for obj in matching for ref in obj.evidence_refs}))
        counts.append(ClassCount(class_name, len(confirmed), len(uncertain), "GROUNDED_STATIC", evidence))
    return tuple(counts)


def _people_count(people_by_frame: tuple[FramePeople, ...]) -> ClassCount:
    if not people_by_frame:
        return ClassCount("person", 0, 0, "MAX_SIMULTANEOUS_LOWER_BOUND")
    best = max(people_by_frame, key=lambda frame: (
        frame.confirmed_count + frame.uncertain_count, frame.confirmed_count, frame.frame_id,
    ))
    return ClassCount("person", best.confirmed_count, best.uncertain_count,
                      "MAX_SIMULTANEOUS_LOWER_BOUND", (best.frame_id,))


def project_counts(objects, people_by_frame, coverage: CoverageProjection) -> tuple[ClassCount, ...]:
    """Static classes from associated objects; people from the single frame
    with the most simultaneously visible detections. Never derives a person
    count from ``objects`` -- people never enter ``associate_static``."""
    static = _static_counts(objects, coverage)
    return static + (_people_count(people_by_frame),)


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
    return f"{count.class_name}: {count.confirmed} confirmed; {count.uncertain} uncertain"


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
