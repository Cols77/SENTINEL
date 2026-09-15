from __future__ import annotations

import pytest

from sentinel.envelope import EnvelopeViolation, FlightEnvelope
from sentinel.models import Direction, Position


@pytest.mark.parametrize(
    "position",
    [
        Position(x_cm=-50, y_cm=0, z_cm=200),
        Position(x_cm=50, y_cm=0, z_cm=200),
        Position(x_cm=0, y_cm=-50, z_cm=200),
        Position(x_cm=0, y_cm=50, z_cm=200),
        Position(x_cm=0, y_cm=0, z_cm=150),
        Position(x_cm=0, y_cm=0, z_cm=250),
    ],
)
def test_all_six_cube_faces_are_inclusive(position: Position) -> None:
    assert FlightEnvelope().contains(position)


@pytest.mark.parametrize(
    "position",
    [
        Position(x_cm=-51, y_cm=0, z_cm=200),
        Position(x_cm=51, y_cm=0, z_cm=200),
        Position(x_cm=0, y_cm=-51, z_cm=200),
        Position(x_cm=0, y_cm=51, z_cm=200),
        Position(x_cm=0, y_cm=0, z_cm=149),
        Position(x_cm=0, y_cm=0, z_cm=251),
    ],
)
def test_positions_outside_any_cube_face_are_rejected(position: Position) -> None:
    assert not FlightEnvelope().contains(position)


@pytest.mark.parametrize("distance_cm", [20, 500])
def test_tello_movement_distance_inclusive_bounds_are_accepted(distance_cm: int) -> None:
    FlightEnvelope().validate_distance(distance_cm)


@pytest.mark.parametrize("distance_cm", [19, 501])
def test_tello_movement_distance_outside_bounds_is_rejected(distance_cm: int) -> None:
    with pytest.raises(EnvelopeViolation):
        FlightEnvelope().validate_distance(distance_cm)


def test_move_crossing_a_cube_face_is_rejected_before_transmission() -> None:
    envelope = FlightEnvelope()

    with pytest.raises(EnvelopeViolation):
        envelope.validate_move(
            Position(x_cm=40, y_cm=0, z_cm=200), Direction.RIGHT, 20
        )


def test_move_returns_the_validated_target_position() -> None:
    target = FlightEnvelope().validate_move(
        Position(x_cm=0, y_cm=0, z_cm=200), Direction.FORWARD, 20
    )

    assert target == Position(x_cm=0, y_cm=20, z_cm=200)

