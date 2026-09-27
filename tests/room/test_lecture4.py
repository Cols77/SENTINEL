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


# -- lecture4high: wall looks 30 cm up, board look at tag-framing height ------
HIGH_SITE = FIXTURES / "lecture4-high-site.json"
HIGH_POSES = [
    "center-middle-middle-toward_targets",
    "left-middle-top-left",
    "right-middle-top-right",
    "center-rear-top-backward",
]


def test_lecture4high_raises_the_walls_and_keeps_the_board_look_on_the_tags():
    """2026-09-27 demo day: +30 cm on the wall looks to frame every person.
    A blanket +30 would put the 210 cm tag fix 31-35 degrees below the
    horizon, past the camera's ~31 degree lower field (8.6 degree pitch plus
    the half-height of the frame), so the board look drops to middle height
    instead: 18 degrees, framed with margin."""
    site = parse_room_site(_doc(HIGH_SITE))
    board = resolve_pose_id(site, "center-middle-middle-toward_targets")
    assert (board.x_cm, board.y_cm, board.z_cm, board.yaw_deg) == (250, 190, 88, 90)
    for pose_id, x, y in (("left-middle-top-left", 183, 190),
                          ("right-middle-top-right", 317, 190),
                          ("center-rear-top-backward", 250, 163)):
        pose = resolve_pose_id(site, pose_id)
        assert (pose.x_cm, pose.y_cm, pose.z_cm) == (x, y, 147), pose_id  # 117 + 30


def test_lecture4high_shares_the_venue_binding_and_is_the_prepared_default():
    doc = _doc(HIGH_SITE)
    site = parse_room_site(doc)
    lecture4 = parse_room_site(_doc(SITE))
    assert site.site_id == "lecture-room-walls-high"
    assert site.tag_map_sha256 == lecture4.tag_map_sha256
    assert site.cube_site_sha256 == lecture4.cube_site_sha256
    assert site.home == lecture4.home
    cell_ids = {cell["cell_id"] for cell in doc["coverage_cells"]}
    assert {cell for _, visible in doc["pose_visibility"] for cell in visible} == cell_ids
    assert [pose_id for pose_id, _ in doc["pose_visibility"]] == HIGH_POSES
    assert doc["pose_tag_visibility"] == [["center-middle-middle-toward_targets", [2, 4]]]
    # Demo day: the whole-day floor-check window, as in lecture4.
    assert all(e["max_assurance_age_ms"] == 43200000 for e in doc["terminal_contingencies"])
    scenario = room_live_prepare.SCENARIOS["lecture4high"]
    assert scenario.site == HIGH_SITE and scenario.tag_map == TAG_MAP
    # Live l3-20260927-111636: lecture4high's 59 cm climb per wall leg put the
    # fixless out-and-back forecast over the 60 cm gate and every wall zone
    # was refused, so the prepared default is lecture4 at the flown height.
    import inspect
    assert inspect.signature(room_live_prepare.prepare).parameters["scenario"].default == "lecture4"
