import json
import math
import time
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


# -- A3: additive log fields (frames, measured heading) ----------------------

def test_off_mode_records_frames_but_no_heading(tmp_path):
    mission = Mission(270, 250, (Waypoint("WP1", 270, 300, 100, looks_deg=(-10, 0, 10)),))
    runner, _ = _runner(tmp_path, "off", [], mission=mission)
    assert runner.run(confirm=True).status == "PASS"
    wp1 = _log(tmp_path)[0]
    assert wp1["heading_deg"] is None and wp1["heading_error_deg"] is None
    assert [f["look_deg"] for f in wp1["frames"]] == [-10, 0, 10]
    # DryRunClient, no capture_dir -> no recorder: path/sha256 are None, not omitted
    assert all(f["path"] is None and f["sha256"] is None for f in wp1["frames"])
    assert all(f["captured_at"] for f in wp1["frames"])


def test_passive_mode_records_the_measured_heading_next_to_its_error(tmp_path):
    mission = Mission(270, 250, (Waypoint("WP1", 270, 300, 100),))
    runner, _ = _runner(tmp_path, "passive", [[_report(270, 300, heading=104.0)] * 10], mission=mission)
    assert runner.run(confirm=True).status == "PASS"
    wp1 = _log(tmp_path)[0]
    assert wp1["heading_deg"] == pytest.approx(104.0)
    assert wp1["heading_error_deg"] == pytest.approx(14.0)


# -- A4: in-move pose watchdog ------------------------------------------------

def test_pose_mode_off_never_starts_the_watchdog(tmp_path):
    calls = []
    client = DryRunClient()
    runner = CubeMissionRunner(client, "dry-run", mission=MISSION, site=SITE, pose_mode="off",
                               mission_log=tmp_path / "mission.jsonl", sleep_fn=lambda seconds: None,
                               watchdog_fix_fn=lambda: calls.append(1))
    assert runner.run(confirm=True).status == "PASS"
    assert calls == []


class _SlowMoveClient(DryRunClient):
    """A DryRunClient whose move() takes a moment -- DryRunClient otherwise
    returns instantly, giving a short-interval watchdog no time to poll."""

    def move(self, direction, distance_cm):
        time.sleep(0.08)
        return super().move(direction, distance_cm)


def test_a_watchdog_trip_during_a_move_ends_in_the_landing_path(tmp_path):
    client = _SlowMoveClient()
    runner = CubeMissionRunner(
        client, "dry-run", mission=MISSION, site=SITE, pose_mode="passive",
        mission_log=tmp_path / "mission.jsonl", burst_fn=lambda: [_report(270, 300)] * 10,
        sleep_fn=lambda seconds: None, watchdog_fix_fn=lambda: (1000.0, 1000.0),
        watchdog_poll_interval_s=0.005)
    report = runner.run(confirm=True)
    assert report.status == "FAIL"
    assert "watchdog" in report.error.lower()
    assert "land" in client.commands
    wp1 = _log(tmp_path)[0]
    assert wp1["status"] == "ABORTED_EN_ROUTE"
    assert wp1["fix_cm"] == [1000.0, 1000.0]      # the watchdog's last fix


def test_a_failed_land_during_move_is_surfaced_in_the_mission_log(tmp_path):
    """_watchdog_land_error was previously write-only: set when the
    cross-thread `land` (land_during_move=True) fails, but never read
    anywhere. It must reach the mission log for the waypoint the drone
    aborted en route to."""
    client = _SlowMoveClient()

    def failing_land():
        raise RuntimeError("simulated link drop")

    client.land = failing_land
    runner = CubeMissionRunner(
        client, "dry-run", mission=MISSION, site=SITE, pose_mode="passive",
        mission_log=tmp_path / "mission.jsonl", burst_fn=lambda: [_report(270, 300)] * 10,
        sleep_fn=lambda seconds: None, watchdog_fix_fn=lambda: (1000.0, 1000.0),
        watchdog_poll_interval_s=0.005, land_during_move=True)
    report = runner.run(confirm=True)
    assert report.status == "FAIL"
    wp1 = _log(tmp_path)[0]
    assert wp1["status"] == "ABORTED_EN_ROUTE"
    assert wp1["watchdog_land_error"] is not None
    assert "simulated link drop" in wp1["watchdog_land_error"]


