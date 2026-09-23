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


def test_canningst_takeoff_is_admitted_under_one_trial_grant_with_the_history_profiles(tmp_path):
    """Items 4-6 composed: the committed history profiles, one TRIAL grant
    bound to exactly them (FlightBinding.sha256s) inside its acceptance run,
    and the real stack's pad anchoring -- the supervisor validates its own
    bindings (no stub) and admits takeoff."""
    from dataclasses import replace
    from sentinel.room import calibration, live_session
    from sentinel.room.live_session import acceptance_manifest_for
    from sentinel.room.models import CapabilityGrant, canonical_grant_sha256
    profiles = ROOT / "fixtures" / "room_agent" / "v1" / "profiles"
    binding = live_session.load_flight_binding(
        site_path=SITE_PATH, drift_profile_path=profiles / "canningst-drift.json",
        energy_profile_path=profiles / "canningst-energy.json",
        command_limits_path=profiles / "canningst-command-limits.json",
        profile_paths=(profiles / "canningst-flight-profile.json",))
    assert binding.energy_profile.execution_scope == "CALIBRATION"
    assert calibration.admissible_for(binding.calibration_profiles[0], grant_state="TRIAL")

    class Manifest:
        mode, run_id, requested_capability = "acceptance", "run-0001", "count_return"

    acceptance = acceptance_manifest_for(Manifest())
    grant = CapabilityGrant("count_return", "TRIAL", binding.site.cube_site_sha256,
                            binding.energy_profile.aircraft_id, binding.sha256s, (),
                            "2099-01-01T00:00:00+00:00", "", acceptance.manifest_sha256)
    grant = replace(grant, grant_sha256=canonical_grant_sha256(grant))
    clock = time.monotonic
    tracker = LocalizationTracker(binding.drift_profile, clock=clock, real_mode=True)
    transport = FakeTransport(battery=90)
    link = live_stack.TelloLink(build_client=transport, clock=clock)
    stack = live_stack.TelloFlightStackFactory(
        link=link, tag_map=None, run_id="run-0001", run_dir=tmp_path,
        pose_burst=Bursts(reports(250, 190, 90.0)), home_settle_s=0.0)(tracker=tracker, clock=clock, site=binding.site)
    store = RoomStore.open(tmp_path / "room.sqlite3")
    try:
        store.create_session("run-0001", "request", CapabilityProfile((grant,)))
        supervisor = SafetySupervisor(
            store=store, session_id="run-0001", site=binding.site, tracker=tracker,
            reserve=ReserveGuard(binding.energy_profile, clock), limits=binding.limits,
            aircraft_id=binding.energy_profile.aircraft_id, mode=ExecutionMode.REAL, refresh=stack.refresh,
            emergency=stack.emergency, clock=clock, wall_clock=lambda: datetime(2026, 9, 23, tzinfo=timezone.utc),
            capability="count_return", acceptance_manifest=acceptance,
            profile_sha256s=binding.calibration_sha256s)
        for assurance in live_fakes.operator_assurances("run-0001", binding.site,
                                                        clock_domain_id=supervisor.clock_domain_id):
            store.append_contingency_assurance(assurance, expected_version=store.session_version("run-0001"))
        supervisor.validate_bindings(binding.site)
        assert supervisor.admitted_grant == grant
        stack.refresh()
        supervisor.start(stack.executor)
        assert stack.executor.state == ExecutorState.AIRBORNE
    finally:
        store.close()
        link.close()


def _airborne_supervisor(tmp_path, drift_override=None):
    """The CanningSt supervisor with the committed history profiles (drift
    optionally overridden), airborne over the pad after the real stack's
    takeoff; bindings are item 5's concern and are stubbed here."""
    from dataclasses import replace
    from sentinel.room import live_session
    profiles = ROOT / "fixtures" / "room_agent" / "v1" / "profiles"
    binding = live_session.load_flight_binding(
        site_path=SITE_PATH, drift_profile_path=profiles / "canningst-drift.json",
        energy_profile_path=profiles / "canningst-energy.json",
        command_limits_path=profiles / "canningst-command-limits.json",
        profile_paths=(profiles / "canningst-flight-profile.json",))
    drift = binding.drift_profile if drift_override is None else replace(binding.drift_profile,
                                                                          xy_drift_per_cm=drift_override)
    clock = time.monotonic
    tracker = LocalizationTracker(drift, clock=clock, real_mode=True)
    link = live_stack.TelloLink(build_client=FakeTransport(battery=90), clock=clock)
    stack = live_stack.TelloFlightStackFactory(
        link=link, tag_map=None, run_id="run-0001", run_dir=tmp_path,
        pose_burst=Bursts(reports(250, 190, 90.0)), home_settle_s=0.0)(tracker=tracker, clock=clock, site=binding.site)
    store = RoomStore.open(tmp_path / "room.sqlite3")
    store.create_session("run-0001", "request", CapabilityProfile(()))
    supervisor = SafetySupervisor(
        store=store, session_id="run-0001", site=binding.site, tracker=tracker,
        reserve=ReserveGuard(binding.energy_profile, clock), limits=binding.limits,
        aircraft_id=binding.energy_profile.aircraft_id, mode=ExecutionMode.REAL, refresh=stack.refresh,
        emergency=stack.emergency, clock=clock, capability="count_return")
    supervisor.validate_bindings = lambda active_site: None
    for assurance in live_fakes.operator_assurances("run-0001", binding.site,
                                                    clock_domain_id=supervisor.clock_domain_id):
        store.append_contingency_assurance(assurance, expected_version=store.session_version("run-0001"))
    stack.refresh()
    supervisor.start(stack.executor)
    return supervisor, store, link, binding


def _admission(supervisor, store, binding, pose_id):
    from types import SimpleNamespace
    from sentinel.room import wiring
    resolve = wiring.make_resolve_checkpoint(site=binding.site, profile=binding.calibration_profiles[0],
                                             pose_source=lambda: supervisor.tracker.health, limits=binding.limits)
    plan = resolve(SimpleNamespace(candidate_id=f"OBSERVE:g1:{pose_id}"), None)
    return supervisor.prepare(snapshot_version=store.session_version("run-0001"), checkpoint=plan, provider_wait_s=10)


def test_with_the_measured_history_drift_every_canningst_region_is_refused_after_takeoff(tmp_path):
    """The runbook's "What L3 will do with the history profiles": the worst
    measured drift (0.45 cm/cm) leaves no admissible observation between the
    start pad and the cube's rear face, so L3 returns without a count."""
    supervisor, store, link, binding = _airborne_supervisor(tmp_path)
    try:
        for pose_id, _ in binding.site.pose_visibility:
            decision = _admission(supervisor, store, binding, pose_id)
            assert decision.transition == "RETURN_NOW", (pose_id, decision.reason)
    finally:
        store.close()
        link.close()


def test_a_declared_smaller_drift_admits_the_center_region(tmp_path):
    supervisor, store, link, binding = _airborne_supervisor(tmp_path, drift_override=0.1)
    try:
        decision = _admission(supervisor, store, binding, "center-rear-middle-toward_targets")
        assert decision.transition == "CONTINUE", decision.reason
    finally:
        store.close()
        link.close()
