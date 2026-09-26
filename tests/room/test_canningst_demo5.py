"""The CanningSt demo5 scenario: five high observation poses instead of
demo360's nine, so a Live run flies fewer regions.

The operator's 2026-09-26 request: two tag-facing looks from the top
corners, a left and a right wall look from the top center, and one backward
look from the top center. The site is demo360's with only the poses (and
their visibility) changed.

Rebuilt the same day after live l3-20260926-142659: the rear-row corners
(y 170) sat 30 cm from the flight volume's y 140 edge, so every fixless
route-home forecast from them poked outside it and the zones were refused;
the voluntary re-anchor's rotation clearance failed the same margin. All
five poses now sit on the middle row (y 190, the pad's own row), 50 cm from
that edge."""
from __future__ import annotations

import json
from pathlib import Path

from scripts import room_live_prepare
from sentinel.room.semantic import resolve_pose_id
from sentinel.room.site import parse_room_site

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "room_agent" / "v1"
DEMO5_SITE = FIXTURES / "canningst-demo5-site.json"
DEMO360_SITE = FIXTURES / "canningst-demo360-site.json"
DEMO_DRIFT = FIXTURES / "profiles" / "canningst-demo-drift.json"

DEMO5_POSES = [
    "right-middle-top-toward_targets",
    "left-middle-top-toward_targets",
    "center-middle-top-left",
    "center-middle-top-right",
    "center-middle-top-backward",
]


def _doc(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_prepare_script_offers_demo5_with_the_demo_drift_profile():
    site, drift = room_live_prepare.SCENARIOS["demo5"]
    assert site == DEMO5_SITE
    assert drift == DEMO_DRIFT
    # demo5 is added beside the existing scenarios, never in place of them.
    assert {"canningst", "wide", "demo", "demo360"} <= set(room_live_prepare.SCENARIOS)


def test_the_demo5_site_declares_exactly_the_five_operator_poses():
    doc = _doc(DEMO5_SITE)
    assert [pose_id for pose_id, _ in doc["pose_visibility"]] == DEMO5_POSES


def test_the_demo5_site_parses_and_every_visible_cell_is_declared():
    doc = _doc(DEMO5_SITE)
    site = parse_room_site(doc)
    cell_ids = {cell["cell_id"] for cell in doc["coverage_cells"]}
    for pose_id, visible in doc["pose_visibility"]:
        assert set(visible) <= cell_ids, pose_id
    # Every declared cell is seen by at least one pose, or the count answer
    # could never cover it.
    seen = {cell for _, visible in doc["pose_visibility"] for cell in visible}
    assert seen == cell_ids
    # Tag visibility is declared only for the two tag-facing looks.
    assert [pose_id for pose_id, _ in doc["pose_tag_visibility"]] == DEMO5_POSES[:2]
    assert site.site_id == "canningst"


def test_every_demo5_pose_resolves_at_the_top_of_the_envelope():
    site = parse_room_site(_doc(DEMO5_SITE))
    resolved = {pose_id: resolve_pose_id(site, pose_id) for pose_id in DEMO5_POSES}
    assert all(resolved.values()), resolved
    middle = resolve_pose_id(site, "center-middle-middle-backward")
    assert all(pose.z_cm > middle.z_cm for pose in resolved.values()), resolved


def test_every_demo5_pose_sits_on_the_pad_row_clear_of_the_flight_edge():
    # Live l3-20260926-142659: the y 170 rear row left 30 cm to the flight
    # volume's y 140 edge and every travelling zone was refused on its
    # fixless route-home clearance. The middle row doubles that margin.
    site = parse_room_site(_doc(DEMO5_SITE))
    for pose_id in DEMO5_POSES:
        pose = resolve_pose_id(site, pose_id)
        assert pose.y_cm == site.home.nominal_pose.y_cm == 190, (pose_id, pose.y_cm)


def test_demo5_keeps_demo360_geometry_and_changes_only_the_poses():
    demo5, demo360 = _doc(DEMO5_SITE), _doc(DEMO360_SITE)
    changed = {key for key in demo5.keys() | demo360.keys() if demo5.get(key) != demo360.get(key)}
    assert changed == {"pose_visibility", "pose_tag_visibility"}
