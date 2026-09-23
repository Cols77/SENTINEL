"""Task B item 4: the flyable CanningSt room site.

``fixtures/room_agent/v1/canningst-site.json`` is the room-site document for
Mission Control's ``CanningSt`` profile: the WP5 cube of
``docs/wp5-flight1-site.json``, the tag map ``docs/wp5-flight3-map.json``,
start 250,190 cm and the demo surface at 50 cm. These tests pin it to those
sources and prove the supervisor admits takeoff from it with the real
stack's pre-takeoff tracker anchoring (the tracker anchored on the pad at pad
height, Task A's carry-over).
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from sentinel.cube.checkpoint import CommandLimits
from sentinel.cube.energy import ExecutionMode, ReserveGuard
from sentinel.cube.localization import LocalizationState, LocalizationTracker
from sentinel.cube.step_executor import ExecutorState
from sentinel.room import live_stack
from sentinel.room.models import CapabilityProfile
from sentinel.room.semantic import resolve_pose_id
from sentinel.room.site import parse_room_site, room_site_document
from sentinel.room.store import RoomStore
from sentinel.room.supervisor import SafetySupervisor
from tests.room import test_live_session as live_fakes
from tests.room.test_live_stack import Bursts, FakeTransport, reports

ROOT = Path(__file__).resolve().parents[2]
SITE_PATH = ROOT / "fixtures" / "room_agent" / "v1" / "canningst-site.json"
CUBE_PATH = ROOT / "docs" / "wp5-flight1-site.json"
TAG_MAP_PATH = ROOT / "docs" / "wp5-flight3-map.json"


def canningst_site():
    return parse_room_site(json.loads(SITE_PATH.read_text(encoding="utf-8")))


def lf_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def test_canningst_site_binds_the_mission_control_cube_and_tag_map():
    site = canningst_site()
    assert site.cube_site_sha256 == lf_sha256(CUBE_PATH)
    assert site.tag_map_sha256 == lf_sha256(TAG_MAP_PATH)
    assert parse_room_site(room_site_document(site)) == site


def test_canningst_geometry_is_the_wp5_cube_with_front_toward_the_tags():
    site = canningst_site()
    cube = json.loads(CUBE_PATH.read_text(encoding="utf-8"))["cube"]
    envelope = live_stack.site_envelope(site)
    assert (envelope.min_x_cm, envelope.max_x_cm) == (round(cube["min_x_m"] * 100), round(cube["max_x_m"] * 100))
    assert (envelope.min_y_cm, envelope.max_y_cm) == (round(cube["min_y_m"] * 100), round(cube["max_y_m"] * 100))
    assert (envelope.min_z_cm, envelope.max_z_cm) == (60, 180)  # WP5 commanded altitude band
    assert site.semantic_frame.rear_to_front_axis_xy == (0.0, 1.0)  # front = max Y, the tag stands' side
    # The semantic left/right regions land on WP5's commanded inset corners (x 190 / 310).
    assert resolve_pose_id(site, "left-rear-middle-toward_targets").x_cm == 190
    assert resolve_pose_id(site, "right-rear-middle-toward_targets").x_cm == 310


def test_canningst_home_is_the_start_pad_and_its_route_ends_over_it():
    site = canningst_site()
    home = site.home.nominal_pose
    assert (home.x_cm, home.y_cm, home.z_cm) == (250, 190, 0)
    route = next(r for r in site.routes if r.route_id == site.home.route_id)
    assert (route.checkpoints[-1].x_cm, route.checkpoints[-1].y_cm) == (250, 190)


def test_canningst_coverage_cells_lie_inside_the_commanded_flyable_area_and_planes_are_declared():
    site = canningst_site()
    for cell in site.coverage_cells:
        assert all(190 <= x <= 310 and 190 <= y <= 270 for x, y in cell.polygon_xy_cm)
    planes = {plane.plane_id: plane.z_cm for plane in site.support_planes}
    assert planes == {"FLOOR": 0, "demo-surface": 50}


def test_supervisor_admits_takeoff_for_canningst_with_the_real_stack_pad_anchoring(tmp_path):
    site = canningst_site()
    clock = time.monotonic
    tracker = LocalizationTracker(live_fakes.drift_profile(), clock=clock, real_mode=True)
    transport = FakeTransport()
    link = live_stack.TelloLink(build_client=transport, clock=clock)
    # The drone sits a few centimetres off the surveyed pad, facing the tags.
    factory = live_stack.TelloFlightStackFactory(
        link=link, tag_map=None, run_id="run-0001", run_dir=tmp_path,
        pose_burst=Bursts(reports(253, 186, 90.0)), home_settle_s=0.0)
    stack = factory(tracker=tracker, clock=clock, site=site)
    store = RoomStore.open(tmp_path / "room.sqlite3")
    try:
        store.create_session("run-0001", "request", CapabilityProfile(()))
        supervisor = SafetySupervisor(
            store=store, session_id="run-0001", site=site, tracker=tracker,
            reserve=ReserveGuard(live_fakes.energy_profile(site), clock),
            limits=CommandLimits(trim_rounds=0, localise=False), aircraft_id=live_fakes.AIRCRAFT_ID,
            mode=ExecutionMode.REAL, refresh=stack.refresh, emergency=stack.emergency, clock=clock,
            wall_clock=lambda: datetime(2026, 9, 23, tzinfo=timezone.utc), capability="count_return")
        supervisor.validate_bindings = lambda active_site: None  # grants are item 5's concern
        for assurance in live_fakes.operator_assurances("run-0001", site,
                                                        clock_domain_id=supervisor.clock_domain_id):
            store.append_contingency_assurance(assurance, expected_version=store.session_version("run-0001"))

        stack.refresh()  # the real stack anchors the tracker on the pad, at pad height
        pad = tracker.health
        assert pad.state == LocalizationState.ANCHORED and pad.position.z_cm == 0

        home = supervisor.start(stack.executor)
        assert stack.executor.state == ExecutorState.AIRBORNE
        assert (home.pose.x_cm, home.pose.y_cm) == (253, 186) and home.pose.z_cm == 100
        assert "takeoff" in transport.tello.sent and "land" not in transport.tello.sent
    finally:
        store.close()
        link.close()


def test_the_canningst_tag_map_binds_to_the_sites_hash_in_either_line_ending(tmp_path):
    site = canningst_site()
    raw = TAG_MAP_PATH.read_bytes().replace(b"\r\n", b"\n")
    for name, content in (("lf.json", raw), ("crlf.json", raw.replace(b"\n", b"\r\n"))):
        path = tmp_path / name
        path.write_bytes(content)
        tag_map = live_stack.load_bound_tag_map(path, expected_sha256=site.tag_map_sha256)
        assert sorted(tag["tag_id"] for tag in tag_map.doc["tags"]) == [0, 2]
    edited = tmp_path / "edited.json"
    edited.write_bytes(raw.replace(b"21.56", b"21.57"))
    import pytest
    with pytest.raises(live_stack.LiveStackError):
        live_stack.load_bound_tag_map(edited, expected_sha256=site.tag_map_sha256)
    # The two wall tags face the aircraft at the canonical heading (90 deg, +y).
    assert live_stack.mapped_heading_tag_ids(tag_map) == ((90.0, (0, 2)),)
