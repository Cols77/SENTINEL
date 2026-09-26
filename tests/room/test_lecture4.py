"""The lecture4 scenario: one board look, then travel to the other three walls.

2026-09-26 operator request, eve of the demo: "one zone facing the board
with the tags, then move to the 3 other walls (left, right, rear)" -- the
aircraft travels to each wall viewpoint and rotates to face that wall.

Same venue, tags and profiles as ``lecture`` (lecture-room-site.json); what
changes is the observation envelope and the declared poses. The envelope
widens to x 150-350, y 150-230 so the wall looks actually travel: the board
look stays at the validated pad spot (250, 190, the 210 cm tag standoff),
the wall looks fly ~67 cm left and right and ~27 cm back, each facing its
wall. Every endpoint keeps at least 80 cm to the flight volume's edges
(l3-20260926-142659 refused zones at 30 cm; demo5's rebuild bought 50).
"""
import hashlib
import json
from pathlib import Path

from scripts import room_live_prepare
from sentinel.room.semantic import resolve_pose_id
from sentinel.room.site import parse_room_site

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "room_agent" / "v1"
SITE = FIXTURES / "lecture4-site.json"
LECTURE_SITE = FIXTURES / "lecture-room-site.json"
TAG_MAP = ROOT / "docs" / "lecture-room-map.json"

#: Mission order: the board (tags) first, then the left, right and rear walls.
POSES = [
    "center-middle-top-toward_targets",
    "left-middle-top-left",
    "right-middle-top-right",
    "center-rear-top-backward",
]


def _doc(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _volume(site, volume_id):
    return next(v for v in site.free_space_volumes if v.volume_id == volume_id)


def _bounds(volume):
    xs = [p[0] for p in volume.polygon_xy_cm]
    ys = [p[1] for p in volume.polygon_xy_cm]
    return min(xs), max(xs), min(ys), max(ys)


def test_the_board_look_flies_from_the_validated_pad_spot():
    site = parse_room_site(_doc(SITE))
    pose = resolve_pose_id(site, "center-middle-top-toward_targets")
    assert (pose.x_cm, pose.y_cm) == (250, 190)  # the flown 210 cm standoff
    assert pose.yaw_deg == 90  # facing the tags / the board


def test_the_wall_looks_travel_to_their_walls_and_face_them():
    site = parse_room_site(_doc(SITE))
    left = resolve_pose_id(site, "left-middle-top-left")
    right = resolve_pose_id(site, "right-middle-top-right")
    rear = resolve_pose_id(site, "center-rear-top-backward")
    # Travel: each wall look leaves the pad spot toward its wall.
    assert (left.x_cm, left.y_cm, left.yaw_deg) == (183, 190, -180)
    assert (right.x_cm, right.y_cm, right.yaw_deg) == (317, 190, 0)
    assert (rear.x_cm, rear.y_cm, rear.yaw_deg) == (250, 163, -90)


def test_every_pose_keeps_80_cm_to_the_flight_edges():
    site = parse_room_site(_doc(SITE))
    min_x, max_x, min_y, max_y = _bounds(_volume(site, "flight"))
    for pose_id in POSES:
        pose = resolve_pose_id(site, pose_id)
        assert pose is not None, pose_id
        margin = min(pose.x_cm - min_x, max_x - pose.x_cm,
                     pose.y_cm - min_y, max_y - pose.y_cm)
        assert margin >= 80, (pose_id, margin)


def test_the_site_binds_this_venue_s_tags_and_calibration_domain():
    site = parse_room_site(_doc(SITE))
    lecture = parse_room_site(_doc(LECTURE_SITE))
    assert site.site_id == "lecture-room-walls"
    raw = TAG_MAP.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(raw).hexdigest() == site.tag_map_sha256
    # The same venue: same tags, same calibration domain, same home pad.
    assert site.tag_map_sha256 == lecture.tag_map_sha256
    assert site.cube_site_sha256 == lecture.cube_site_sha256
    assert site.home == lecture.home


def test_every_declared_cell_is_seen_and_the_board_look_expects_both_tags():
    doc = _doc(SITE)
    parse_room_site(doc)  # containment and visibility are validated at load
    cell_ids = {cell["cell_id"] for cell in doc["coverage_cells"]}
    seen = {cell for _, visible in doc["pose_visibility"] for cell in visible}
    assert seen == cell_ids
    assert [pose_id for pose_id, _ in doc["pose_visibility"]] == POSES
    # The tags physically mounted here are images 00002 and 00004.
    assert doc["pose_tag_visibility"] == [["center-middle-top-toward_targets", [2, 4]]]


def test_the_prepare_script_offers_lecture4_with_the_venue_s_map():
    scenario = room_live_prepare.SCENARIOS["lecture4"]
    assert scenario.site == SITE
    assert scenario.tag_map == TAG_MAP
    assert scenario.location == room_live_prepare.LECTURE_LOCATION
    assert scenario.drift == room_live_prepare.SCENARIOS["lecture"].drift
