import pytest

from sentinel.cube.observe import observation_candidates
from sentinel.envelope import FlightEnvelope

ENV = FlightEnvelope(min_x_cm=60, max_x_cm=480, min_y_cm=230, max_y_cm=390,
                     min_z_cm=80, max_z_cm=180, max_move_cm=300)


def test_a_narrow_alley_needs_only_the_close_candidate():
    (c,) = observation_candidates(150, 500, 120, ENV)
    assert (c.kind, c.x_cm, c.y_cm, c.z_cm, c.look_deg) == ("close_and_deep", 150, 390, 80, 0)
    assert c.standoff_cm == 110 and c.width_fraction == 1.0
    assert c.near_blind_cm == pytest.approx(80 / 0.7373, abs=1.0)


def test_a_wide_alley_adds_a_back_and_wide_candidate():
    close, wide = observation_candidates(150, 500, 200, ENV)
    assert close.width_fraction == pytest.approx(1.406 * 110 / 200, abs=0.01)
    assert (wide.kind, wide.x_cm, wide.y_cm) == ("back_and_wide", 150, 357)
    assert wide.width_fraction == 1.0


def test_an_alley_beside_the_bounds_gets_an_oblique_look():
    (c,) = observation_candidates(20, 500, 120, ENV)
    assert (c.kind, c.x_cm, c.y_cm, c.look_deg) == ("oblique", 60, 390, 20)
