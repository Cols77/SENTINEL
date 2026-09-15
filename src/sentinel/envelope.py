"""Pure flight-envelope geometry for conservative Tello tests."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Position:
    """A drone position expressed as integer centimetres."""

    x: int
    y: int
    z: int

    def __post_init__(self) -> None:
        if any(isinstance(coordinate, bool) or not isinstance(coordinate, int)
               for coordinate in (self.x, self.y, self.z)):
            raise TypeError("Position coordinates must be integers in centimetres")


@dataclass(frozen=True)
class FlightEnvelope:
    """Inclusive rectangular airspace for supervised Tello movement.

    The horizontal convention is +x to the right and +y forward from the
    current orientation; -x is left and -y is back.  +z is up and -z is down.
    """

    x_min: int = -50
    x_max: int = 50
    y_min: int = -50
    y_max: int = 50
    z_min: int = 150
    z_max: int = 250

    @property
    def initial_position(self) -> Position:
        """Return the conservative starting point on the lower face."""

        return Position(0, 0, self.z_min)

    def contains(self, position: Position) -> bool:
        """Return whether *position* is within all six inclusive bounds."""

        self._require_position(position)
        return (
            self.x_min <= position.x <= self.x_max
            and self.y_min <= position.y <= self.y_max
            and self.z_min <= position.z <= self.z_max
        )

    def project_move(
        self, position: Position, direction: str, distance_cm: int
    ) -> Position:
        """Validate and return the position reached by a bounded move.

        This method is pure: it never mutates *position* or the envelope.
        """

        self._require_position(position)
        offset = self._direction_offset(direction)
        self._validate_distance(distance_cm)

        projected = Position(
            position.x + offset[0] * distance_cm,
            position.y + offset[1] * distance_cm,
            position.z + offset[2] * distance_cm,
        )
        if not self.contains(projected):
            raise ValueError(
                f"projected position {projected} is outside the flight envelope"
            )
        return projected

    def commit_move(
        self, position: Position, direction: str, distance_cm: int
    ) -> Position:
        """Return a validated new position without mutating the input."""

        return self.project_move(position, direction, distance_cm)

    @staticmethod
    def _require_position(position: Position) -> None:
        if not isinstance(position, Position):
            raise TypeError("position must be a Position")

    @staticmethod
    def _direction_offset(direction: str) -> tuple[int, int, int]:
        offsets = {
            "up": (0, 0, 1),
            "down": (0, 0, -1),
            "forward": (0, 1, 0),
            "back": (0, -1, 0),
            "left": (-1, 0, 0),
            "right": (1, 0, 0),
        }
        if not isinstance(direction, str) or direction not in offsets:
            raise ValueError(
                "unsupported direction; expected one of "
                "up, down, forward, back, left, right"
            )
        return offsets[direction]

    @staticmethod
    def _validate_distance(distance_cm: int) -> None:
        if (
            isinstance(distance_cm, bool)
            or not isinstance(distance_cm, int)
            or not 20 <= distance_cm <= 500
        ):
            raise ValueError(
                "distance_cm must be an integer from 20 to 500 cm"
            )
