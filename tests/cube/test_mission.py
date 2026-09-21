import json
from datetime import datetime, timezone

import pytest

from sentinel.contracts.pose import DronePoseReport, TagObservation
from sentinel.cube.mission import (
    CubeMissionRunner, Mission, PlanError, Waypoint, axis_move, load_mission,
)
from sentinel.cube.site import parse_site
from sentinel.dry_run import DryRunClient

SITE_DOC = {
    "schema_version": "cube-site-v1",
    "line_y_m": 5.0,
    "stands": [{"x_m": x, "first_tag_id": 2 * i, "heights_m": [0.90, 1.45], "size_m": 0.192}
               for i, x in enumerate([0.25, 0.75, 2.3, 2.7, 3.1, 4.65, 5.15])],
    "cube": {"min_x_m": 0.3, "max_x_m": 5.1, "min_y_m": 2.0, "max_y_m": 4.2, "max_z_m": 2.0},
    "commanded": {"inset_m": 0.3, "min_z_cm": 80, "max_z_cm": 180},
}
CALIBRATION = {"width": 960, "height": 720, "fx": 893.0, "fy": 894.0, "cx": 468.0,
               "cy": 354.0, "dist_coeffs": [], "calibration_source": "chessboard"}

SITE = parse_site(SITE_DOC)
MISSION = Mission(270, 250, (
    Waypoint("WP1", 270, 300, 100),
    Waypoint("WP2", 150, 300, 140, looks_deg=(-45, 0, 45)),
))


def _report(x_cm, y_cm, heading=90.0):
    return DronePoseReport(
        report_id="p", run_id="r", observed_at=datetime.now(timezone.utc),
        source_kind="SIMULATED", point=(x_cm / 10.0, y_cm / 10.0), heading_deg=heading,
        altitude_m=None, method="APRILTAG_36H11_PNP",
        tag_observations=(TagObservation(4, "STAND_2", 2.0, 0.5),), reprojection_error_px=0.5)


def _runner(tmp_path, pose_mode, bursts, mission=MISSION):
    queue = list(bursts)

    def burst():
        if pose_mode == "off":
            raise AssertionError("pose_mode off must never localise")
        return queue.pop(0)

    client = DryRunClient()
    runner = CubeMissionRunner(client, "dry-run", mission=mission, site=SITE, pose_mode=pose_mode,
                               mission_log=tmp_path / "mission.jsonl", burst_fn=burst,
                               sleep_fn=lambda seconds: None)
    return runner, client


def _log(tmp_path):
    return [json.loads(line) for line in (tmp_path / "mission.jsonl").read_text().splitlines()]


def test_axis_move_rule():
    assert [axis_move(d) for d in (0, 10, -10, 11, -14, 20, -135.4)] == [0, 0, 0, 20, 20, 20, 135]


def test_off_mode_navigates_open_loop_with_height_and_looks(tmp_path):
    runner, client = _runner(tmp_path, "off", [])
    assert runner.run(confirm=True).status == "PASS"
    commands = [c for c in client.commands if c.split()[0] in
                {"forward", "back", "left", "right", "up", "down", "cw", "ccw", "land"}]
    assert commands == ["forward 50", "left 120", "up 40",
                        "cw 45", "ccw 45", "ccw 45", "cw 45", "land"]
    assert [e["status"] for e in _log(tmp_path)] == ["OPEN_LOOP", "OPEN_LOOP"]


def test_passive_mode_measures_and_proposes_but_never_sends(tmp_path):
    bursts = [[_report(270, 282)] * 10, [_report(151, 300)] * 10]   # WP1 is 18 cm short in y
    runner, client = _runner(tmp_path, "passive", bursts)
    assert runner.run(confirm=True).status == "PASS"
    wp1, wp2 = _log(tmp_path)
    assert wp1["status"] == "LOCALISED" and wp1["containment"] == "INSIDE"
    assert wp1["arrival_error_cm"] == pytest.approx(18.0, abs=0.1)
    assert wp1["proposed_trims"] == [["forward", 20]] and wp1["trim_rounds_sent"] == 0
    assert wp2["proposed_trims"] == []
    assert "forward 20" not in client.commands          # SR-063 as written: nothing sent
    assert "left 120" in client.commands                # next leg still planned open-loop


def test_reanchor_mode_trims_then_plans_the_next_leg_from_the_fix(tmp_path):
    bursts = [[_report(270, 282)] * 10,                  # WP1 first fix: 18 cm short
              [_report(275, 301)] * 10,                  # after the trim: good enough
              [_report(150, 300)] * 10]                  # WP2
    runner, client = _runner(tmp_path, "reanchor", bursts)
    assert runner.run(confirm=True).status == "PASS"
    assert "forward 20" in client.commands
    assert "left 125" in client.commands and "left 120" not in client.commands
    wp1 = _log(tmp_path)[0]
    assert wp1["trim_rounds_sent"] == 1 and wp1["arrival_error_cm"] == pytest.approx(5.1, abs=0.1)


def test_reanchor_mode_trims_a_yaw_error(tmp_path):
    mission = Mission(270, 250, (Waypoint("WP1", 270, 300, 100),))
    runner, client = _runner(tmp_path, "reanchor", [[_report(270, 300, heading=104.0)] * 10],
                             mission=mission)
    assert runner.run(confirm=True).status == "PASS"
    assert "cw 14" in client.commands
    assert _log(tmp_path)[0]["heading_error_deg"] == pytest.approx(14.0)


