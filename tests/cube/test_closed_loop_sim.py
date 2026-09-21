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
