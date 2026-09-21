# tests/survey/test_cube_bridge.py
"""``survey.cube_bridge`` -- the only ``sentinel.cube`` importer in the
survey package (plan Task 11, V12 + V13). Runs the whole path against
``DryRunClient``, with an injected burst function standing in for a live
pose stream: no hardware, no network."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sentinel.camera import SYNTHETIC_LEVEL, TELLO_MEASURED
from sentinel.contracts.pose import DronePoseReport, TagObservation
from sentinel.cube.mission import FACING_DEG, CubeMissionRunner
from sentinel.cube.site import Site, Stand, load_site
from sentinel.dry_run import DryRunClient
from sentinel.survey.cube_bridge import (
    CubeFlight, SyntheticCameraRefused, envelope_from_site, mission_from_option,
)
from sentinel.survey.envelope import SweepOption
from sentinel.survey.options import generate_options

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_SITE_PATH = REPO_ROOT / "docs" / "wp5-flight1-site.json"

SITE = Site(line_y_m=0.0, stands=(Stand(0.0, 0, (0.9, 1.45), 0.16),),
            cube_min_x_m=-1.5, cube_max_x_m=1.5, cube_min_y_m=0.0, cube_max_y_m=1.0,
            cube_max_z_m=1.8, inset_m=0.1, min_z_cm=80, max_z_cm=180)

#: A second, room-corner-origin site for the fly()-based tests below.
#: DronePoseReport.point is a LocalPoint (0-100 units = 0-1000 cm, room
#: origin at a corner -- see site.py's tag_map_doc), so a fix's x/y_cm
#: cannot be negative the way SITE's (centred-origin, plan-doc fixture)
#: can; every real site (tests/cube/test_mission.py's own SITE_DOC
#: included) is set up this way. Envelope (inset 0.3 m): x 60-270, y
#: 230-390, z 80-180.
FLY_SITE = Site(line_y_m=5.0, stands=(Stand(1.5, 0, (0.9, 1.45), 0.16),),
                cube_min_x_m=0.3, cube_max_x_m=3.0, cube_min_y_m=2.0, cube_max_y_m=4.2,
                cube_max_z_m=2.0, inset_m=0.3, min_z_cm=80, max_z_cm=180)


def _report(x_cm, y_cm, heading=FACING_DEG):
    """A DronePoseReport with just the fields localise()/mission.py read."""
    return DronePoseReport(
        report_id="p", run_id="r", observed_at=datetime.now(timezone.utc),
        source_kind="SIMULATED", point=(x_cm / 10.0, y_cm / 10.0), heading_deg=heading,
        altitude_m=None, method="APRILTAG_36H11_PNP",
        tag_observations=(TagObservation(2, "STAND_0", 2.0, 0.5),), reprojection_error_px=0.5)


def _flight(tmp_path, *, pose_mode="off", burst_fn=None, sr063_amended=False,
           surface_z_cm=0.0, camera=TELLO_MEASURED, start_x_cm=150, start_y_cm=250,
           site=FLY_SITE):
    return CubeFlight(
        client=DryRunClient(), site=site, mode="dry-run", pose_mode=pose_mode,
        run_id="RUN1", mission_log=tmp_path / "mission.jsonl",
        start_x_cm=start_x_cm, start_y_cm=start_y_cm, surface_z_cm=surface_z_cm,
        sr063_amended=sr063_amended, burst_fn=burst_fn, sleep_fn=lambda seconds: None,
        camera=camera)


def _rail(envelope):
    return next(o for o in generate_options(envelope, TELLO_MEASURED)
                if o.option_id == "RAIL_MID_HEIGHT")


# -- envelope_from_site / mission_from_option (Task 11 body, unchanged by V12/V13) --

def test_the_envelope_is_inset_from_the_cube_bounds():
    envelope = envelope_from_site(SITE, standoff_cm=300)
    assert envelope.min_x_cm == -140
    assert envelope.max_x_cm == 140
    assert envelope.min_z_cm == 80 and envelope.max_z_cm == 180


def test_every_generated_mission_stays_within_the_waypoint_cap():
    envelope = envelope_from_site(SITE, standoff_cm=300)
    for option in generate_options(envelope, TELLO_MEASURED):
        mission = mission_from_option(option, start_x_cm=0, start_y_cm=0)
        assert len(mission.waypoints) <= 8


def test_a_rail_mission_never_asks_the_runner_to_rotate():
    envelope = envelope_from_site(SITE, standoff_cm=300)
    rail = _rail(envelope)
    mission = mission_from_option(rail, start_x_cm=0, start_y_cm=0)
    assert all(waypoint.looks_deg == (0,) for waypoint in mission.waypoints)


def test_every_waypoint_asks_the_runner_to_localise():
    """V13: a pose burst at every stop, so the freshest position is always
    attempted, even for an option whose looks never rotate."""
    envelope = envelope_from_site(SITE, standoff_cm=300)
    rail = _rail(envelope)
    mission = mission_from_option(rail, start_x_cm=0, start_y_cm=0)
    assert all(waypoint.localise for waypoint in mission.waypoints)


def test_every_generated_option_validates_against_the_real_site(tmp_path):
    """The real wp5-flight1-site.json, not the plan-doc fixture: every
    option generate_options builds for it must be a legal mission --
    CubeMissionRunner validates the whole mission at construction, so
    building one for every option IS the check."""
    site = load_site(REAL_SITE_PATH)
    envelope = envelope_from_site(site, standoff_cm=300)
    start_x_cm = (envelope.min_x_cm + envelope.max_x_cm) // 2
    for option in generate_options(envelope, TELLO_MEASURED):
        mission = mission_from_option(option, start_x_cm=start_x_cm, start_y_cm=envelope.min_y_cm)
        CubeMissionRunner(DryRunClient(), "dry-run", mission=mission, site=site,
                          mission_log=tmp_path / f"{option.option_id}.jsonl",
                          sleep_fn=lambda seconds: None)


# -- CubeFlight: MEASURED / COMMANDED stamping, tolerance, delta_z_cm --------

def test_a_localised_stop_produces_measured_frames_with_heading(tmp_path):
    option = SweepOption("ONE_STOP", "one stop", ((150, 300),), 120, (0,), 100, 22, (), 300)
    flight = _flight(tmp_path, pose_mode="passive",
                     burst_fn=lambda: [_report(155, 303, heading=95.0)] * 10)
    frames = flight.fly(option)
    assert len(frames) == 1
    frame = frames[0]
    assert frame.position_kind == "MEASURED"
    assert (frame.x_cm, frame.y_cm) == (155.0, 303.0)         # the fix, not the commanded stop
    # The fix's heading, not the nominal one -- and in the SURVEY's convention. The cube
    # says 95 (5 degrees counter-clockwise of facing the tables, which is 90); the survey
    # says -5 (0 faces the tables, clockwise positive). This assertion used to expect 95,
    # which placed every object a quarter turn from where it was.
    assert frame.heading_deg == pytest.approx(-5.0)


def test_an_unlocalised_stop_falls_back_to_commanded_dead_reckoning(tmp_path):
    option = SweepOption("ONE_STOP", "one stop", ((150, 300),), 120, (0,), 100, 22, (), 300)
    flight = _flight(tmp_path, pose_mode="passive", burst_fn=lambda: [])   # no usable burst
    frames = flight.fly(option)
    assert len(frames) == 1
    frame = frames[0]
    assert frame.position_kind == "COMMANDED"
    assert (frame.x_cm, frame.y_cm) == (150.0, 300.0)          # the commanded target
    assert frame.heading_deg == pytest.approx(0.0)            # facing the tables, survey convention


def test_the_tolerance_verdict_is_carried_onto_the_frame(tmp_path):
    option = SweepOption("ONE_STOP", "one stop", ((150, 300),), 120, (0,), 100, 22, (), 300)
    flight = _flight(tmp_path, pose_mode="passive",
                     burst_fn=lambda: [_report(150, 320)] * 10)   # 20 cm short: OUTER, not INSIDE
    frame = flight.fly(option)[0]
    assert frame.tolerance == "OUTER"


def test_delta_z_cm_is_height_minus_surface(tmp_path):
    option = SweepOption("ONE_STOP", "one stop", ((150, 300),), 120, (0,), 100, 22, (), 300)
    flight = _flight(tmp_path, pose_mode="off", surface_z_cm=17.5)
    frame = flight.fly(option)[0]
    assert frame.z_cm == 120
    assert frame.delta_z_cm == pytest.approx(120 - 17.5)


def test_two_stops_in_one_option_each_produce_their_own_frame(tmp_path):
    fixes = iter([[_report(150, 300)] * 10, [_report(200, 350)] * 10])
    flight = _flight(tmp_path, pose_mode="passive", burst_fn=lambda: next(fixes))
    option = SweepOption("TWO_STOP", "two stops", ((150, 300), (200, 350)), 120, (0,), 100, 44, (), 300)
    frames = flight.fly(option)
    assert [f.stop_index for f in frames] == [0, 1]
    assert (frames[0].x_cm, frames[1].x_cm) == (150.0, 200.0)


def test_the_mission_log_read_offset_is_tracked_across_two_flights(tmp_path):
    """Guards the offset bookkeeping inside fly(): flying a second option on
    the same mission_log file must not re-attribute the first option's log
    lines to itself."""
    fixes = iter([[_report(150, 300)] * 10, [_report(200, 350)] * 10])
    flight = _flight(tmp_path, pose_mode="passive", burst_fn=lambda: next(fixes))
    option1 = SweepOption("OPT1", "d", ((150, 300),), 120, (0,), 100, 22, (), 300)
    option2 = SweepOption("OPT2", "d", ((200, 350),), 120, (0,), 100, 22, (), 300)
    frames1 = flight.fly(option1)
    frames2 = flight.fly(option2)
    assert len(frames1) == 1 and len(frames2) == 1
    assert frames1[0].x_cm == 150.0
    assert frames2[0].x_cm == 200.0
    assert frames1[0].artifact_id != frames2[0].artifact_id


def test_a_stop_aborted_en_route_yields_no_frames(tmp_path):
    """A fix wildly outside the cube lands the drone (ContainmentAbort);
    the aborted waypoint's log entry never reaches ``_look`` and so has no
    "frames" -- fly() returns whatever frames were captured before the
    abort (here, none) instead of raising."""
    option = SweepOption("ONE_STOP", "one stop", ((150, 300),), 120, (0,), 100, 22, (), 300)
    flight = _flight(tmp_path, pose_mode="passive", burst_fn=lambda: [_report(150, 900)] * 10)
    assert flight.fly(option) == []


# -- guards: synthetic camera, reanchor without the SR-063 amendment --------

def test_the_synthetic_camera_is_refused(tmp_path):
    with pytest.raises(SyntheticCameraRefused):
        _flight(tmp_path, camera=SYNTHETIC_LEVEL)


def test_reanchor_without_the_amendment_is_refused(tmp_path):
    with pytest.raises(ValueError, match="SR-063"):
        _flight(tmp_path, pose_mode="reanchor")


def test_reanchor_with_the_amendment_is_permitted(tmp_path):
    _flight(tmp_path, pose_mode="reanchor", sr063_amended=True)   # must not raise


# -- the takeoff position: measured, or carried forward between sweeps ----------

def test_a_flight_with_no_start_measures_it_and_keeps_stop_indices_honest(tmp_path):
    """The runner logs a START line for a measured start. It is not a stop:
    counted as one, every frame's stop_index would be off by one."""
    option = SweepOption("TWO_STOPS", "two stops", ((150, 300), (200, 300)), 120, (0,), 100, 44,
                         (), 300)
    flight = _flight(tmp_path, pose_mode="passive", start_x_cm=None, start_y_cm=None,
                     burst_fn=lambda: [_report(152, 262)] * 10)
    frames = flight.fly(option)
    assert [frame.stop_index for frame in frames] == [0, 1]
    log = [json.loads(line) for line in (tmp_path / "mission.jsonl").read_text().splitlines()]
    assert log[0]["waypoint"] == "START" and log[0]["fix_cm"] == [152.0, 262.0]


def test_pose_off_cannot_measure_a_start_and_says_so(tmp_path):
    with pytest.raises(ValueError, match="tape-measured start"):
        _flight(tmp_path, pose_mode="off", start_x_cm=None, start_y_cm=None)
    with pytest.raises(ValueError, match="both start coordinates"):
        _flight(tmp_path, pose_mode="passive", start_x_cm=150, start_y_cm=None)


def test_a_follow_up_sweep_starts_where_the_last_one_ended_not_at_the_pad(tmp_path):
    """The drone lands at the last stop of the first sweep. Planning the
    second sweep from the original pad would dead-reckon every leg from a
    place the drone is not."""
    first = SweepOption("FIRST", "rail", ((150, 300), (250, 300)), 120, (0,), 100, 44, (), 300)
    second = SweepOption("SECOND", "close look", ((250, 330),), 120, (0,), 100, 22, (), 150)
    flight = _flight(tmp_path, pose_mode="off", start_x_cm=150, start_y_cm=250)
    flight.fly(first)
    assert (flight.start_x_cm, flight.start_y_cm) == (250, 300)
    frames = flight.fly(second)
    assert (frames[0].x_cm, frames[0].y_cm) == (250, 330)
