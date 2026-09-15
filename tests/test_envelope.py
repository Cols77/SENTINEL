from dataclasses import FrozenInstanceError

import pytest

from sentinel.envelope import EnvelopeViolation, FlightEnvelope
from sentinel.models import Position


def test_position_is_frozen_and_uses_integer_centimetres():
    position = Position(1, 2, 3)

    with pytest.raises(FrozenInstanceError):
        position.x = 4


def test_default_envelope_starts_at_launch_origin_and_lower_altitude():
    envelope = FlightEnvelope()

    assert envelope.position == Position(0, 0, 150)
    assert envelope.contains(envelope.position)


@pytest.mark.parametrize(
    "position",
    [
        Position(-50, 0, 200),
        Position(50, 0, 200),
        Position(0, -50, 200),
        Position(0, 50, 200),
        Position(0, 0, 150),
        Position(0, 0, 250),
        Position(-50, -50, 150),
        Position(50, 50, 250),
    ],
)
def test_exact_boundary_points_are_inside_the_default_envelope(position):
    assert FlightEnvelope().contains(position)


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        ("forward", Position(0, 50, 150)),
        ("back", Position(0, -50, 150)),
        ("right", Position(50, 0, 150)),
        ("left", Position(-50, 0, 150)),
        ("up", Position(0, 0, 200)),
    ],
)
def test_project_move_returns_expected_position_without_updating_state(
    direction, expected
):
    envelope = FlightEnvelope()

    assert envelope.project_move(direction, 50) == expected
    assert envelope.position == Position(0, 0, 150)


def test_project_move_allows_down_inside_the_cube():
    envelope = FlightEnvelope(position=Position(0, 0, 200))

    assert envelope.project_move("down", 50) == Position(0, 0, 150)
    assert envelope.position == Position(0, 0, 200)


@pytest.mark.parametrize(
    ("position", "direction"),
    [
        (Position(50, 0, 150), "right"),
        (Position(-50, 0, 150), "left"),
        (Position(0, 50, 150), "forward"),
        (Position(0, -50, 150), "back"),
        (Position(0, 0, 250), "up"),
        (Position(0, 0, 150), "down"),
    ],
)
def test_project_move_rejects_crossing_any_cube_face(position, direction):
    envelope = FlightEnvelope(position=position)

    with pytest.raises(EnvelopeViolation):
        envelope.project_move(direction, 20)

    assert envelope.position == position


def test_commit_move_updates_position_only_after_validation():
    envelope = FlightEnvelope()

    assert envelope.commit_move("forward", 50) == Position(0, 50, 150)
    assert envelope.position == Position(0, 50, 150)

    with pytest.raises(EnvelopeViolation):
        envelope.commit_move("forward", 20)

    assert envelope.position == Position(0, 50, 150)


def test_commit_moves_compose_state_across_multiple_steps():
    envelope = FlightEnvelope()

    assert envelope.commit_move("forward", 50) == Position(0, 50, 150)
    assert envelope.commit_move("right", 50) == Position(50, 50, 150)
    assert envelope.position == Position(50, 50, 150)


def test_invalid_position_assignment_raises_and_preserves_state():
    envelope = FlightEnvelope()
    original = envelope.position

    with pytest.raises(ValueError, match="position must be inside the envelope"):
        envelope.position = Position(51, 0, 150)

    assert envelope.position == original


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("min_x", 51),
        ("max_x", -51),
        ("min_y", 51),
        ("max_y", -51),
        ("min_z", 160),
        ("max_z", 149),
    ],
)
def test_invalid_bound_assignment_raises_and_preserves_configuration(
    attribute, value
):
    envelope = FlightEnvelope()
    original_bounds = (
        envelope.min_x,
        envelope.max_x,
        envelope.min_y,
        envelope.max_y,
        envelope.min_z,
        envelope.max_z,
    )

    with pytest.raises(ValueError):
        setattr(envelope, attribute, value)

    assert (
        envelope.min_x,
        envelope.max_x,
        envelope.min_y,
        envelope.max_y,
        envelope.min_z,
        envelope.max_z,
    ) == original_bounds
    assert envelope.position == Position(0, 0, 150)


@pytest.mark.parametrize("attribute", ["min_x", "max_y"])
def test_non_integer_bound_assignment_raises_and_preserves_configuration(attribute):
    envelope = FlightEnvelope()
    original = getattr(envelope, attribute)

    with pytest.raises(ValueError, match="bounds must be integers"):
        setattr(envelope, attribute, True if attribute == "min_x" else 50.5)

    assert getattr(envelope, attribute) == original


@pytest.mark.parametrize("direction", ["hover", "UP", ""])
def test_malformed_direction_raises_value_error_not_envelope_violation(direction):
    with pytest.raises(ValueError, match="unsupported movement direction") as error:
        FlightEnvelope().project_move(direction, 20)

    assert not isinstance(error.value, EnvelopeViolation)


@pytest.mark.parametrize("distance_cm", [None, True, 0, -20, 19, 501, 20.5, "20"])
def test_malformed_distance_raises_value_error_not_envelope_violation(distance_cm):
    with pytest.raises(ValueError, match="between 20 and 500 cm") as error:
        FlightEnvelope().project_move("up", distance_cm)

    assert not isinstance(error.value, EnvelopeViolation)


@pytest.mark.parametrize(
    "configuration",
    [
        {"min_x": True},
        {"max_y": 50.5},
        {"min_z": "150"},
        {"max_z": False},
    ],
)
def test_envelope_rejects_non_integer_or_boolean_bounds(configuration):
    with pytest.raises(ValueError, match="bounds must be integers"):
        FlightEnvelope(**configuration)


def test_envelope_rejects_unordered_bounds():
    with pytest.raises(ValueError, match="bounds must be ordered"):
        FlightEnvelope(min_x=50, max_x=-50)


@pytest.mark.parametrize(
    "position",
    [
        (0, 0, 150),
        Position(51, 0, 150),
        Position(0, 0, 149),
    ],
)
def test_envelope_rejects_invalid_initial_position(position):
    with pytest.raises(ValueError):
        FlightEnvelope(position=position)


def test_position_rejects_non_integer_coordinates():
    with pytest.raises(ValueError, match="Position coordinates must be integers"):
        Position(0, 0.5, 150)


def test_contains_rejects_a_non_position_value():
    with pytest.raises(ValueError, match="position must be a Position"):
        FlightEnvelope().contains((0, 0, 150))
