"""RoomSite.validate()'s coverage-cell and declared-visibility checks.

Plan 03 Task 2 fix round 1. Before this file, none of these branches was
exercised: a coverage cell escaping surveyed free space or entering a
forbidden volume, duplicate coverage cell/pose-visibility identity, a pose
declaring visibility of an unknown cell, and a declared pose_id that does
not actually resolve.
"""
from __future__ import annotations

import pytest

from sentinel.cube.checkpoint import HomeDefinition
from sentinel.models import Position
from sentinel.room.coverage import CoverageCell
from sentinel.room.site import (
    ContingencyKind,
    ContingencyTrigger,
    RoomSite,
    RouteDefinition,
    SemanticFrame,
    TerminalContingency,
    Volume3D,
    room_site_document,
)

RECT = ((150, 120), (350, 120), (350, 260), (150, 260))
Z_BAND = (60, 140)
CELL = CoverageCell("room-floor", RECT, Z_BAND, ("phone",))
POSE_A = "center-middle-middle-toward_targets"


def _terminal_contingencies():
    return tuple(TerminalContingency(trigger.value, trigger, ContingencyKind.LAND_IN_PLACE,
        RECT, Z_BAND, "COASTING", "survey", "checked-clear", 5000) for trigger in ContingencyTrigger)


def _site(*, coverage_cells=(), pose_visibility=(), forbidden_volumes=(), semantic_frame="default"):
    if semantic_frame == "default":
        semantic_frame = SemanticFrame("room", (1.0, 0.0), (0.0, 1.0), "FRONT", ())
    return RoomSite(
        "room", "a" * 64, "b" * 64,
        (Volume3D("room", RECT, Z_BAND),), forbidden_volumes,
        (Volume3D("corridor", RECT, Z_BAND),),
        (RouteDefinition("home-route", (Position(250, 140, 90),), ("corridor",)),),
        HomeDefinition("pad", Position(250, 140, 90), 90, "home-route"),
        _terminal_contingencies(), "survey-revision-1",
        semantic_frame=semantic_frame, coverage_cells=coverage_cells, pose_visibility=pose_visibility,
    )


def test_valid_coverage_cell_and_declared_visibility_pass():
    site = _site(coverage_cells=(CELL,), pose_visibility=((POSE_A, ("room-floor",)),))
    site.validate()  # must not raise


def test_coverage_cell_escaping_surveyed_free_space_is_rejected():
    outside = CoverageCell("outside", ((0, 0), (10, 0), (10, 10), (0, 10)), Z_BAND, ("phone",))
    site = _site(coverage_cells=(outside,))
    with pytest.raises(ValueError, match="escapes surveyed free space"):
        site.validate()


def test_coverage_cell_split_across_two_disjoint_free_space_volumes_is_rejected():
    # Each corner lands in *some* free-space volume, but no single volume
    # contains every corner -- the cell's interior crosses unsurveyed ground.
    left = Volume3D("left", ((0, 0), (200, 0), (200, 200), (0, 200)), Z_BAND)
    right = Volume3D("right", ((200, 0), (400, 0), (400, 200), (200, 200)), Z_BAND)
    straddling = CoverageCell("straddling", ((100, 50), (300, 50), (300, 150), (100, 150)), Z_BAND, ("phone",))
    site = RoomSite(
        "room", "a" * 64, "b" * 64, (left, right), (),
        (Volume3D("corridor", ((0, 0), (400, 0), (400, 200), (0, 200)), Z_BAND),),
        (RouteDefinition("home-route", (Position(50, 50, 90),), ("corridor",)),),
        HomeDefinition("pad", Position(50, 50, 90), 90, "home-route"),
        _terminal_contingencies(), "survey-revision-1", coverage_cells=(straddling,),
    )
    with pytest.raises(ValueError, match="escapes surveyed free space"):
        site.validate()


def test_coverage_cell_touching_a_forbidden_volume_at_a_corner_is_rejected():
    # No semantic_frame here: the envelope-vs-forbidden-volume check that
    # runs before this one would otherwise fire first, since any forbidden
    # volume inside this fixture's RECT necessarily overlaps its envelope.
    forbidden = Volume3D("hazard", ((150, 120), (200, 120), (200, 160), (150, 160)), Z_BAND)
    site = _site(coverage_cells=(CELL,), forbidden_volumes=(forbidden,), semantic_frame=None)
    with pytest.raises(ValueError, match="enters a forbidden volume"):
        site.validate()


def test_coverage_cell_containing_a_forbidden_volume_entirely_in_its_interior_is_rejected():
    # The forbidden volume touches none of the cell's corners -- a
    # corners-only containment check would miss this entirely.
    forbidden = Volume3D("hazard", ((220, 160), (240, 160), (240, 180), (220, 180)), Z_BAND)
    site = _site(coverage_cells=(CELL,), forbidden_volumes=(forbidden,), semantic_frame=None)
    with pytest.raises(ValueError, match="enters a forbidden volume"):
        site.validate()


def test_duplicate_coverage_cell_identity_is_rejected():
    site = _site(coverage_cells=(CELL, CELL))
    with pytest.raises(ValueError, match="duplicate coverage cell identity"):
        site.validate()


def test_duplicate_declared_pose_visibility_identity_is_rejected():
    site = _site(coverage_cells=(CELL,),
                 pose_visibility=((POSE_A, ("room-floor",)), (POSE_A, ("room-floor",))))
    with pytest.raises(ValueError, match="duplicate declared pose visibility identity"):
        site.validate()


def test_pose_visibility_naming_an_unknown_cell_is_rejected():
    site = _site(coverage_cells=(CELL,), pose_visibility=((POSE_A, ("no-such-cell",)),))
    with pytest.raises(ValueError, match="unknown coverage cell"):
        site.validate()


def test_pose_visibility_with_a_malformed_pose_id_is_rejected():
    site = _site(coverage_cells=(CELL,), pose_visibility=(("not-a-real-pose-id", ("room-floor",)),))
    with pytest.raises(ValueError, match="does not resolve"):
        site.validate()


def test_pose_visibility_requires_a_semantic_frame_to_resolve_against():
    # A syntactically well-formed pose_id with no semantic_frame at all
    # cannot resolve to anything -- this must be rejected, not silently
    # accepted with an unresolvable pose_id.
    site = _site(coverage_cells=(CELL,), pose_visibility=((POSE_A, ("room-floor",)),), semantic_frame=None)
    with pytest.raises(ValueError, match="does not resolve"):
        site.validate()


def test_room_site_document_omits_empty_coverage_fields_like_semantic_frame():
    # Matches the existing semantic_frame precedent: a site that declares
    # neither field keeps exactly the document (and digest) it had before
    # these fields existed, so persisted calibration_hashes/site_sha256
    # bindings for an unchanged site do not break.
    site = _site()
    document = room_site_document(site)
    assert "coverage_cells" not in document
    assert "pose_visibility" not in document


def test_room_site_document_includes_declared_coverage_fields():
    site = _site(coverage_cells=(CELL,), pose_visibility=((POSE_A, ("room-floor",)),))
    document = room_site_document(site)
    assert document["coverage_cells"]
    assert document["pose_visibility"]
