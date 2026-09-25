from sentinel.survey.envelope import SweepEnvelope
from sentinel.survey.options import generate_options

ENVELOPE = SweepEnvelope(min_x_cm=-150, max_x_cm=150, min_y_cm=0, max_y_cm=100,
                         min_z_cm=80, max_z_cm=180, standoff_cm=300)


def test_every_generated_stop_lies_inside_the_envelope():
    for option in generate_options(ENVELOPE):
        for x_cm, y_cm in option.stops:
            assert ENVELOPE.min_x_cm <= x_cm <= ENVELOPE.max_x_cm
            assert ENVELOPE.min_y_cm <= y_cm <= ENVELOPE.max_y_cm
        assert ENVELOPE.min_z_cm <= option.z_cm <= ENVELOPE.max_z_cm


def test_the_main_rail_options_never_rotate():
    """Spec 5.3: differential yaw between stops is the largest depth error
    term, and rotate-to-look is what introduces it."""
    rails = [o for o in generate_options(ENVELOPE) if o.option_id.startswith("RAIL_")]
    assert rails
    for option in rails:
        assert option.looks_deg == (0,)


def test_no_option_exceeds_the_mission_runner_waypoint_cap():
    for option in generate_options(ENVELOPE):
        assert len(option.stops) <= 8


def test_options_are_deterministic_and_uniquely_identified():
    first = generate_options(ENVELOPE)
    assert first == generate_options(ENVELOPE)
    assert len({o.option_id for o in first}) == len(first)


def test_each_option_states_which_classes_it_can_resolve():
    for option in generate_options(ENVELOPE):
        assert "computer" in option.resolvable_classes


def test_a_close_look_option_exists_and_resolves_more_than_the_main_rail():
    options = {o.option_id: o for o in generate_options(ENVELOPE)}
    close = options["CLOSE_LOOK_CENTRE"]
    rail = options["RAIL_MID_HEIGHT"]
    assert close.range_cm < rail.range_cm
    assert set(rail.resolvable_classes) <= set(close.resolvable_classes)
    assert "mug" in close.resolvable_classes
    assert "mug" not in rail.resolvable_classes