class _InterruptibleMoveClient(DryRunClient):
    """What bench test B1 observed: `land` sent mid-move is honoured, and the
    interrupted move then answers ``error Auto land``."""

    def __init__(self):
        super().__init__()
        self.landed_mid_move = False

    def land_now(self):
        self.landed_mid_move = True
        return "sent"

    def move(self, direction, distance_cm):
        deadline = time.monotonic() + 1.0
        while not self.landed_mid_move and time.monotonic() < deadline:
            time.sleep(0.005)
        if self.landed_mid_move:
            return "error Auto land"
        return super().move(direction, distance_cm)


def test_an_immediate_land_reports_the_containment_abort_not_a_movement_fault(tmp_path):
    client = _InterruptibleMoveClient()
    runner = CubeMissionRunner(
        client, "dry-run", mission=MISSION, site=SITE, pose_mode="passive",
        mission_log=tmp_path / "mission.jsonl", burst_fn=lambda: [_report(270, 300)] * 10,
        sleep_fn=lambda seconds: None, watchdog_fix_fn=lambda: (1000.0, 1000.0),
        watchdog_poll_interval_s=0.005, land_during_move=True)
    report = runner.run(confirm=True)
    assert client.landed_mid_move
    assert report.status == "FAIL"
    assert "watchdog" in report.error.lower()
    assert "auto land" not in report.error.lower()
    assert _log(tmp_path)[0]["status"] == "ABORTED_EN_ROUTE"


def test_an_immediate_land_trips_inside_the_cube_edge_and_a_deferred_one_at_it(tmp_path):
    """The cube's x edge is 510 cm. 495 cm is inside it, but within the ~26 cm
    a `land` sent now still needs at mission speed."""
    outcomes = {}
    for immediate in (True, False):
        client = _InterruptibleMoveClient() if immediate else _SlowMoveClient()
        log = tmp_path / f"mission-{immediate}.jsonl"
        runner = CubeMissionRunner(
            client, "dry-run", mission=MISSION, site=SITE, pose_mode="passive",
            mission_log=log, burst_fn=lambda: [_report(270, 300)] * 10,
            sleep_fn=lambda seconds: None, watchdog_fix_fn=lambda: (495.0, 300.0),
            watchdog_poll_interval_s=0.005, land_during_move=immediate)
        outcomes[immediate] = runner.run(confirm=True).status
    assert outcomes == {True: "FAIL", False: "PASS"}


def test_a_movement_fault_without_a_watchdog_trip_is_still_a_movement_fault(tmp_path):
    class _FailingMove(DryRunClient):
        def move(self, direction, distance_cm):
            return "error Motor stop"

    runner = CubeMissionRunner(
        _FailingMove(), "dry-run", mission=MISSION, site=SITE, pose_mode="passive",
        mission_log=tmp_path / "mission.jsonl", burst_fn=lambda: [_report(270, 300)] * 10,
        sleep_fn=lambda seconds: None, watchdog_fix_fn=lambda: (270.0, 300.0),
        watchdog_poll_interval_s=0.005)
    report = runner.run(confirm=True)
    assert report.status == "FAIL"
    assert "motor stop" in report.error.lower()


def test_the_live_fix_source_ignores_single_tag_fixes(tmp_path, monkeypatch):
    """B1: in motion, single-tag fixes jumped by more than a metre."""
    import sentinel.cube.mission as mission_module

    runner, client = _runner(tmp_path, "passive", [])
    client.read_frame = lambda timeout=None: object()
    two_tags = DronePoseReport(
        report_id="p", run_id="r", observed_at=datetime.now(timezone.utc),
        source_kind="SIMULATED", point=(27.0, 30.0), heading_deg=90.0, altitude_m=None,
        method="APRILTAG_36H11_PNP", reprojection_error_px=0.5,
        tag_observations=(TagObservation(4, "STAND_2", 2.0, 0.5),
                          TagObservation(5, "STAND_2", 2.0, 0.5)))
    monkeypatch.setattr(mission_module, "_to_bgr", lambda frame: frame)
    for report, expected in ((_report(270, 300), None), (two_tags, (270.0, 300.0))):
        monkeypatch.setattr(mission_module, "estimate_pose", lambda *a, _r=report, **k: _r)
        assert runner._live_watchdog_fix() == expected


# -- F1: a waypoint aborted en route still gets a log line --------------------

