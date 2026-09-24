"""Semantic room-pose vocabulary: resolves left/right x rear/front x bottom/top
positions inside a site's declared SemanticFrame into concrete executor poses.

The model only ever sees region IDs, landmark IDs and pose_ids (see
semantic_vocabulary / render_semantic_vocabulary); it never sees x/y/z
coordinates. ResolvedPose carries real coordinates and is for the executor
only. Corridor/swept-path validation between two resolved endpoints is a
later task; resolve_semantic_pose only validates that the resolved endpoint
itself is contained in the declared envelope volume.

RoomSite.validate() (called by parse_room_site) enforces the static geometry
invariants this module relies on at load time, so a RoomSite that reached
this module through the normal loading path already guarantees: the
envelope never overlaps a forbidden volume, the envelope is an axis-aligned
rectangle with respect to the declared frame axes (so every one of the 27
canonical poses is resolvable), and every declared landmark lies inside
surveyed free space and outside every forbidden volume. A RoomSite built by
hand, bypassing validate(), does not get these guarantees for free.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal, Sequence

from sentinel.models import Position
from sentinel.room.site import RoomSite, SemanticFrame, Volume3D, volume_contains

HORIZONTAL_VALUES: tuple[str, ...] = ("left", "center", "right")
DEPTH_VALUES: tuple[str, ...] = ("rear", "middle", "front")
HEIGHT_VALUES: tuple[str, ...] = ("bottom", "middle", "top")
LOOK_VALUES: tuple[str, ...] = ("toward_targets", "left", "right")

# Fractional position along an axis extent for each of its three named
# regions. These are the deterministic "insets": each region sits at the
# midpoint of its third, so the edge regions (low/high) stand one sixth of
# the extent clear of the wall and never on it. They are
# constants, applied to whatever extent a given envelope volume has -- no
# randomness, no uuid, no clock.
_LOW_FRACTION = 1 / 6
_MID_FRACTION = 1 / 2
_HIGH_FRACTION = 5 / 6

_HORIZONTAL_FRACTIONS = {"left": _LOW_FRACTION, "center": _MID_FRACTION, "right": _HIGH_FRACTION}
_DEPTH_FRACTIONS = {"rear": _LOW_FRACTION, "middle": _MID_FRACTION, "front": _HIGH_FRACTION}
_HEIGHT_FRACTIONS = {"bottom": _LOW_FRACTION, "middle": _MID_FRACTION, "top": _HIGH_FRACTION}


@dataclass(frozen=True)
class SemanticPose:
    horizontal: Literal["left", "center", "right"]
    depth: Literal["rear", "middle", "front"]
    height: Literal["bottom", "middle", "top"]
    look: Literal["toward_targets", "left", "right"] = "toward_targets"

    def __post_init__(self):
        if self.horizontal not in HORIZONTAL_VALUES:
            raise ValueError(f"unknown horizontal region: {self.horizontal}")
        if self.depth not in DEPTH_VALUES:
            raise ValueError(f"unknown depth region: {self.depth}")
        if self.height not in HEIGHT_VALUES:
            raise ValueError(f"unknown height region: {self.height}")
        if self.look not in LOOK_VALUES:
            raise ValueError(f"unknown look direction: {self.look}")


@dataclass(frozen=True)
class ResolvedPose:
    pose_id: str
    x_cm: int
    y_cm: int
    z_cm: int
    yaw_deg: int


def canonical_semantic_poses() -> tuple[SemanticPose, ...]:
    """All 27 canonical positions (default look: toward_targets)."""
    return tuple(SemanticPose(h, d, z)
                 for h in HORIZONTAL_VALUES for d in DEPTH_VALUES for z in HEIGHT_VALUES)


def _pose_id(pose: SemanticPose) -> str:
    return f"{pose.horizontal}-{pose.depth}-{pose.height}-{pose.look}"


def _centroid(polygon: tuple[tuple[float, float], ...]) -> tuple[float, float]:
    n = len(polygon)
    return (sum(p[0] for p in polygon) / n, sum(p[1] for p in polygon) / n)


def _axis_extent(polygon: tuple[tuple[float, float], ...], axis: tuple[float, float],
                  origin: tuple[float, float]) -> tuple[float, float]:
    projections = [(p[0] - origin[0]) * axis[0] + (p[1] - origin[1]) * axis[1] for p in polygon]
    return min(projections), max(projections)


def _volume_by_id(site: RoomSite, volume_id: str) -> Volume3D:
    # A semantic envelope must be a free-space volume: RoomSite.validate() enforces
    # this at load time, so a forbidden or corridor volume can never be resolved
    # into an executor-facing waypoint through this lookup.
    for volume in site.free_space_volumes:
        if volume.volume_id == volume_id:
            return volume
    raise ValueError(f"unknown envelope volume: {volume_id}")


def _require_frame(site: RoomSite) -> SemanticFrame:
    if site.semantic_frame is None:
        raise ValueError("site has no declared semantic frame")
    return site.semantic_frame


def _yaw_for_look(frame: SemanticFrame, look: str) -> int:
    if look == "toward_targets":
        vector = frame.rear_to_front_axis_xy
    elif look == "right":
        vector = frame.left_to_right_axis_xy
    elif look == "left":
        vector = (-frame.left_to_right_axis_xy[0], -frame.left_to_right_axis_xy[1])
    else:
        raise ValueError(f"unknown look direction: {look}")
    angle_deg = math.degrees(math.atan2(vector[1], vector[0]))
    return round((angle_deg + 180.0) % 360.0 - 180.0)


def resolve_semantic_pose(site: RoomSite, pose: SemanticPose) -> ResolvedPose:
    """Resolve one endpoint inside the declared SemanticFrame.

    Validates only that the resolved endpoint is contained in the declared
    envelope volume; corridor/swept-path validation is a later task.
    """
    frame = _require_frame(site)
    volume = _volume_by_id(site, frame.envelope_volume_id)
    origin = _centroid(volume.polygon_xy_cm)
    u_min, u_max = _axis_extent(volume.polygon_xy_cm, frame.left_to_right_axis_xy, origin)
    v_min, v_max = _axis_extent(volume.polygon_xy_cm, frame.rear_to_front_axis_xy, origin)
    z_min, z_max = volume.z_interval_cm
    u = u_min + _HORIZONTAL_FRACTIONS[pose.horizontal] * (u_max - u_min)
    v = v_min + _DEPTH_FRACTIONS[pose.depth] * (v_max - v_min)
    z = z_min + _HEIGHT_FRACTIONS[pose.height] * (z_max - z_min)
    x = origin[0] + u * frame.left_to_right_axis_xy[0] + v * frame.rear_to_front_axis_xy[0]
    y = origin[1] + u * frame.left_to_right_axis_xy[1] + v * frame.rear_to_front_axis_xy[1]
    resolved_position = Position(round(x), round(y), round(z))
    if not volume_contains(volume, resolved_position):
        raise ValueError("resolved endpoint escapes its declared envelope volume")
    return ResolvedPose(pose_id=_pose_id(pose), x_cm=resolved_position.x_cm, y_cm=resolved_position.y_cm,
                         z_cm=resolved_position.z_cm, yaw_deg=_yaw_for_look(frame, pose.look))


def resolve_semantic_sequence(site: RoomSite, poses: Sequence[SemanticPose]) -> tuple[ResolvedPose, ...]:
    """Resolve a caller-ordered sequence, preserving order, subsets and repetitions."""
    return tuple(resolve_semantic_pose(site, pose) for pose in poses)


#: site_id -> (dx_cm, dy_cm): the measured airborne home's offset from the
#: declared pad. Declared observation poses are placed relative to where the
#: aircraft actually took off, so a pad placed off the nominal mark does not
#: turn every observation into a long out-and-back the supervisor refuses.
_HOME_OFFSETS: dict[str, tuple[int, int]] = {}


def set_home_offset(site: RoomSite, dx_cm: float, dy_cm: float) -> None:
    """Place declared poses relative to the measured home (x/y only; the
    declared heights stay, since the altimeter is not trusted for them)."""
    _HOME_OFFSETS[site.site_id] = (round(dx_cm), round(dy_cm))


def clear_home_offset(site: RoomSite) -> None:
    _HOME_OFFSETS.pop(site.site_id, None)


def _shift_to_home(site: RoomSite, resolved: ResolvedPose) -> ResolvedPose:
    dx, dy = _HOME_OFFSETS.get(site.site_id, (0, 0))
    if not dx and not dy:
        return resolved
    volume = _volume_by_id(site, _require_frame(site).envelope_volume_id)
    xs = [p[0] for p in volume.polygon_xy_cm]
    ys = [p[1] for p in volume.polygon_xy_cm]
    x = min(max(resolved.x_cm + dx, math.ceil(min(xs))), math.floor(max(xs)))
    y = min(max(resolved.y_cm + dy, math.ceil(min(ys))), math.floor(max(ys)))
    if not volume_contains(volume, Position(x, y, resolved.z_cm)):
        return resolved
    return ResolvedPose(pose_id=resolved.pose_id, x_cm=x, y_cm=y, z_cm=resolved.z_cm, yaw_deg=resolved.yaw_deg)


def resolve_pose_id(site: RoomSite, pose_id: str) -> ResolvedPose | None:
    """Parse a declared ``pose_id`` (``"horizontal-depth-height-look"``, as
    embedded in an OBSERVE candidate ID or ``RoomSite.pose_visibility``) and
    resolve it, or ``None`` for a malformed ID or one that fails to resolve
    (no semantic frame, unknown region/look value). Shared by
    ``candidates.py`` (candidate cost/suppression) and ``controller.py``
    (fix round 2, Q5: verifying a resolved checkpoint plan's endpoint/heading
    actually match the pose its candidate ID declares, rather than trusting
    that ID alone to credit coverage)."""
    parts = pose_id.split("-")
    if len(parts) != 4:
        return None
    horizontal, depth, height, look = parts
    try:
        return _shift_to_home(site, resolve_semantic_pose(site, SemanticPose(horizontal, depth, height, look)))
    except ValueError:
        return None


def semantic_region_of(site: RoomSite, x_cm: float, y_cm: float) -> tuple[str, str, bool]:
    """Name the horizontal/depth region a room-plane point falls in.

    Returns ``(horizontal, depth, inside)``: the envelope's thirds along the
    declared left-to-right and rear-to-front axes (the same thirds
    ``resolve_semantic_pose`` places its regions at), and whether the point
    lies inside the envelope footprint at all. A point beyond the envelope
    (an object on a surface outside the flyable volume) is named after the
    nearest region, with ``inside`` False so callers can say so.
    """
    frame = _require_frame(site)
    volume = _volume_by_id(site, frame.envelope_volume_id)
    origin = _centroid(volume.polygon_xy_cm)
    names = []
    inside = True
    for axis, values in ((frame.left_to_right_axis_xy, HORIZONTAL_VALUES),
                         (frame.rear_to_front_axis_xy, DEPTH_VALUES)):
        low, high = _axis_extent(volume.polygon_xy_cm, axis, origin)
        value = (x_cm - origin[0]) * axis[0] + (y_cm - origin[1]) * axis[1]
        fraction = (value - low) / (high - low)
        inside = inside and 0.0 <= fraction <= 1.0
        names.append(values[min(2, max(0, int(fraction * 3)))] if 0.0 <= fraction < 1.0
                     else values[0] if fraction < 0.0 else values[2])
    return names[0], names[1], inside


def resolve_landmark(site: RoomSite, landmark_id: str) -> Position:
    """Executor-facing: the concrete Position behind a declared landmark ID.

    Landmarks are flyable waypoints, not merely look-at references:
    RoomSite.validate() rejects, at load time, any landmark that falls
    outside surveyed free space or inside a forbidden volume. A position
    returned here is therefore always a validated endpoint, the same
    contract resolve_semantic_pose gives for its resolved poses.
    """
    frame = _require_frame(site)
    for candidate_id, candidate_position in frame.landmarks:
        if candidate_id == landmark_id:
            return candidate_position
    raise ValueError(f"unknown landmark: {landmark_id}")


def semantic_vocabulary(site: RoomSite) -> dict:
    """Model-facing vocabulary: region IDs, pose_ids and landmark IDs only -- never coordinates."""
    frame = _require_frame(site)
    return {
        "horizontal": HORIZONTAL_VALUES,
        "depth": DEPTH_VALUES,
        "height": HEIGHT_VALUES,
        "look": LOOK_VALUES,
        "pose_ids": tuple(_pose_id(pose) for pose in canonical_semantic_poses()),
        "landmark_ids": tuple(landmark_id for landmark_id, _ in frame.landmarks),
    }


def render_semantic_vocabulary(site: RoomSite) -> str:
    """A model-ready text rendering of semantic_vocabulary().

    It carries no coordinates by construction: semantic_vocabulary() returns
    only region IDs, pose IDs and landmark IDs. It is free of digits only as
    far as the site's declared landmark IDs are -- that is a property of the
    site document, not something this function enforces.
    """
    vocabulary = semantic_vocabulary(site)
    return "\n".join(f"{key}: {', '.join(values)}" for key, values in vocabulary.items())
