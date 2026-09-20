import pytest

from sentinel.survey.observability import MIN_DETECTABLE_PX, pixels_at, resolvable_classes


@pytest.mark.parametrize("class_name, expected_px", [
    ("person", 113.8), ("computer", 79.7), ("phone", 34.2), ("pen", 2.3),
])
def test_apparent_size_at_three_metres_matches_the_spec(class_name, expected_px):
    assert pixels_at(class_name, 300.0) == pytest.approx(expected_px, abs=0.2)


def test_a_pen_is_never_resolvable_at_demo_range():
    assert "pen" not in resolvable_classes(300.0)
    assert pixels_at("pen", 300.0) < MIN_DETECTABLE_PX


def test_moving_closer_can_make_a_phone_resolvable():
    """This is what gives a follow-up sweep a real reason to exist."""
    assert "phone" not in resolvable_classes(600.0)
    assert "phone" in resolvable_classes(150.0)


def test_an_unknown_class_raises_rather_than_guessing():
    with pytest.raises(KeyError):
        pixels_at("bicycle", 300.0)
