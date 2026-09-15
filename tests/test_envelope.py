import pytest

from sentinel.envelope import FlightEnvelope, Position


def test_exact_boundary_points_are_inside_the_default_envelope():
    envelope = FlightEnvelope()

    boundary_points = (
        Position(-50, -50, 150),
        Position(-50, 50, 250),
        Position(50, -50, 250),
        Position(50, 50, 150),
        Position(0, 0, 150),
        Position(0, 0, 250),
    )

    for point in boundary_points:
        assert envelope.contains(point)


def test_initial_position_is_one_point_five_metres_above_ground():
    assert FlightEnvelope().initial_position == Position(0, 0, 150)


@pytest.mark.parametrize(
    ("direction", "expected"),
    (
        ("up", Position(0, 0, 220)),
        ("down", Position(0, 0, 180)),
        ("forward", Position(0, 20, 200)),
        ("back", Position(0, -20, 200)),
        ("left", Position(-20, 0, 200)),
        ("right", Position(20, 0, 200)),
    ),
)
def test_each_direction_changes_its_expected_coordinate(direction, expected):
    assert FlightEnvelope().commit_move(Position(0, 0, 200), direction, 20) == expected


def test_down_from_two_metres_to_one_point_eight_metres_is_allowed():
    assert FlightEnvelope().commit_move(Position(0, 0, 200), "down", 20) == Position(
        0, 0, 180
    )


@pytest.mark.parametrize(
    ("position", "direction"),
    (
        (Position(-50, 0, 200), "left"),
        (Position(50, 0, 200), "right"),
        (Position(0, -50, 200), "back"),
        (Position(0, 50, 200), "forward"),
        (Position(0, 0, 150), "down"),
        (Position(0, 0, 250), "up"),
    ),
)
def test_every_face_rejects_an_outward_move(position, direction):
    with pytest.raises(ValueError, match="outside the flight envelope"):
        FlightEnvelope().commit_move(position, direction, 20)


@pytest.mark.parametrize("direction", ("hover", "UP", ""))
def test_unknown_directions_are_rejected(direction):
    with pytest.raises(ValueError, match="unsupported direction"):
        FlightEnvelope().project_move(Position(0, 0, 200), direction, 20)


@pytest.mark.parametrize("distance", (None, True, 0, -20, 19, 501, 20.5, "20"))
def test_invalid_distances_are_rejected(distance):
    with pytest.raises(ValueError, match="distance_cm must be an integer from 20 to 500"):
        FlightEnvelope().project_move(Position(0, 0, 200), "up", distance)


def test_invalid_transition_leaves_original_position_unchanged():
    original = Position(50, 0, 200)

    with pytest.raises(ValueError):
        FlightEnvelope().commit_move(original, "right", 20)

    assert original == Position(50, 0, 200)