def test_unreliable_burst_degrades_the_stop_to_open_loop(tmp_path):
    runner, _ = _runner(tmp_path, "reanchor", [[_report(270, 300)] * 3, [_report(150, 300)] * 10])
    assert runner.run(confirm=True).status == "PASS"
    assert [e["status"] for e in _log(tmp_path)] == ["UNLOCALISED", "LOCALISED"]


def test_two_implausible_fixes_in_a_row_land_the_drone(tmp_path):
    bursts = [[_report(200, 300)] * 10, [_report(80, 380)] * 10]    # both > 60 cm from dead reckoning
    runner, client = _runner(tmp_path, "reanchor", bursts)
    report = runner.run(confirm=True)
    assert report.status == "FAIL" and "implausible" in report.error.lower()
    assert "land" in client.commands
    assert [e["status"] for e in _log(tmp_path)] == ["IMPLAUSIBLE", "IMPLAUSIBLE"]


def test_fix_outside_the_cube_lands_the_drone(tmp_path):
    runner, client = _runner(tmp_path, "passive", [[_report(270, 430)] * 10])
    report = runner.run(confirm=True)
    assert report.status == "FAIL" and "containment" in report.error.lower()
    assert "land" in client.commands and "left 120" not in client.commands
    assert _log(tmp_path)[0]["containment"] == "OUTSIDE"


def test_a_stop_flagged_localise_false_is_not_measured(tmp_path):
    mission = Mission(270, 250, (Waypoint("WP1", 270, 300, 100, localise=False),))
    runner, _ = _runner(tmp_path, "passive", [], mission=mission)
    assert runner.run(confirm=True).status == "PASS"
    assert _log(tmp_path)[0]["status"] == "OPEN_LOOP"


def test_mission_outside_the_commanded_bounds_is_refused_before_takeoff(tmp_path):
    with pytest.raises(PlanError, match="WP1"):
        _runner(tmp_path, "off", [], mission=Mission(270, 250, (Waypoint("WP1", 270, 410, 100),)))
    with pytest.raises(PlanError, match="start"):
        _runner(tmp_path, "off", [], mission=Mission(20, 250, (Waypoint("WP1", 270, 300, 100),)))


def test_load_mission(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({
        "schema_version": "cube-mission-v1", "start": {"x_cm": 270, "y_cm": 250},
        "waypoints": [{"name": "WP1", "x_cm": 270, "y_cm": 300, "z_cm": 100},
                      {"name": "WP2", "x_cm": 150, "y_cm": 300, "z_cm": 140,
                       "looks_deg": [-45, 0, 45], "localise": False}]}))
    mission = load_mission(path)
    assert mission.waypoints[0] == Waypoint("WP1", 270, 300, 100, (0,), True)
    assert mission.waypoints[1] == Waypoint("WP2", 150, 300, 140, (-45, 0, 45), False)
    path.write_text(json.dumps({"schema_version": "cube-mission-v1",
                                "start": {"x_cm": 270, "y_cm": 250}, "waypoints": []}))
    with pytest.raises(ValueError):
        load_mission(path)


def test_arrival_inside_the_tolerance_circle_is_logged_inside(tmp_path):
    runner, _ = _runner(tmp_path, "reanchor", [[_report(275, 301)] * 10, [_report(150, 300)] * 10])
    assert runner.run(confirm=True).status == "PASS"
    assert [e["tolerance"] for e in _log(tmp_path)] == ["INSIDE", "INSIDE"]


def test_arrival_in_the_outer_circle_after_both_trims_continues_and_is_logged(tmp_path):
    bursts = [[_report(270, 280)] * 10] * 3 + [[_report(150, 300)] * 10]   # 20 cm short, trims do nothing
    runner, client = _runner(tmp_path, "reanchor", bursts)
    assert runner.run(confirm=True).status == "PASS"
    wp1, wp2 = _log(tmp_path)
    assert wp1["trim_rounds_sent"] == 2 and wp1["tolerance"] == "OUTER"
    assert wp2["tolerance"] == "INSIDE"


def test_arrival_beyond_the_outer_circle_after_both_trims_lands_the_drone(tmp_path):
    bursts = [[_report(270, 265)] * 10] * 3                                # 35 cm short, trims do nothing
    runner, client = _runner(tmp_path, "reanchor", bursts)
    report = runner.run(confirm=True)
    assert report.status == "FAIL" and "tolerance" in report.error.lower()
    assert "land" in client.commands and "left 120" not in client.commands
    wp1 = _log(tmp_path)[0]
    assert wp1["trim_rounds_sent"] == 2 and wp1["tolerance"] == "OUTSIDE"


def test_passive_mode_logs_the_tolerance_verdict_but_never_lands_on_it(tmp_path):
    runner, client = _runner(tmp_path, "passive", [[_report(270, 265)] * 10, [_report(150, 300)] * 10])
    assert runner.run(confirm=True).status == "PASS"
    assert [e["tolerance"] for e in _log(tmp_path)] == ["OUTSIDE", "INSIDE"]
