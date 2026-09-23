"""Grounded count evidence for one observed frame, and the session COUNT
projection built from it (Plan 03 residual task R, finding F2).

``ground_frame`` runs every detection of one authorized perception result
through ``spatial.estimate_detection`` at the measured pose the checkpoint
reported, and returns a ``FrameCountEvidence``: the support-aware spatial
estimates, the frame's people split, and every static detection that could
not be placed (with the reason). The controller persists it verbatim on the
``OBSERVE_SUCCEEDED`` outcome event, so everything a later count needs is
append-only session state -- the pose, camera model and support declarations
used at capture time are never re-read later.

``project_objective_count`` is pure: it associates every persisted estimate
of the frames in an objective's scope (``association.associate_static``),
projects them into confirmed/uncertain classes and a people lower bound
(``counting.project_counts``), and pairs them with the objective's coverage
projection. Replaying the same persisted frames always yields the same
counts.

Honesty rules this module adds on top of counting.py:

* A detection is never placed from a guess: no count geometry, an undeclared
  support, a stale pose, a frame whose size is not the camera model's, or a
  box that does not meet the declared plane each leave it *unplaced* -- and
  unplaced detections are reported per class, never silently dropped.
* A low-confidence sighting may create or join an object, but it never
  counts as a confirming view: uncertain detections stay uncertain however
  many views repeat them.
* People never enter association; the per-frame split is high-confidence
  (confirmed) versus medium/low (uncertain), and the count is the best single
  frame's lower bound.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, replace
from typing import Literal

from sentinel.camera import CameraModel

from .association import associate_static
from .counting import ClassCount, FramePeople, project_counts, render_count_answer
from .coverage import SUPPORTED_CLASSES, CoverageProjection, render_coverage_note
from .spatial import PoseEvidence, SpatialEstimate, SupportDeclaration, SupportKind, estimate_detection

_UNDECLARED = SupportDeclaration(SupportKind.UNKNOWN)

UnplacedReason = Literal["NO_COUNT_GEOMETRY", "SUPPORT_UNDECLARED", "STALE_POSE", "FRAME_CAMERA_MISMATCH",
                         "NOT_ON_SUPPORT_PLANE", "INVALID_GEOMETRY"]
_REASONS = frozenset(UnplacedReason.__args__)


@dataclass(frozen=True)
class CountGeometry:
    """The camera model and per-class support declarations a session places
    static detections with. A class not named here has undeclared support,
    so its detections are reported unplaced rather than rayed onto a guessed
    plane. ``person`` is never placed (people are counted per frame)."""
    camera: CameraModel
    supports: tuple[tuple[str, SupportDeclaration], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "supports", tuple(tuple(item) for item in self.supports))
        if not isinstance(self.camera, CameraModel):
            raise ValueError("count geometry requires a camera model")
        names = [name for name, _ in self.supports]
        if len(set(names)) != len(names):
            raise ValueError("count geometry declares a class support twice")
        for name, support in self.supports:
            if name not in SUPPORTED_CLASSES or name == "person":
                raise ValueError("count geometry declares support only for supported static classes")
            if not isinstance(support, SupportDeclaration):
                raise ValueError("count geometry requires support declarations")

    def support_for(self, class_name: str) -> SupportDeclaration:
        return dict(self.supports).get(class_name, _UNDECLARED)


@dataclass(frozen=True)
class UnplacedDetection:
    detection_id: str
    class_name: str
    reason: str

    def __post_init__(self) -> None:
        if not self.detection_id or self.class_name not in SUPPORTED_CLASSES or self.reason not in _REASONS:
            raise ValueError("invalid unplaced detection")


@dataclass(frozen=True)
class FrameCountEvidence:
    """Everything one observed frame contributes to a count, as persisted."""
    observation_id: str
    frame_id: str
    pose_id: str
    captured_at: str
    pose: PoseEvidence
    estimates: tuple[SpatialEstimate, ...] = ()
    low_confidence_estimate_ids: tuple[str, ...] = ()
    people_confirmed: int = 0
    people_uncertain: int = 0
    unplaced: tuple[UnplacedDetection, ...] = ()

    def __post_init__(self) -> None:
        for name in ("estimates", "low_confidence_estimate_ids", "unplaced"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if not all((self.observation_id, self.frame_id, self.pose_id, self.captured_at)):
            raise ValueError("frame count evidence requires identity, pose and time")
        if not isinstance(self.pose, PoseEvidence):
            raise ValueError("frame count evidence requires its capture pose")
        if any(not isinstance(e, SpatialEstimate) for e in self.estimates) \
                or any(not isinstance(u, UnplacedDetection) for u in self.unplaced):
            raise ValueError("invalid frame count evidence")
        ids = {e.estimate_id for e in self.estimates}
        if len(ids) != len(self.estimates) or not set(self.low_confidence_estimate_ids) <= ids:
            raise ValueError("frame count evidence estimate identities are inconsistent")
        for value in (self.people_confirmed, self.people_uncertain):
            if type(value) is not int or value < 0:
                raise ValueError("frame people counts must be non-negative integers")

    def to_payload(self) -> dict:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: dict) -> FrameCountEvidence:
        estimates = tuple(SpatialEstimate(**{**item, "support": SupportKind(item["support"])})
                          for item in payload["estimates"])
        return cls(payload["observation_id"], payload["frame_id"], payload["pose_id"], payload["captured_at"],
                   PoseEvidence(**payload["pose"]), estimates, tuple(payload["low_confidence_estimate_ids"]),
                   payload["people_confirmed"], payload["people_uncertain"],
                   tuple(UnplacedDetection(**item) for item in payload["unplaced"]))


def _place(detection, qualified_id, pose: PoseEvidence, geometry: CountGeometry | None):
    """Return ``(estimate, None)`` or ``(None, reason)`` for one static detection."""
    if geometry is None:
        return None, "NO_COUNT_GEOMETRY"
    support = geometry.support_for(detection.class_name)
    if support.support == SupportKind.UNKNOWN:
        return None, "SUPPORT_UNDECLARED"
    if pose.stale:
        return None, "STALE_POSE"
    if (detection.frame_width_px, detection.frame_height_px) != (geometry.camera.width, geometry.camera.height):
        # The camera model's intrinsics describe its own image size only.
        return None, "FRAME_CAMERA_MISMATCH"
    try:
        estimate = estimate_detection(replace(detection, detection_id=qualified_id), pose, support, geometry.camera)
    except Exception:
        return None, "INVALID_GEOMETRY"
    return (estimate, None) if estimate is not None else (None, "NOT_ON_SUPPORT_PLANE")


def ground_frame(detections, *, observation_id: str, frame_id: str, pose_id: str, pose: PoseEvidence,
                 geometry: CountGeometry | None) -> FrameCountEvidence:
    """Place every static detection of one frame, split its people, and
    record every detection that could not be placed.

    Detection IDs are delivery-local, so each is qualified by its
    observation (``<observation_id>/<detection_id>``) before it becomes a
    spatial estimate's ``source_detection_id``: two frames' ``d1`` must never
    collide inside one association call. Classes outside the supported
    detector vocabulary are not countable and are ignored here (they remain
    in the session's historical observation evidence)."""
    estimates, low, unplaced = [], [], []
    people = Counter()
    for detection in detections:
        if detection.class_name not in SUPPORTED_CLASSES:
            continue
        if detection.class_name == "person":
            people["confirmed" if detection.confidence_band == "high" else "uncertain"] += 1
            continue
        qualified_id = f"{observation_id}/{detection.detection_id}"
        estimate, reason = _place(detection, qualified_id, pose, geometry)
        if estimate is None:
            unplaced.append(UnplacedDetection(qualified_id, detection.class_name, reason))
            continue
        estimates.append(estimate)
        if detection.confidence_band == "low":
            low.append(estimate.estimate_id)
    return FrameCountEvidence(observation_id, frame_id, pose_id, pose.captured_at, pose, tuple(estimates),
                              tuple(low), people["confirmed"], people["uncertain"], tuple(unplaced))


@dataclass(frozen=True)
class ObjectiveCount:
    """The COUNT fact for one objective: grounded counts plus the coverage
    they were projected against, and the frames they came from."""
    objective_id: str
    scope: Literal["ROOM", "VIEWPOINT"]
    pose_id: str | None
    classes: tuple[str, ...]
    frame_ids: tuple[str, ...]
    counts: tuple[ClassCount, ...]
    coverage: CoverageProjection

    def to_payload(self) -> dict:
        coverage = self.coverage
        return {
            "objective_id": self.objective_id, "scope": self.scope, "pose_id": self.pose_id,
            "classes": list(self.classes), "frame_ids": list(self.frame_ids),
            "counts": [asdict(count) for count in self.counts],
            "coverage": {
                "by_cell_and_class": [list(row) for row in coverage.by_cell_and_class],
                "complete": coverage.complete, "uncovered": list(coverage.uncovered),
                "evidence_refs": list(coverage.evidence_refs), "latest_capture_at": coverage.latest_capture_at,
                "analysed_frames": coverage.analysed_frames, "unanalysed_frames": coverage.unanalysed_frames,
            },
        }


def project_objective_count(objective, frames, coverage: CoverageProjection, *,
                            scope: Literal["ROOM", "VIEWPOINT"], pose_id: str | None = None) -> ObjectiveCount:
    """Associate the frames' estimates and project the objective's counts.

    ``frames`` is every persisted frame in the objective's scope, in the
    order they were observed. Counts are restricted to the objective's own
    target classes (when it names any), so a count of computers never
    reports phones or people it happened to see."""
    if scope not in ("ROOM", "VIEWPOINT"):
        raise ValueError("count scope must be ROOM or VIEWPOINT")
    frames = tuple(frames)
    estimates = tuple(estimate for frame in frames for estimate in frame.estimates)
    low = {estimate_id for frame in frames for estimate_id in frame.low_confidence_estimate_ids}
    corroborating = {estimate.estimate_id: estimate.view_id or estimate.captured_at
                     for estimate in estimates if estimate.estimate_id not in low}
    people = tuple(FramePeople(frame.frame_id, frame.pose_id, frame.captured_at,
                               frame.people_confirmed, frame.people_uncertain)
                   for frame in frames if frame.people_confirmed or frame.people_uncertain)
    unplaced = Counter(item.class_name for frame in frames for item in frame.unplaced)
    classes = tuple(objective.target_classes)
    counts = project_counts(associate_static((), estimates), people, coverage, requested_classes=classes,
                            corroborating_views=corroborating, unplaced_by_class=unplaced)
    if classes:
        counts = tuple(count for count in counts if count.class_name in classes)
    return ObjectiveCount(objective.objective_id, scope, pose_id, classes,
                          tuple(frame.frame_id for frame in frames), counts, coverage)


def render_objective_count(count: ObjectiveCount) -> str:
    """The objective's section of a terminal answer: a scope header, one
    line per class and the scope-worded coverage caveat."""
    subject = ("room-wide count" if count.scope == "ROOM"
               else f"count from declared viewpoint {count.pose_id}")
    header = f"Objective {count.objective_id} ({subject}):"
    if not count.counts:
        return "\n".join((header, "No supported class was requested or observed.",
                          render_coverage_note(count.coverage, scope=count.scope)))
    return header + "\n" + render_count_answer(count.counts, count.coverage, scope=count.scope)
