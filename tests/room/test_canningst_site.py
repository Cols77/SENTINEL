"""The measured CanningSt room site and its safety admissions.

``fixtures/room_agent/v1/canningst-site.json`` is the room-site document for
Mission Control's ``CanningSt`` profile: the 2026-09-24 operator-measured
clearance, the tag map ``docs/wp5-flight3-map.json``,
start 250,190 cm and the demo surface at 50 cm. These tests pin it to those
sources and prove the supervisor admits takeoff from it with the real
stack's pre-takeoff tracker anchoring (the tracker anchored on the pad at pad
height, Task A's carry-over).
"""
from __future__ import annotations

from datetime import datetime, timezone
import sys
import hashlib
import json
from pathlib import Path
import time

import pytest

from sentinel.cube.checkpoint import CommandLimits
from sentinel.cube.energy import ExecutionMode, ReserveGuard
from sentinel.cube.localization import LocalizationState, LocalizationTracker
from sentinel.cube.step_executor import ExecutorState
from sentinel.envelope import EnvelopeViolation
from sentinel.models import Position
from sentinel.room import live_stack
from sentinel.room.models import CapabilityProfile
from sentinel.room.semantic import resolve_pose_id
from sentinel.room.site import parse_room_site, room_site_document
from sentinel.room.store import RoomStore
from sentinel.room.supervisor import SafetySupervisor
from tests.room import test_live_session as live_fakes
from tests.room.test_live_stack import Bursts, FakeTello, FakeTransport, reports

ROOT = Path(__file__).resolve().parents[2]
SITE_PATH = ROOT / "fixtures" / "room_agent" / "v1" / "canningst-site.json"
CUBE_PATH = ROOT / "docs" / "wp5-flight1-site.json"
TAG_MAP_PATH = ROOT / "docs" / "wp5-flight3-map.json"


class MeasuredHeightTello(FakeTello):
    """The CanningSt unit reports 40 cm at takeoff, 30–40 below true height."""

    def takeoff(self):
        result = super().takeoff()
        self.height_dm = 4
        return result


class MeasuredHeightTransport(FakeTransport):
    def __init__(self, **tello_kwargs):
        super().__init__(**tello_kwargs)
        self.tello = MeasuredHeightTello(**tello_kwargs)


def canningst_site():
    return parse_room_site(json.loads(SITE_PATH.read_text(encoding="utf-8")))


def test_canningst_commanded_flight_starts_at_floor():
    site = canningst_site()
    volumes = {volume.volume_id: volume for volume in site.free_space_volumes}
    assert volumes["flight"].z_interval_cm == (0, 250)
    assert volumes["observation"].z_interval_cm == (0, 140)

    envelope = live_stack.site_envelope(site)
    assert envelope.contains(Position(250, 190, 0))
    with pytest.raises(EnvelopeViolation):
        envelope.validate_position(Position(250, 190, -1))


def lf_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def test_canningst_site_binds_the_mission_control_cube_and_tag_map():
    site = canningst_site()
    assert site.cube_site_sha256 == lf_sha256(CUBE_PATH)
    assert site.tag_map_sha256 == lf_sha256(TAG_MAP_PATH)
    assert parse_room_site(room_site_document(site)) == site


def test_canningst_geometry_uses_the_measured_clearance_and_faces_the_tags():
    site = canningst_site()
    volumes = {volume.volume_id: volume for volume in site.free_space_volumes}
    # 2026-09-24 operator measurement replaces the smaller WP5 cube. The
    # flight/observation floor is 0 (56d6784): the Tello height reading is
    # faulty, so it must not refuse a low hover. The observation ceiling 140
    # keeps the middle viewpoints at reported z 70 (the coverage matrix).
    assert volumes["cube"].polygon_xy_cm == ((90, 109), (410, 109), (410, 400), (90, 400))
    assert volumes["cube"].z_interval_cm == (-20, 250)
    assert volumes["flight"].polygon_xy_cm == ((120, 140), (380, 140), (380, 360), (120, 360))
    assert volumes["flight"].z_interval_cm == (0, 250)
    # The runner's envelope is the declared flight volume (where the aircraft
    # may be); the observation box only bounds where poses sit.
    envelope = live_stack.site_envelope(site)
    assert (envelope.min_x_cm, envelope.max_x_cm, envelope.min_y_cm, envelope.max_y_cm) == (120, 380, 140, 360)
    assert (envelope.min_z_cm, envelope.max_z_cm) == (0, 250)
    assert site.semantic_frame.rear_to_front_axis_xy == (0.0, 1.0)  # front = max Y, the tag stands' side
    poses = {pose_id: resolve_pose_id(site, pose_id) for pose_id in (LEFT, CENTER, RIGHT)}
    assert [(p.x_cm, p.y_cm, p.z_cm) for p in poses.values()] == [(210, 170, 70), (250, 170, 70), (290, 170, 70)]
    assert len(site.pose_visibility) == 8
    assert all(resolve_pose_id(site, pose_id) is not None for pose_id, _ in site.pose_visibility)
    assert {pose_id.split("-")[1] for pose_id, _ in site.pose_visibility} == {"rear", "middle", "front"}
    corridor = site.corridors[0]
    assert corridor.polygon_xy_cm == volumes["cube"].polygon_xy_cm
    assert corridor.z_interval_cm == volumes["cube"].z_interval_cm
    assert all(entry.landing_polygon_xy_cm == corridor.polygon_xy_cm
               and entry.z_band_cm == (0, 250) for entry in site.terminal_contingencies)