def test_an_abort_during_the_move_still_logs_a_line_for_that_waypoint(tmp_path):
    """Previously the mission-log entry for a waypoint was only created AFTER
    _go_to returned, so an abort raised while still travelling to it (the
    watchdog, an envelope violation, ...) left no log line at all for the
    stop the drone was heading to."""
    client = DryRunClient()
    real_move = client.move
    calls = {"n": 0}

    def failing_move(direction, distance_cm):
        calls["n"] += 1
        if calls["n"] == 1:
            return "error"
        return real_move(direction, distance_cm)

    client.move = failing_move
    runner = CubeMissionRunner(client, "dry-run", mission=MISSION, site=SITE, pose_mode="off",
                               mission_log=tmp_path / "mission.jsonl", sleep_fn=lambda seconds: None)
    report = runner.run(confirm=True)
    assert report.status == "FAIL" and "movement failed" in report.error.lower()
    wp1 = _log(tmp_path)[0]
    assert wp1["status"] == "ABORTED_EN_ROUTE"
    assert wp1["commanded_cm"] == [270, 300, 100]
    assert wp1["fix_cm"] is None        # pose_mode off: no watchdog, nothing measured


# -- F2: a re-anchored fix outside the commanded bounds on one axis ----------

def test_reanchor_recovers_an_axis_left_outside_the_bounds_before_the_other_axis_moves(tmp_path):
    """A re-anchored fix a few cm beyond a commanded bound on one axis must
    not block the NEXT waypoint's move on the other axis: validate_move
    checks the WHOLE candidate position, so a stale out-of-bounds axis fails
    a move that has nothing to do with it (reproduces in closed_loop_sim: a
    waypoint 10 cm from a commanded edge, a fix 2 cm beyond it)."""
    mission = Mission(270, 235, (
        Waypoint("WP1", 270, 235, 100), Waypoint("WP2", 220, 235, 100)))
    bursts = [[_report(270, 226)] * 10,     # WP1 fix: y is 4 cm beyond the 230 cm bound
              [_report(220, 235)] * 10]     # WP2
    runner, client = _runner(tmp_path, "reanchor", bursts, mission=mission)
    report = runner.run(confirm=True)
    assert report.status == "PASS", report.error
    assert [e["status"] for e in _log(tmp_path)] == ["LOCALISED", "LOCALISED"]


def test_reanchor_recovers_both_axes_left_outside_the_bounds_at_once(tmp_path):
    """A reanchor fix near a cube corner can leave BOTH x and y outside the
    commanded bounds at the same time. Recovering one axis at a time still
    validates the WHOLE candidate position, so the first recovery move (say,
    x) is itself rejected on the other, still-uncorrected axis (y) -- the
    exact nuisance abort F2 exists to eliminate. While a not-yet-corrected
    axis is being recovered around, its bound must admit its own stale
    out-of-bounds value so only the axis actually moving is checked."""
    mission = Mission(65, 235, (
        Waypoint("WP1", 65, 235, 100), Waypoint("WP2", 200, 300, 100)))
    bursts = [[_report(56, 226)] * 10,      # WP1 fix: x and y both beyond their bound
              [_report(200, 300)] * 10]     # WP2
    runner, client = _runner(tmp_path, "reanchor", bursts, mission=mission)
    report = runner.run(confirm=True)
    assert report.status == "PASS", report.error
    assert [e["status"] for e in _log(tmp_path)] == ["LOCALISED", "LOCALISED"]


# -- survey bridge (Phase 2): the mission log carries the measured height ----

def test_a_stop_that_is_reached_logs_the_measured_height(tmp_path):
    """cube_bridge.CubeFlight needs "the runner's height reading" for z_cm
    (plan Task 11, V12) -- the mission log otherwise only carries the
    commanded target height, not what altitude telemetry actually measured
    after the move (they agree within DEAD_BAND_CM by construction, but are
    not the same number)."""
    mission = Mission(270, 250, (Waypoint("WP1", 270, 300, 140),))
    runner, _ = _runner(tmp_path, "off", [], mission=mission)
    assert runner.run(confirm=True).status == "PASS"
    wp1 = _log(tmp_path)[0]
    assert wp1["height_cm"] == 140


def test_a_stop_aborted_en_route_has_no_measured_height(tmp_path):
    client = DryRunClient()

    def failing_move(direction, distance_cm):
        raise RuntimeError("simulated link drop")

    client.move = failing_move
    runner = CubeMissionRunner(client, "dry-run", mission=MISSION, site=SITE, pose_mode="off",
                               mission_log=tmp_path / "mission.jsonl", sleep_fn=lambda seconds: None)
    report = runner.run(confirm=True)
    assert report.status == "FAIL"
    wp1 = _log(tmp_path)[0]
    assert wp1["status"] == "ABORTED_EN_ROUTE"
    assert wp1["height_cm"] is None


