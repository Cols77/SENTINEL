"""Shared immutable domain models used by SENTINEL."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Position:
    """A drone position expressed as integer centimetres."""

    x: int
    y: int
    z: int

    def __post_init__(self) -> None:
        coordinates = (self.x, self.y, self.z)
        if any(
            isinstance(coordinate, bool) or not isinstance(coordinate, int)
            for coordinate in coordinates
        ):
            raise ValueError("Position coordinates must be integers in centimetres")


@dataclass(frozen=True)
class VideoFrame:
    """A decoded video frame produced by a client."""

    width: int
    height: int
    captured_at: datetime


@dataclass(frozen=True)
class CommandRecord:
    """An immutable client command and its observed response."""

    command: str
    response: str
    position: Position | None
    timestamp: datetime