def test_canningst_home_is_the_start_pad_and_its_route_ends_over_it():
    site = canningst_site()
    home = site.home.nominal_pose
    assert (home.x_cm, home.y_cm, home.z_cm) == (250, 190, 0)
    route = next(r for r in site.routes if r.route_id == site.home.route_id)
    assert (route.checkpoints[-1].x_cm, route.checkpoints[-1].y_cm) == (250, 190)


def test_canningst_coverage_cells_tile_the_declared_demo_surface_and_planes_are_declared():
    site = canningst_site()
    assert len(site.coverage_cells) == 6  # 120 × 80 cm surface, 40 cm grid.
    for cell in site.coverage_cells:
        assert all(190 <= x <= 310 and 190 <= y <= 270 for x, y in cell.polygon_xy_cm)
    planes = {plane.plane_id: plane.z_cm for plane in site.support_planes}
    assert planes == {"FLOOR": 0, "demo-surface": 50}


def test_supervisor_admits_takeoff_for_canningst_with_the_real_stack_pad_anchoring(tmp_path):
    site = canningst_site()
    clock = time.monotonic
    tracker = LocalizationTracker(live_fakes.drift_profile(), clock=clock, real_mode=True)
    transport = MeasuredHeightTransport()
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
        assert (home.pose.x_cm, home.pose.y_cm) == (253, 186) and home.pose.z_cm == 40
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
    transport = MeasuredHeightTransport(battery=90)
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


def _airborne_supervisor(tmp_path, drift_override=None, battery=90, *, recovery=False, drift_changes=None,
                         site_changes=None, battery_guard=True):
    """The CanningSt supervisor with the committed history profiles (drift
    optionally overridden), airborne over the pad after the real stack's
    takeoff; bindings are item 5's concern and are stubbed here.

    ``recovery`` gives it the live session's recovery adapter (the mapped
    tag headings of the bound tag map), which the expected re-anchor needs."""
    from dataclasses import replace
    from sentinel.cube.recovery import RecoveryController
    from sentinel.room import live_session
    profiles = ROOT / "fixtures" / "room_agent" / "v1" / "profiles"
    binding = live_session.load_flight_binding(
        site_path=SITE_PATH, drift_profile_path=profiles / "canningst-drift.json",
        energy_profile_path=profiles / "canningst-energy.json",
        command_limits_path=profiles / "canningst-command-limits.json",
        profile_paths=(profiles / "canningst-flight-profile.json",))
    if site_changes:
        binding = replace(binding, site=replace(binding.site, **site_changes))
    drift = binding.drift_profile if drift_override is None else replace(binding.drift_profile,
                                                                          xy_drift_per_cm=drift_override)
    if drift_changes:
        drift = replace(drift, **drift_changes)
    clock = time.monotonic
    tracker = LocalizationTracker(drift, clock=clock, real_mode=True)
    link = live_stack.TelloLink(build_client=MeasuredHeightTransport(battery=battery), clock=clock)
    stack = live_stack.TelloFlightStackFactory(
        link=link, tag_map=None, run_id="run-0001", run_dir=tmp_path,
        pose_burst=Bursts(reports(250, 190, 90.0)), home_settle_s=0.0)(tracker=tracker, clock=clock, site=binding.site)
    store = RoomStore.open(tmp_path / "room.sqlite3")
    store.create_session("run-0001", "request", CapabilityProfile(()))
    adapter, headings = None, ()
    if recovery:
        tag_map = live_stack.load_bound_tag_map(TAG_MAP_PATH, expected_sha256=binding.site.tag_map_sha256)
        headings = live_stack.mapped_heading_tag_ids(tag_map)
        adapter = RecoveryController(tracker, clock=clock, burst_source=lambda action: (),
                                     energy_admits=lambda action: True)
    supervisor = SafetySupervisor(
        store=store, session_id="run-0001", site=binding.site, tracker=tracker,
        reserve=ReserveGuard(binding.energy_profile, clock, disable_predictive_guard=not battery_guard), limits=binding.limits,
        aircraft_id=binding.energy_profile.aircraft_id, mode=ExecutionMode.REAL, refresh=stack.refresh,
        emergency=stack.emergency, clock=clock, capability="count_return", recovery=adapter,
        mapped_heading_tag_ids=headings)
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
    return supervisor.prepare(snapshot_version=store.session_version("run-0001"), checkpoint=plan, provider_wait_s=10,
                              zone_admission=True)