# -- start-independent missions: the start is measured, not assumed ------------------------

FREE_START = Mission(None, None, (
    Waypoint("WP1", 270, 300, 100),
    Waypoint("WP2", 150, 300, 140),
))


def test_a_mission_without_a_start_measures_it_after_takeoff_and_plans_from_the_fix(tmp_path):
    bursts = [[_report(230, 280)] * 10,      # START: wherever the operator put it
              [_report(270, 300)] * 10,      # WP1
              [_report(150, 300)] * 10]      # WP2
    runner, client = _runner(tmp_path, "reanchor", bursts, mission=FREE_START)
    assert runner.run(confirm=True).status == "PASS"
    assert "right 40" in client.commands and "forward 20" in client.commands
    start, wp1, wp2 = _log(tmp_path)
    assert start["waypoint"] == "START" and start["status"] == "LOCALISED"
    assert start["fix_cm"] == [230.0, 280.0]


def test_an_unlocalised_start_lands_rather_than_guessing(tmp_path):
    runner, client = _runner(tmp_path, "reanchor", [[_report(230, 280)] * 3], mission=FREE_START)
    report = runner.run(confirm=True)
    assert report.status == "FAIL" and "start" in report.error.lower()
    assert "land" in client.commands
    assert not any(c.split()[0] in {"forward", "back", "left", "right"} for c in client.commands)
    assert _log(tmp_path)[0]["status"] == "UNLOCALISED"


def test_a_start_outside_the_cube_lands(tmp_path):
    runner, client = _runner(tmp_path, "reanchor", [[_report(230, 470)] * 10], mission=FREE_START)
    report = runner.run(confirm=True)
    assert report.status == "FAIL" and "containment" in report.error.lower()
    assert not any(c.split()[0] in {"forward", "back", "left", "right"} for c in client.commands)


def test_a_crooked_start_is_squared_up_in_reanchor_mode(tmp_path):
    bursts = [[_report(270, 300, heading=75.0)] * 10, [_report(270, 300)] * 10,
              [_report(150, 300)] * 10]
    runner, client = _runner(tmp_path, "reanchor", bursts, mission=FREE_START)
    assert runner.run(confirm=True).status == "PASS"
    assert "ccw 15" in client.commands
    assert _log(tmp_path)[0]["yaw_trim_deg"] == 15


def test_pose_off_cannot_fly_a_mission_without_a_start(tmp_path):
    with pytest.raises(PlanError, match="start"):
        _runner(tmp_path, "off", [], mission=FREE_START)


def test_each_stop_records_the_open_loop_error_before_any_trim(tmp_path):
    bursts = [[_report(230, 280)] * 10,
              [_report(270, 282)] * 10, [_report(271, 301)] * 10,    # WP1: 18 cm short, then trimmed
              [_report(150, 300)] * 10]
    runner, _ = _runner(tmp_path, "reanchor", bursts, mission=FREE_START)
    assert runner.run(confirm=True).status == "PASS"
    wp1 = _log(tmp_path)[1]
    assert wp1["open_loop_fix_cm"] == [270.0, 282.0]
    assert wp1["open_loop_error_cm"] == pytest.approx(18.0)
    assert wp1["arrival_error_cm"] == pytest.approx(1.4, abs=0.1)
    assert wp1["localise_s"] is not None


def test_load_mission_accepts_a_file_without_a_start(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"schema_version": "cube-mission-v1",
                                "waypoints": [{"name": "A", "x_cm": 270, "y_cm": 300, "z_cm": 100}]}))
    mission = load_mission(path)
    assert mission.start_x_cm is None and mission.start_y_cm is None


def test_a_firmware_without_stop_still_completes_the_mission(tmp_path):
    """2026-09-21, trial 5: the Tello answered `unknown command: stop` and a
    fully flown mission was reported FAIL. A Tello already hovers after a move."""

    class NoStopClient(DryRunClient):
        def stop(self):
            raise RuntimeError("Command 'stop' was unsuccessful for 4 tries. "
                               "Latest response:\t'unknown command: stop'")

    client = NoStopClient()
    runner = CubeMissionRunner(client, "dry-run", mission=MISSION, site=SITE, pose_mode="off",
                               mission_log=tmp_path / "mission.jsonl", sleep_fn=lambda seconds: None)
    report = runner.run(confirm=True)
    assert report.status == "PASS"
    assert client.commands[-1] == "land"


