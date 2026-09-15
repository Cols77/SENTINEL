"""Validation for SENTINEL's one-metre flight envelope."""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Direction, Position


class EnvelopeViolation(ValueError):
    """Raised when a position or movement is outside the safety envelope."""


@dataclass(slots=True)
class FlightEnvelope:
    """A closed 1 m x 1 m x 1 m cube around the open-loop estimate."""

    min_x_cm: int = -50
    max_x_cm: int = 50
    min_y_cm: int = -50
    max_y_cm: int = 50
    min_z_cm: int = 150
    max_z_cm: int = 250
    min_move_cm: int = 20
    max_move_cm: int = 500
    position: Position = field(default_factory=Position)

    def contains(self, position: Position) -> bool:
        return (
            self.min_x_cm <= position.x_cm <= self.max_x_cm
            and self.min_y_cm <= position.y_cm <= self.max_y_cm
            and self.min_z_cm <= position.z_cm <= self.max_z_cm
        )

    def validate_position(self, position: Position) -> Position:
        if not self.contains(position):
            raise EnvelopeViolation(
                "position is outside the flight envelope: "
                f"({position.x_cm}, {position.y_cm}, {position.z_cm}) cm"
            )
        return position

    def validate_distance(self, distance_cm: int) -> int:
        if isinstance(distance_cm, bool) or not isinstance(distance_cm, int):
            raise EnvelopeViolation("movement distance must be an integer number of centimetres")
        if not self.min_move_cm <= distance_cm <= self.max_move_cm:
            raise EnvelopeViolation(
                f"movement distance must be between {self.min_move_cm} and "
                f"{self.max_move_cm} cm"
            )
        return distance_cm

    def validate_move(
        self, position: Position, direction: Direction | str, distance_cm: int
    ) -> Position:
        self.validate_distance(distance_cm)
        try:
            target = position.moved(direction, distance_cm)
        except ValueError as error:
            raise EnvelopeViolation(f"unknown movement direction: {direction!r}") from error
        return self.validate_position(target)

    def move(self, direction: Direction | str, distance_cm: int) -> Position:
        """Validate and commit a movement from the tracked position."""

        self.position = self.validate_move(self.position, direction, distance_cm)
        return self.position