def _decisions(tmp_path, **kwargs):
    # A refusal latches RETURN_NOW on that supervisor. Assess each newly
    # declared viewpoint from a fresh airborne home, as a separate flight
    # opportunity, so an early refusal cannot poison the following results.
    decisions = {}
    for index, (pose_id, _) in enumerate(canningst_site().pose_visibility):
        pose_dir = tmp_path / f"pose-{index}"
        pose_dir.mkdir(parents=True)
        supervisor, store, link, binding = _airborne_supervisor(pose_dir, **kwargs)
        try:
            decisions[pose_id] = _admission(supervisor, store, binding, pose_id)
        finally:
            store.close()
            link.close()
    return decisions


CENTER, LEFT, RIGHT = ("center-rear-middle-toward_targets", "left-rear-middle-toward_targets",
                       "right-rear-middle-toward_targets")


def test_a_zone_refused_on_the_dispatch_recheck_executes_nothing(tmp_path):
    """Live 2026-09-24 17:21: a zone admitted at the edge of the drift budget
    was refused by dispatch's own re-check a moment later. That REFUSE fell
    through, executed the plan's first command and re-dispatched, ~75 times
    over four minutes. Now it returns ZONE_REFUSED with nothing executed,
    and only an explicit zone admission may refuse at all."""
    from dataclasses import replace

    supervisor, store, link, binding = _airborne_supervisor(tmp_path)
    try:
        refused = _admission(supervisor, store, binding, CENTER)
        assert refused.transition == "REFUSE"
        plan = refused.request.checkpoint
        # Without the zone-admission flag the same checkpoint is not refused.
        plain = supervisor.prepare(snapshot_version=store.session_version("run-0001"), checkpoint=plan,
                                   provider_wait_s=10)
        assert plain.transition != "REFUSE"
        executed = []

        class Recorder:
            state = None
            home = None

            def execute(self, part):
                executed.append(part)
                raise AssertionError("a refused zone must not execute any command")

            return_home = execute
        recorder = Recorder()
        recorder.home = getattr(supervisor, "_measured_home", None)
        result = supervisor.dispatch(replace(refused, transition="CONTINUE"), recorder)
        assert result.status == "ZONE_REFUSED" and not executed
    finally:
        store.close()
        link.close()


def test_an_unsegmented_three_look_checkpoint_is_refused_at_90_percent(tmp_path):
    """A whole sweep has a longer uncertainty horizon than one SDK leg.

    The controller segments it into three safely rechecked looks; this direct
    supervisor test must not claim the whole sweep is admitted at takeoff.
    """
    decisions = _decisions(tmp_path)
    assert all(d.transition != "CONTINUE" for d in decisions.values())
    # Freshly anchored, a re-anchor cannot shorten the trip: only this zone
    # is refused (never dispatched) and the aircraft stays where it is.
    assert decisions[CENTER].transition == "REFUSE"
    assert decisions[CENTER].reason == "LOCALIZATION_RECOVERY_REQUIRED"
    assert "xy_uncertainty" in decisions[CENTER].pose_forecast.reasons


def test_the_old_outlier_drift_is_still_refused_for_the_new_translated_poses(tmp_path):
    """Every new view translates from the pad; none merits this outlier drift."""
    decisions = _decisions(tmp_path, drift_override=0.45)
    assert all(d.transition != "CONTINUE" for d in decisions.values())
    assert all(d.expected_reanchor is None for d in decisions.values())


def test_at_30_percent_battery_the_mission_is_refused_before_takeoff(tmp_path):
    with pytest.raises(ValueError, match="RETURN_ENERGY_UNAVAILABLE"):
        _decisions(tmp_path, battery=30)