def test_any_other_stop_failure_still_fails_the_run(tmp_path):
    class BrokenStopClient(DryRunClient):
        def stop(self):
            return "error"

    client = BrokenStopClient()
    runner = CubeMissionRunner(client, "dry-run", mission=MISSION, site=SITE, pose_mode="off",
                               mission_log=tmp_path / "mission.jsonl", sleep_fn=lambda seconds: None)
    assert runner.run(confirm=True).status == "FAIL"


def test_a_stop_already_inside_the_tolerance_circle_is_not_trimmed(tmp_path):
    """2026-09-21 enlarged trial: a 12 cm arrival (INSIDE) was 'corrected' by 20 cm minimum
    moves on a 10.1 cm axis delta, overshot, and finished 25.7 cm out after two trims."""
    bursts = [[_report(281, 292)] * 10,      # WP1 (270, 300): 13.6 cm off, 11 cm of it along x
              [_report(150, 300)] * 10]
    runner, client = _runner(tmp_path, "reanchor", bursts)
    assert runner.run(confirm=True).status == "PASS"
    wp1 = _log(tmp_path)[0]
    assert wp1["tolerance"] == "INSIDE" and wp1["trim_rounds_sent"] == 0
    assert "left 20" not in client.commands


# -- mission tuning: trim rounds per stop, leg speed ---------------------------

def test_a_mission_can_allow_up_to_ten_trim_rounds_and_stops_once_inside(tmp_path):
    """The convergence test: keep trimming while the drone is outside the 15 cm circle."""
    mission = Mission(270, 250, (Waypoint("WP1", 270, 300, 100),), max_trim_rounds=10)
    far = [[_report(270, 280)] * 10] * 5            # 20 cm short, five trims that do nothing
    runner, client = _runner(tmp_path, "reanchor", far + [[_report(272, 296)] * 10], mission=mission)
    assert runner.run(confirm=True).status == "PASS"
    wp1 = _log(tmp_path)[0]
    assert wp1["trim_rounds_sent"] == 5 and wp1["tolerance"] == "INSIDE"
    assert wp1["trim_errors_cm"] == [20.0] * 5 + [pytest.approx(4.5, abs=0.1)]
    assert client.commands.count("forward 20") == 5


def test_trim_rounds_are_bounded_by_the_mission(tmp_path):
    mission = Mission(270, 250, (Waypoint("WP1", 270, 300, 100),), max_trim_rounds=4)
    runner, client = _runner(tmp_path, "reanchor", [[_report(270, 280)] * 10] * 5, mission=mission)
    assert runner.run(confirm=True).status == "PASS"
    wp1 = _log(tmp_path)[0]
    assert wp1["trim_rounds_sent"] == 4 and wp1["tolerance"] == "OUTER"
    assert len(wp1["trim_errors_cm"]) == 5


@pytest.mark.parametrize("rounds", [0, 11])
def test_trim_rounds_outside_one_to_ten_are_refused(rounds):
    with pytest.raises(ValueError, match="max_trim_rounds"):
        Mission(None, None, (Waypoint("WP1", 270, 300, 100),), max_trim_rounds=rounds)


@pytest.mark.parametrize("speed", [10, 60])
def test_leg_speeds_outside_twenty_to_fifty_are_refused(speed):
    with pytest.raises(ValueError, match="leg_speed_cm_s"):
        Mission(None, None, (Waypoint("WP1", 270, 300, 100),), leg_speed_cm_s=speed)


def test_legs_fly_at_the_mission_speed_and_trims_go_back_to_twenty(tmp_path):
    mission = Mission(270, 250, (Waypoint("WP1", 270, 300, 100), Waypoint("WP2", 150, 300, 100)),
                      leg_speed_cm_s=40)
    bursts = [[_report(270, 282)] * 10, [_report(271, 299)] * 10, [_report(150, 300)] * 10]
    runner, client = _runner(tmp_path, "reanchor", bursts, mission=mission)
    assert runner.run(confirm=True).status == "PASS"
    moves = [c for c in client.commands if c.split()[0] in {"speed", "forward", "left", "back", "right"}]
    assert moves == ["speed 20",                         # preflight
                     "speed 40", "forward 50",           # leg to WP1
                     "speed 20", "forward 20",           # trim
                     "speed 40", "left 121"]             # leg to WP2 from the trimmed fix
    assert {e["leg_speed_cm_s"] for e in _log(tmp_path)} == {40}


