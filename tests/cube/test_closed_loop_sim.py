import random

import pytest

from sentinel.cube.closed_loop_sim import DriftingClient, DriftModel, fly_once, monte_carlo
from sentinel.cube.mission import Mission, Waypoint
from sentinel.cube.site import parse_site

SITE = parse_site({
    "schema_version": "cube-site-v1", "line_y_m": 4.0,
    "stands": [{"x_m": 2.156, "first_tag_id": 2, "heights_m": [0.55], "size_m": 0.192},
               {"x_m": 2.844, "first_tag_id": 0, "heights_m": [0.55], "size_m": 0.192}],
    "cube": {"min_x_m": 1.6, "max_x_m": 3.4, "min_y_m": 1.6, "max_y_m": 3.0, "max_z_m": 2.0},
    "commanded": {"inset_m": 0.3, "min_z_cm": 60, "max_z_cm": 180},
})
LOOP = Mission(250, 250, (
    Waypoint("RIGHT_NEAR", 280, 250, 100), Waypoint("RIGHT_FAR", 280, 215, 100),
    Waypoint("LEFT_FAR", 220, 215, 100), Waypoint("HOME", 250, 250, 100),
))
PERFECT = DriftModel(0, 0, 0, 0, 0, 0, 0, 0)


def test_a_perfect_drone_arrives_exactly_and_is_never_trimmed(tmp_path):
    flight = fly_once(LOOP, SITE, "reanchor", PERFECT, seed=1, log_path=tmp_path / "m.jsonl")
    assert flight["status"] == "PASS"
    assert [row["true_error_cm"] for row in flight["rows"]] == [0.0] * 4
    assert all(row["trim_rounds_sent"] == 0 for row in flight["rows"])


def test_moves_are_applied_in_the_room_frame():
    client = DriftingClient(250, 250, PERFECT, random.Random(0))
    client.connect()
    client.takeoff()
    client.move("right", 30)
    client.move("back", 50)
    assert (client.x_cm, client.y_cm) == pytest.approx((280, 200))
    client.rotate("ccw", 90)          # now facing -x
    client.move("forward", 20)
    assert (client.x_cm, client.y_cm) == pytest.approx((260, 200))


def test_reanchoring_returns_home_closer_than_open_loop():
    open_loop = monte_carlo(LOOP, SITE, "off", DriftModel(), runs=40, tolerance_cm=15)
    closed = monte_carlo(LOOP, SITE, "reanchor", DriftModel(), runs=40, tolerance_cm=15)
    assert closed["completed"] == 40
    assert closed["waypoints"]["HOME"]["median_cm"] < open_loop["waypoints"]["HOME"]["median_cm"]
    assert closed["waypoints"]["HOME"]["within_tolerance"] >= 0.9


def test_a_start_free_mission_flies_from_random_takeoff_points():
    free = Mission(None, None, LOOP.waypoints)
    closed = monte_carlo(free, SITE, "reanchor", DriftModel(), runs=30, tolerance_cm=15)
    assert closed["completed"] == 30
    assert closed["waypoints"]["HOME"]["within_tolerance"] >= 0.9


# -- the sim cube enforces the tag-coverage guard (2026-09-22) ------------------------------------

def _coverage(site):
    from pathlib import Path
    from sentinel.cube.coverage import CoverageModel
    root = Path(__file__).resolve().parents[2]
    return CoverageModel.from_files(site, root / "calibration/tag-map-calibrated.json")


BIASED = parse_site({
    "schema_version": "cube-site-v1", "line_y_m": 4.0,
    "stands": [{"x_m": 2.156, "first_tag_id": 2, "heights_m": [0.55], "size_m": 0.192},
               {"x_m": 2.844, "first_tag_id": 0, "heights_m": [0.55], "size_m": 0.192}],
    "cube": {"min_x_m": 1.6, "max_x_m": 3.4, "min_y_m": 1.6, "max_y_m": 3.0, "max_z_m": 2.0},
    "commanded": {"inset_m": 0.3, "min_z_cm": 60, "max_z_cm": 180},
    "altitude_bias_cm": 45,     # calibrated with coverage.HOVER_PITCH_DOWN_DEG, see test_coverage
})


def test_the_sim_refuses_the_stop_the_09_22_flight_lost_the_tags_at(tmp_path):
    from sentinel.cube.mission import PlanError
    r_near = Mission(250, 250, (Waypoint("R_NEAR", 300, 270, 100),))
    with pytest.raises(PlanError, match="R_NEAR: the tags can be lost"):
        fly_once(r_near, BIASED, "reanchor", PERFECT, seed=1, log_path=tmp_path / "m.jsonl",
                 coverage=_coverage(BIASED))


def test_the_simulated_camera_sees_nothing_where_the_drone_really_is_blind():
    """The guard checks commanded stops; drift can still carry the real drone out of view, and
    then the simulated burst is empty, as the real one was at R_NEAR."""
    model = _coverage(BIASED)
    assert model.tags_in_view(250, 330, 100) == 0 and model.tags_in_view(250, 250, 100) > 0
    client = DriftingClient(250, 330, PERFECT, random.Random(0))
    client.sees_tags = lambda: model.tags_in_view(client.x_cm, client.y_cm, 100) > 0
    assert client.burst() == []
    client.y_cm = 250
    assert len(client.burst()) == 12


def test_return_to_start_in_the_sim_comes_back_inside_tolerance():
    loop = Mission(None, None, LOOP.waypoints[:3], return_to_start=True)
    closed = monte_carlo(loop, BIASED, "reanchor", DriftModel(), runs=20, tolerance_cm=15,
                         coverage=_coverage(BIASED))
    assert closed["completed"] == 20
    assert closed["waypoints"]["RETURN"]["within_tolerance"] >= 0.9