def test_takeoff_needs_about_45_percent(tmp_path):
    """The runbook's takeoff figure (a recovery and the whole return above the
    20 % floor): admitted at 50 %, refused at 40 % (the threshold is 43 %)."""
    _decisions(tmp_path / "ok", battery=50)
    with pytest.raises(ValueError, match="start blocked"):
        _decisions(tmp_path / "refused", battery=40)


@pytest.mark.parametrize("battery", [50, 57, 65])
def test_low_battery_refuses_the_full_three_look_checkpoint(tmp_path, battery):
    """The old 55/60% figures were for one frame, not three looks.

    This directly offered full checkpoint reserves all provider waits and is
    unaffordable even though 50% still admits takeoff. The segmented live
    mission exercises the actual observation path separately.
    """
    decisions = _decisions(tmp_path, battery=battery)
    assert all(d.transition == "RETURN_NOW" and d.reason == "OPTIONAL_WORK_UNAFFORDABLE"
               for d in decisions.values())


def test_widening_the_semantic_envelope_does_not_bypass_the_full_sweep_guard(tmp_path):
    """Even zero hover drift cannot make an unsegmented sweep admissible."""
    from dataclasses import replace
    from sentinel.room import live_session
    original = live_session.load_flight_binding

    def whole_cube(**kwargs):
        binding = original(**kwargs)
        site = replace(binding.site, semantic_frame=replace(binding.site.semantic_frame, envelope_volume_id="flight"))
        return replace(binding, site=site, drift_profile=replace(binding.drift_profile, xy_drift_cm_s=0.0,
                                                                 heading_drift_deg_s=0.0))

    live_session.load_flight_binding = whole_cube
    try:
        decisions = _decisions(tmp_path)
    finally:
        live_session.load_flight_binding = original
    assert all(d.transition != "CONTINUE" for d in decisions.values())


# ---------------------------------------------------------------------------
# Task V2: an expected re-anchor at a tag-visible observation checkpoint
# ---------------------------------------------------------------------------

#: The pre-fix-1 declared hover drift: at it no lateral region is admissible
#: from a fixless out-and-back (Task B2's report), so it isolates the credit.
DOUBLED_HOVER = {"xy_drift_cm_s": 0.1}


def test_the_site_declares_tags_only_for_the_six_wall_facing_views():
    site = canningst_site()
    forward = {pose_id for pose_id, _ in site.pose_visibility if pose_id.endswith("toward_targets")}
    assert len(forward) == 6  # Side looks face away from the two wall tags.
    assert dict(site.pose_tag_visibility) == {pose_id: (0, 2) for pose_id in forward}


def test_a_tag_visibility_declaration_must_name_a_declared_pose_and_valid_tags():
    from dataclasses import replace
    site = canningst_site()
    for bad in ((("front-rear-middle-toward_targets", (0, 2)),), ((CENTER, ()),), ((CENTER, (0, 0)),),
                ((CENTER, (-1, 2)),), ((CENTER, (0, 2)), (CENTER, (0, 2)))):
        with pytest.raises(ValueError):
            replace(site, pose_tag_visibility=bad).validate()


def test_no_tag_credit_is_granted_to_a_refused_full_sweep(tmp_path):
    """A tag declaration cannot compensate for unaffordable full sweeps."""
    for label, kwargs in (("no-recovery", {}), ("no-tags", {"recovery": True,
                                                            "site_changes": {"pose_tag_visibility": ()}})):
        decisions = _decisions(tmp_path / label, drift_changes=DOUBLED_HOVER, **kwargs)
        assert all(d.transition != "CONTINUE" and d.expected_reanchor is None
                   for d in decisions.values()), label


def test_declared_tags_do_not_override_a_refused_full_sweep(tmp_path):
    """The tag pair is geometrically in view, but safety still gates motion."""
    decisions = _decisions(tmp_path, drift_changes=DOUBLED_HOVER, recovery=True)
    assert all(d.transition != "CONTINUE" for d in decisions.values())
    assert all(d.expected_reanchor is None for d in decisions.values())


def test_the_expected_reanchor_does_not_relieve_an_absurd_drift(tmp_path):
    """The corridor home is always judged from the fixless forecast (a fix
    that never comes must still leave a clear way home): the 0.45 cm/cm
    outlier is still refused wherever the aircraft must move, even with the
    recovery adapter and the declared tags."""
    decisions = _decisions(tmp_path, drift_override=0.45, recovery=True)
    assert all(d.transition != "CONTINUE" and d.expected_reanchor is None
               for d in decisions.values())