def test_load_mission_reads_the_tuning_fields(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"schema_version": "cube-mission-v1", "max_trim_rounds": 10,
                                "leg_speed_cm_s": 35,
                                "waypoints": [{"name": "A", "x_cm": 250, "y_cm": 250, "z_cm": 100}]}))
    mission = load_mission(path)
    assert (mission.max_trim_rounds, mission.leg_speed_cm_s, mission.start_x_cm) == (10, 35, None)


def test_load_mission_defaults_keep_the_old_behaviour(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"schema_version": "cube-mission-v1",
                                "waypoints": [{"name": "A", "x_cm": 250, "y_cm": 250, "z_cm": 100}]}))
    mission = load_mission(path)
    assert (mission.max_trim_rounds, mission.leg_speed_cm_s) == (2, 20)


# -- speed against inset, tag coverage, return to takeoff, vertical check (2026-09-22) ------------

class _Coverage:
    """Stands in for cube.coverage.CoverageModel: a verdict per (x, y)."""

    def __init__(self, levels):
        self.levels = levels

    def verdict(self, x_cm, y_cm, z_cm):
        from sentinel.cube.coverage import Coverage
        level = self.levels.get((x_cm, y_cm), "OK")
        return Coverage(level, 0 if level == "BLIND" else 2, 0 if level == "BLIND" else 1, None)


def _site(inset_m):
    return parse_site({**SITE_DOC, "commanded": {**SITE_DOC["commanded"], "inset_m": inset_m}})


def _plan(tmp_path, mission, *, site=SITE, coverage=None, land_during_move=False, pose_mode="reanchor"):
    events = []
    runner = CubeMissionRunner(DryRunClient(), "dry-run", mission=mission, site=site,
                               pose_mode=pose_mode, mission_log=tmp_path / "m.jsonl",
                               burst_fn=lambda: [], sleep_fn=lambda s: None, coverage=coverage,
                               land_during_move=land_during_move,
                               progress=lambda event, message: events.append((event, message)))
    return runner, events


def test_legs_at_fifty_need_a_forty_cm_inset(tmp_path):
    fast = Mission(270, 250, (Waypoint("WP1", 270, 300, 100),), leg_speed_cm_s=50)
    with pytest.raises(PlanError, match="50 cm/s need the commanded area 40 cm"):
        _plan(tmp_path, fast, site=_site(0.3))
    _plan(tmp_path, fast, site=_site(0.4))
    _plan(tmp_path, Mission(270, 250, (Waypoint("WP1", 270, 300, 100),), leg_speed_cm_s=40),
          site=_site(0.3))


def test_land_during_move_is_refused_where_the_watchdog_would_trip_inside_the_commanded_area(tmp_path):
    at_forty = Mission(270, 250, (Waypoint("WP1", 270, 300, 100),), leg_speed_cm_s=40)
    with pytest.raises(PlanError, match="land_during_move"):
        _plan(tmp_path, at_forty, land_during_move=True)
    _plan(tmp_path, Mission(270, 250, (Waypoint("WP1", 270, 300, 100),)), land_during_move=True)


def test_a_stop_the_tags_cannot_be_seen_from_is_refused_unless_flown_open_loop(tmp_path):
    blind = _Coverage({(270, 300): "BLIND"})
    with pytest.raises(PlanError, match="WP1: the tags can be lost"):
        _plan(tmp_path, Mission(270, 250, (Waypoint("WP1", 270, 300, 100),)), coverage=blind)
    _plan(tmp_path, Mission(270, 250, (Waypoint("WP1", 270, 300, 100, localise=False),)),
          coverage=blind)
    _plan(tmp_path, Mission(270, 250, (Waypoint("WP1", 270, 300, 100),)), coverage=blind,
          pose_mode="off")


def test_a_marginal_stop_flies_with_an_alert(tmp_path):
    _, events = _plan(tmp_path, Mission(270, 250, (Waypoint("WP1", 270, 300, 100),)),
                      coverage=_Coverage({(270, 300): "MARGINAL"}))
    assert any(e == "alert" and "WP1: tag coverage is marginal" in m for e, m in events)


