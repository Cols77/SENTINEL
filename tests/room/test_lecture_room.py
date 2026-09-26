"""The lecture-room venue: the operator's 2026-09-26 tape measurements.

Measured on site, with the origin taken between the two AprilTags:

* two tags, 69.4 cm apart along x, tag centres 49.5 cm above the floor;
* 350 cm of usable depth on y, measured back from the tag plane;
* usable width x -200 cm to +200 cm about the tag midpoint.

The internal frame keeps this codebase's convention (positive coordinates,
the tag line at the far y, tags facing -y), so the operator's origin sits at
x 250, y 400: usable floor is x 50-450 by y 50-400. Home keeps CanningSt's
210 cm standoff from the tags, which is the distance the tag-facing poses
were validated at, so the extra space is all behind and beside the aircraft.

demo5's five viewpoints fly here unchanged -- the room is larger than
CanningSt in every direction, so every margin that refused a zone in live
l3-20260926-142659 grows rather than shrinks.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts import room_live_prepare
from sentinel.cube.coverage import check_tag_map_matches_site
from sentinel.cube.site import load_site
from sentinel.pose.tagmap import load_tag_map
from sentinel.room.semantic import resolve_pose_id
from sentinel.room.site import parse_room_site

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "room_agent" / "v1"
SITE = FIXTURES / "lecture-room-site.json"
CUBE_SITE = ROOT / "docs" / "lecture-room-site.json"
TAG_MAP = ROOT / "docs" / "lecture-room-map.json"
DRIFT = FIXTURES / "profiles" / "canningst-demo-drift.json"

#: The operator's origin (between the tags) in internal coordinates.
ORIGIN_X_CM, TAG_LINE_Y_CM = 250, 400
TAG_SEPARATION_CM = 69.4
TAG_HEIGHT_M = 0.495

DEMO5_POSES = [
    "right-middle-top-toward_targets",
    "left-middle-top-toward_targets",
    "center-middle-top-left",
    "center-middle-top-right",
    "center-middle-top-backward",
]


def _doc(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _volume(site, volume_id):
    return next(v for v in site.free_space_volumes if v.volume_id == volume_id)


def _bounds(volume):
    xs = [p[0] for p in volume.polygon_xy_cm]
    ys = [p[1] for p in volume.polygon_xy_cm]
    return min(xs), max(xs), min(ys), max(ys)


def test_the_two_tags_sit_where_the_operator_measured_them():
    cube = load_site(CUBE_SITE)
    assert cube.line_y_m * 100 == TAG_LINE_Y_CM
    xs = sorted(stand.x_m * 100 for stand in cube.stands)
    assert round(xs[1] - xs[0], 1) == TAG_SEPARATION_CM
    # Symmetric about the operator's origin, so their "34.7 either side" holds.
    assert round((xs[0] + xs[1]) / 2, 1) == ORIGIN_X_CM
    assert [stand.heights_m for stand in cube.stands] == [(TAG_HEIGHT_M,), (TAG_HEIGHT_M,)]


def test_the_tag_map_is_exactly_what_the_cube_site_describes():
    cube, tag_map = load_site(CUBE_SITE), load_tag_map(str(TAG_MAP))
    # Zero tolerance: this map is generated from the site file, not measured
    # separately, so the two can never disagree the way CanningSt's do.
    check_tag_map_matches_site(tag_map, cube, tolerance_cm=0.0)
    # The tags physically mounted here are printed images 00002 and 00004
    # (live l3-20260926-171615 saw only tag 2: STAND_1's declared tag 0 never
    # matched the mounted tag 4, so no burst gave a two-tag home fix). Seen
    # from the pad facing the tags, 00004 is the left (smaller-x) tag — the
    # operator confirmed the sides, and that run's single-tag solves against
    # 00002 sat ~69 cm left of the pad until 00002 was mapped to the far stand.
    assert {tag["tag_id"]: tag["x_units"] for tag in tag_map.doc["tags"]} == {4: 21.53, 2: 28.47}
    assert {tag["z_units"] for tag in tag_map.doc["tags"]} == {TAG_HEIGHT_M}
    # The camera is the same aircraft's: a real calibration, never nominal.
    assert tag_map.calibration_source == "chessboard"


def test_the_room_site_binds_this_room_s_tags_and_the_calibration_domain():
    site = parse_room_site(_doc(SITE))
    assert site.site_id == "lecture-room"
    # The tag map is this room's, and the binding is checked against the file
    # bytes when the flight loads it (live_stack.load_bound_tag_map).
    raw = TAG_MAP.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(raw).hexdigest() == site.tag_map_sha256
    # cube_site_sha256 is the *calibration domain*, not this room's survey:
    # the committed energy and flight profiles declare that digest, and
    # SafetySupervisor.validate_bindings refuses a flight whose active site
    # does not match it ("energy profile site does not match the active
    # site", live 2026-09-26). This room reuses those CanningSt profiles, so
    # it must declare their domain.
    canningst = json.loads((FIXTURES / "canningst-demo5-site.json").read_text(encoding="utf-8"))
    assert site.cube_site_sha256 == canningst["cube_site_sha256"]
    energy = json.loads((FIXTURES / "profiles" / "canningst-energy.json").read_text(encoding="utf-8"))
    assert site.cube_site_sha256 == energy["site_sha256"]


def test_the_usable_floor_is_the_measured_400_by_350():
    site = parse_room_site(_doc(SITE))
    min_x, max_x, min_y, max_y = _bounds(_volume(site, "cube"))
    assert (min_x, max_x) == (ORIGIN_X_CM - 200, ORIGIN_X_CM + 200)
    assert (min_y, max_y) == (TAG_LINE_Y_CM - 350, TAG_LINE_Y_CM)
    # The landing footprint is the measured floor: a land-in-place anywhere
    # the aircraft may reach must stay on it.
    for contingency in site.terminal_contingencies:
        assert _bounds(_volume(site, "cube"))[:4] == (
            min(p[0] for p in contingency.landing_polygon_xy_cm),
            max(p[0] for p in contingency.landing_polygon_xy_cm),
            min(p[1] for p in contingency.landing_polygon_xy_cm),
            max(p[1] for p in contingency.landing_polygon_xy_cm))


def test_the_flight_volume_keeps_canningst_s_validated_insets():
    site = parse_room_site(_doc(SITE))
    min_x, max_x, min_y, max_y = _bounds(_volume(site, "flight"))
    cube_min_x, cube_max_x, cube_min_y, _ = _bounds(_volume(site, "cube"))
    assert min_x - cube_min_x == 30 and cube_max_x - max_x == 30
    assert min_y - cube_min_y == 30
    assert TAG_LINE_Y_CM - max_y == 40  # 40 cm clear of the tag stands


def test_home_keeps_the_210_cm_tag_standoff_the_poses_were_validated_at():
    site = parse_room_site(_doc(SITE))
    assert (site.home.nominal_pose.x_cm, site.home.nominal_pose.y_cm) == (ORIGIN_X_CM, 190)
    assert TAG_LINE_Y_CM - site.home.nominal_pose.y_cm == 210
    assert site.home.heading_deg == 90  # facing the tags


def test_every_demo5_pose_resolves_on_the_pad_row_with_more_margin_than_canningst():
    site = parse_room_site(_doc(SITE))
    _, _, flight_min_y, flight_max_y = _bounds(_volume(site, "flight"))
    flight_min_x, flight_max_x, _, _ = _bounds(_volume(site, "flight"))
    for pose_id in DEMO5_POSES:
        pose = resolve_pose_id(site, pose_id)
        assert pose is not None, pose_id
        assert pose.y_cm == site.home.nominal_pose.y_cm == 190, pose_id
        # l3-20260926-142659 refused every zone with 30 cm to the flight edge;
        # the rebuild bought 50 at CanningSt and this room gives at least 100.
        margin = min(pose.x_cm - flight_min_x, flight_max_x - pose.x_cm,
                     pose.y_cm - flight_min_y, flight_max_y - pose.y_cm)
        assert margin >= 100, (pose_id, margin)


def test_every_declared_cell_is_inside_the_room_and_seen_by_a_pose():
    doc = _doc(SITE)
    parse_room_site(doc)  # containment and visibility are validated at load
    cell_ids = {cell["cell_id"] for cell in doc["coverage_cells"]}
    seen = {cell for _, visible in doc["pose_visibility"] for cell in visible}
    assert seen == cell_ids
    assert [pose_id for pose_id, _ in doc["pose_visibility"]] == DEMO5_POSES
    assert [pose_id for pose_id, _ in doc["pose_tag_visibility"]] == DEMO5_POSES[:2]


def test_the_prepare_script_offers_the_venue_with_its_own_tag_map():
    scenario = room_live_prepare.SCENARIOS["lecture"]
    assert scenario.site == SITE and scenario.drift == DRIFT and scenario.tag_map == TAG_MAP
    # The CanningSt scenarios keep the CanningSt map; a venue never borrows
    # another venue's tags.
    assert room_live_prepare.SCENARIOS["demo5"].tag_map == room_live_prepare.TAG_MAP_PATH
    assert {"canningst", "wide", "demo", "demo360", "demo5"} <= set(room_live_prepare.SCENARIOS)


def test_each_scenario_names_the_venue_its_consent_is_recorded_for():
    assert room_live_prepare.SCENARIOS["lecture"].location == "LectureRoom"
    for name in ("canningst", "wide", "demo", "demo360", "demo5"):
        assert room_live_prepare.SCENARIOS[name].location == "CanningSt", name


def test_one_venue_s_filming_consent_never_authorises_a_flight_in_another(tmp_path):
    # The consent record is per (location, day). Moving the demo to another
    # room is another venue with other people in frame, so CanningSt's
    # record must not carry the lecture room's flight.
    import pytest

    consent_db = tmp_path / "consent.sqlite3"
    recorded = room_live_prepare.ensure_consent(consent_db, "2026-09-26", confirmed=True, location="CanningSt")
    assert "CanningSt" in recorded
    # CanningSt itself is then satisfied without re-confirming...
    reused = room_live_prepare.ensure_consent(consent_db, "2026-09-26", confirmed=False, location="CanningSt")
    assert "reusing" in reused and "CanningSt" in reused
    # ...and the lecture room is refused until the operator confirms it too.
    with pytest.raises(room_live_prepare.PrepareError, match="LectureRoom"):
        room_live_prepare.ensure_consent(consent_db, "2026-09-26", confirmed=False, location="LectureRoom")
