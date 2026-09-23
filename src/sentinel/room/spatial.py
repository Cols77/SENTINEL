"""Support-aware spatial estimation for one detection in one frame.

Every position comes from the *bottom* of the detection box -- the point
where an object resting on a horizontal surface actually touches it -- rayed
onto that surface's declared height (design precedent:
``sentinel.survey.geometry.ground_point``, which this module reuses for the
pinhole arithmetic). There is no fallback that guesses a range from the box
center and an assumed object size: an object whose support is not declared
(held, or resting on an undeclared surface) gets no position at all, never a
manufactured one.

A stale pose invalidates the estimate outright: a bottom-box ray is only as
good as the camera pose it was measured from, and a pose known to be too old
to trust is not a basis for a room-space position. A commanded (dead-reckoned,
not measured) pose is not invalidated, but its estimate carries a much wider
covariance, because the *camera's own* position is then the dominant source
of error rather than the pixel geometry.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import math
from typing import Literal

from sentinel.camera import CameraModel
from sentinel.room.ingest import DetectionEvidence, full_frame_bbox
from sentinel.survey.geometry import ground_point

#: No per-class object-height table exists anywhere in this codebase, so a
#: resting object's top is bounded by this generic allowance above its
#: support surface rather than a class-specific figure. Documented as a
#: simplification in the task report.
_RESTING_HEIGHT_BAND_CM = 30.0

#: Per-axis standard deviation floor (cm) for a single, unmerged sighting.
#: Order of magnitude matches survey.merge's measured-frame lateral floor;
#: this module has no cross-view check of its own to tighten it further.
_BASE_SIGMA_CM = 20.0
_COMMANDED_SIGMA_MULTIPLIER = 2.0


class SupportKind(StrEnum):
    FLOOR = "FLOOR"
    DECLARED_HORIZONTAL_PLANE = "DECLARED_HORIZONTAL_PLANE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SupportDeclaration:
    """What a class-specific detection is declared to rest on, if anything.

    UNKNOWN covers both a genuinely undeclared surface and a held object:
    either way there is no fixed plane to ray onto, so estimate_detection
    always returns None for it rather than falling back to a guessed range.
    """
    support: SupportKind
    support_plane_id: str | None = None
    plane_height_cm: float | None = None

    def __post_init__(self) -> None:
        SupportKind(self.support)
        if self.support == SupportKind.DECLARED_HORIZONTAL_PLANE:
            if not self.support_plane_id or self.plane_height_cm is None \
                    or not math.isfinite(self.plane_height_cm):
                raise ValueError("declared horizontal plane support needs a named plane and height")
        elif self.support_plane_id not in (None, "FLOOR"):
            raise ValueError("only a declared horizontal plane may name a support plane")


@dataclass(frozen=True)
class PoseEvidence:
    """The camera pose a detection's frame was captured from.

    ``stale`` and ``pose_provenance`` are supplied by the caller (the
    session's own pose-freshness and measured/commanded tracking) -- this
    module has no clock of its own, matching the rest of ``sentinel.survey``'s
    pure, I/O-free geometry.
    """
    x_cm: float
    y_cm: float
    z_cm: float
    heading_deg: float
    look_deg: float = 0.0
    pose_provenance: Literal["MEASURED", "COMMANDED"] = "MEASURED"
    captured_at: str = ""
    stale: bool = False

    def __post_init__(self) -> None:
        if self.pose_provenance not in ("MEASURED", "COMMANDED"):
            raise ValueError("invalid pose provenance")
        if not all(math.isfinite(v) for v in (self.x_cm, self.y_cm, self.z_cm, self.heading_deg, self.look_deg)):
            raise ValueError("invalid pose geometry")
        if not self.captured_at:
            raise ValueError("pose evidence requires a capture timestamp")


@dataclass(frozen=True)
class SpatialEstimate:
    estimate_id: str
    source_detection_id: str
    class_name: str
    x_cm: float
    y_cm: float
    z_interval_cm: tuple[float, float]
    xy_covariance_cm2: tuple[float, float, float]
    support: SupportKind
    support_plane_id: str | None
    captured_at: str
    pose_provenance: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "z_interval_cm", tuple(self.z_interval_cm))
        object.__setattr__(self, "xy_covariance_cm2", tuple(self.xy_covariance_cm2))
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        if not self.estimate_id or not self.source_detection_id or not self.class_name:
            raise ValueError("spatial estimate requires identity and class")
        if not all(math.isfinite(v) for v in (self.x_cm, self.y_cm, *self.z_interval_cm, *self.xy_covariance_cm2)):
            raise ValueError("invalid spatial estimate geometry")
        if len(self.z_interval_cm) != 2 or self.z_interval_cm[0] > self.z_interval_cm[1]:
            raise ValueError("invalid spatial estimate height band")
        if len(self.xy_covariance_cm2) != 3 or self.xy_covariance_cm2[0] <= 0 or self.xy_covariance_cm2[1] <= 0:
            raise ValueError("invalid spatial estimate covariance")
        SupportKind(self.support)
        if not self.captured_at:
            raise ValueError("spatial estimate requires a capture timestamp")
        if not self.evidence_refs:
            raise ValueError("spatial estimate requires evidence")


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode("utf-8")).hexdigest()


def _estimate_id(detection_id: str, support: SupportKind, support_plane_id: str | None) -> str:
    """A pure function of identity and support, so re-estimating the same
    detection under the same support declaration is always the same ID."""
    return "estimate:" + _digest({"detection_id": detection_id, "support": support.value,
                                  "support_plane_id": support_plane_id})


def _bottom_center_px(detection: DetectionEvidence) -> tuple[float, float]:
    x, y, width, height = full_frame_bbox(detection)
    return (x + width / 2.0, y + height)


def estimate_detection(detection: DetectionEvidence, pose: PoseEvidence,
                        support: SupportDeclaration, camera: CameraModel) -> SpatialEstimate | None:
    """Ray the detection's bottom-box point onto its declared support plane.

    Returns None for unknown/held support, a stale pose, or a box whose
    bottom point is not on the declared plane at all (at/above the horizon
    for that pitch) -- never a manufactured position.
    """
    if support.support == SupportKind.UNKNOWN or pose.stale:
        return None
    if support.support == SupportKind.FLOOR:
        delta_z_cm = pose.z_cm
        support_z_cm = 0.0
        support_plane_id = "FLOOR"
    else:
        delta_z_cm = pose.z_cm - support.plane_height_cm
        support_z_cm = support.plane_height_cm
        support_plane_id = support.support_plane_id
    if delta_z_cm <= 0:
        return None
    bottom_u, bottom_v = _bottom_center_px(detection)
    point_bbox = {"x": bottom_u, "y": bottom_v, "width": 0.0, "height": 0.0}
    try:
        lateral_cm, forward_cm = ground_point(point_bbox, delta_z_cm, camera)
    except ValueError:
        return None
    angle = math.radians(pose.heading_deg + pose.look_deg)
    x_cm = pose.x_cm + forward_cm * math.sin(angle) + lateral_cm * math.cos(angle)
    y_cm = pose.y_cm + forward_cm * math.cos(angle) - lateral_cm * math.sin(angle)
    sigma_cm = _BASE_SIGMA_CM * (_COMMANDED_SIGMA_MULTIPLIER if pose.pose_provenance == "COMMANDED" else 1.0)
    variance_cm2 = sigma_cm * sigma_cm
    z_interval_cm = (support_z_cm, support_z_cm + _RESTING_HEIGHT_BAND_CM)
    return SpatialEstimate(
        estimate_id=_estimate_id(detection.detection_id, support.support, support_plane_id),
        source_detection_id=detection.detection_id,
        class_name=detection.class_name,
        x_cm=x_cm, y_cm=y_cm,
        z_interval_cm=z_interval_cm,
        xy_covariance_cm2=(variance_cm2, variance_cm2, 0.0),
        support=support.support,
        support_plane_id=support_plane_id,
        captured_at=pose.captured_at,
        pose_provenance=pose.pose_provenance,
        evidence_refs=(detection.detection_id,),
    )