def test_return_to_start_flies_back_to_the_measured_takeoff_point(tmp_path):
    mission = Mission(None, None, (Waypoint("WP1", 270, 300, 100),), return_to_start=True)
    bursts = [[_report(250, 240)] * 10, [_report(270, 300)] * 10, [_report(252, 243)] * 10]
    runner, client = _runner(tmp_path, "reanchor", bursts, mission=mission)
    assert runner.run(confirm=True).status == "PASS"
    start, wp1, back = _log(tmp_path)
    assert back["waypoint"] == "RETURN" and back["commanded_cm"] == [250, 240, 100]
    assert back["status"] == "LOCALISED" and back["return_error_cm"] == pytest.approx(3.6, abs=0.1)
    moves = [c for c in client.commands if c.split()[0] in {"forward", "back", "left", "right"}]
    assert moves[-2:] == ["left 20", "back 60"]


def test_return_to_a_takeoff_point_outside_the_commanded_area_goes_to_the_nearest_one(tmp_path):
    mission = Mission(None, None, (Waypoint("WP1", 270, 300, 100),), return_to_start=True)
    bursts = [[_report(250, 215)] * 10, [_report(270, 300)] * 10, [_report(250, 230)] * 10]
    events = []
    runner, _ = _runner(tmp_path, "reanchor", bursts, mission=mission)
    runner.progress = lambda event, message: events.append((event, message))
    assert runner.run(confirm=True).status == "PASS"
    assert _log(tmp_path)[-1]["commanded_cm"] == [250, 230, 100]     # SITE commands y >= 230
    assert any("RETURN: takeoff point" in m for _, m in events)


def test_return_counts_toward_the_stop_limit():
    with pytest.raises(ValueError, match="at most 8 stops"):
        Mission(None, None, tuple(Waypoint(f"W{i}", 250, 250, 100) for i in range(8)),
                return_to_start=True)


def test_load_mission_reads_return_to_start(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"schema_version": "cube-mission-v1", "return_to_start": True,
                                "waypoints": [{"name": "A", "x_cm": 250, "y_cm": 250, "z_cm": 100}]}))
    assert load_mission(path).return_to_start is True


class _TagMap:
    room_scale_m_per_unit = 0.1

    def tag(self, tag_id):
        from types import SimpleNamespace
        return SimpleNamespace(x_units=25.0, y_units=40.0, z_units=0.55)


def _ranged(x_cm, y_cm, slant_m):
    return DronePoseReport(
        report_id="p", run_id="r", observed_at=datetime.now(timezone.utc),
        source_kind="SIMULATED", point=(x_cm / 10.0, y_cm / 10.0), heading_deg=90.0,
        altitude_m=None, method="APRILTAG_36H11_PNP",
        tag_observations=(TagObservation(0, "STAND_0", slant_m, 0.5),), reprojection_error_px=0.5)


def test_tag_implied_height_is_the_vertical_leg_of_the_range_triangle():
    from sentinel.cube.mission import tag_implied_height_cm
    # 160 cm in front of a tag at 55 cm, 30 cm above it: slant = hypot(160, 30)
    reports = [_ranged(250, 240, 1.6279)] * 5
    assert tag_implied_height_cm(reports, _TagMap(), (250.0, 240.0)) == pytest.approx(85.0, abs=0.5)
    assert tag_implied_height_cm(reports, None, (250.0, 240.0)) is None


def test_a_takeoff_height_the_tags_disagree_with_raises_an_alert(tmp_path):
    runner, events = _plan(tmp_path, Mission(270, 250, (Waypoint("WP1", 270, 300, 100),)))
    runner.tag_map = _TagMap()
    entry = {"height_cm": 100}
    # tags say 55 + 40 = 95 cm... with the site's takeoff height 0 that agrees with 100
    runner._check_vertical("WP1", [_ranged(250, 240, math.hypot(1.6, 0.40))] * 5, (250.0, 240.0), entry)
    assert entry["vertical_mismatch_cm"] == pytest.approx(-5.0, abs=0.5) and not events
    # tags say 55 + 75 = 130 cm against 100: the site's vertical model is 30 cm out
    runner._check_vertical("WP1", [_ranged(250, 240, math.hypot(1.6, 0.75))] * 5, (250.0, 240.0), entry)
    assert entry["tag_height_cm"] == pytest.approx(130.0, abs=0.5)
    assert any(e == "alert" and "altitude bias or tag heights are wrong" in m for e, m in events)
