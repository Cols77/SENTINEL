"""Pure flight-envelope validation for conservative Tello movement."""

from dataclasses import dataclass

from .models import Position


_BOUND_FIELDS = frozenset(
    {"min_x", "max_x", "min_y", "max_y", "min_z", "max_z"}
)
_CONFIGURATION_FIELDS = _BOUND_FIELDS | {"position"}


class EnvelopeViolation(ValueError):
    """Raised when a planned movement leaves the flight envelope."""


@dataclass
class FlightEnvelope:
    """A mutable inclusive cube tracking the planned launch-frame position.

    Coordinates use a fixed launch frame: +x is right, -x is left, +y is
    forward, -y is back, and +z is up.  There is no heading state yet, so
    rotation does not change this convention.
    """

    min_x: int = -50
    max_x: int = 50
    min_y: int = -50
    max_y: int = 50
    min_z: int = 150
    max_z: int = 250
    position: Position = Position(0, 0, 150)

    def __setattr__(self, name: str, value: object) -> None:
        """Validate public state assignments without leaving partial changes."""

        if name not in _CONFIGURATION_FIELDS:
            object.__setattr__(self, name, value)
            return

        previous = self.__dict__.get(name)
        had_previous = name in self.__dict__
        object.__setattr__(self, name, value)

        if not _CONFIGURATION_FIELDS.issubset(self.__dict__):
            return

        try:
            self._validate_configuration()
        except ValueError:
            if had_previous:
                object.__setattr__(self, name, previous)
            else:
                del self.__dict__[name]
            raise

    def __post_init__(self) -> None:
        self._validate_configuration()

    def contains(self, position: Position) -> bool:
        """Return whether *position* is inside all six inclusive bounds."""

        self._validate_position(position)
        return (
            self.min_x <= position.x <= self.max_x
            and self.min_y <= position.y <= self.max_y
            and self.min_z <= position.z <= self.max_z
        )

    def project_move(self, direction: str, distance_cm: int) -> Position:
        """Validate and return a candidate position without changing state.

        Malformed directions and distances raise ``ValueError``.  A valid
        command whose candidate crosses a boundary raises ``EnvelopeViolation``.
        """

        self._validate_distance(distance_cm)
        try:
            dx, dy, dz = {
                "forward": (0, distance_cm, 0),
                "back": (0, -distance_cm, 0),
                "right": (distance_cm, 0, 0),
                "left": (-distance_cm, 0, 0),
                "up": (0, 0, distance_cm),
                "down": (0, 0, -distance_cm),
            }[direction]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"unsupported movement direction: {direction}") from exc

        candidate = Position(
            self.position.x + dx,
            self.position.y + dy,
            self.position.z + dz,
        )
        if not self.contains(candidate):
            raise EnvelopeViolation(
                f"{direction} {distance_cm} cm would leave envelope: {candidate}"
            )
        return candidate

    def commit_move(self, direction: str, distance_cm: int) -> Position:
        """Validate a move, assign the candidate, and return the new position."""

        candidate = self.project_move(direction, distance_cm)
        self.position = candidate
        return candidate

    def _validate_bounds(self) -> None:
        bounds = (
            self.min_x,
            self.max_x,
            self.min_y,
            self.max_y,
            self.min_z,
            self.max_z,
        )
        if any(
            isinstance(bound, bool) or not isinstance(bound, int)
            for bound in bounds
        ):
            raise ValueError("envelope bounds must be integers")
        if (
            self.min_x > self.max_x
            or self.min_y > self.max_y
            or self.min_z > self.max_z
        ):
            raise ValueError("envelope bounds must be ordered from minimum to maximum")

    def _validate_configuration(self) -> None:
        self._validate_bounds()
        self._validate_position(self.position)
        if not self.contains(self.position):
            raise ValueError(
                f"position must be inside the envelope: {self.position}"
            )

    @staticmethod
    def _validate_position(position: Position) -> None:
        if not isinstance(position, Position):
            raise ValueError("position must be a Position")

    @staticmethod
    def _validate_distance(distance_cm: int) -> None:
        if (
            isinstance(distance_cm, bool)
            or not isinstance(distance_cm, int)
            or distance_cm < 20
            or distance_cm > 500
        ):
            raise ValueError("Tello movement distance must be between 20 and 500 cm")
