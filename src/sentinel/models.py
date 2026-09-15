"""Small, backend-independent value objects used by SENTINEL."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class Direction(str, Enum):
    """The six translational directions supported by the Tello SDK."""

    FORWARD = "forward"
    BACK = "back"
    RIGHT = "right"
    LEFT = "left"
    UP = "up"
    DOWN = "down"


class RotationDirection(str, Enum):
    """The two rotational directions supported by the Tello SDK."""

    CLOCKWISE = "cw"
    COUNTERCLOCKWISE = "ccw"


@dataclass(frozen=True, slots=True)
class Position:
    """An open-loop position estimate in centimetres."""

    x_cm: int = 0
    y_cm: int = 0
    z_cm: int = 200

    def moved(self, direction: Direction | str, distance_cm: int) -> Position:
        """Return the position reached by a translational command."""

        direction = Direction(direction)
        offsets = {
            Direction.FORWARD: (0, distance_cm, 0),
            Direction.BACK: (0, -distance_cm, 0),
            Direction.RIGHT: (distance_cm, 0, 0),
            Direction.LEFT: (-distance_cm, 0, 0),
            Direction.UP: (0, 0, distance_cm),
            Direction.DOWN: (0, 0, -distance_cm),
        }
        dx, dy, dz = offsets[direction]
        return Position(self.x_cm + dx, self.y_cm + dy, self.z_cm + dz)

    def as_dict(self) -> dict[str, int]:
        return {"x_cm": self.x_cm, "y_cm": self.y_cm, "z_cm": self.z_cm}


@dataclass(frozen=True, slots=True)
class Telemetry:
    """The stable telemetry subset consumed by the runner."""

    battery_percent: int
    height_cm: int
    flight_time_s: int = 0
    temperature_c: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "battery_percent": self.battery_percent,
            "height_cm": self.height_cm,
            "flight_time_s": self.flight_time_s,
            "temperature_c": self.temperature_c,
        }


@dataclass(frozen=True, slots=True)
class VideoFrame:
    """Frame metadata exposed by both the dry-run and real backends."""

    width: int
    height: int
    timestamp: datetime | None = None
    pixel_format: str = "rgb24"
    frame_number: int = 0
    timestamp_ms: int = 0
    data: bytes = b""

    def as_dict(self) -> dict[str, Any]:
        return {
            "frame_number": self.frame_number,
            "width": self.width,
            "height": self.height,
            "pixel_format": self.pixel_format,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "timestamp_ms": self.timestamp_ms,
            "data_bytes": len(self.data),
        }


@dataclass(frozen=True, slots=True)
class CommandRecord:
    """A deterministic audit record for one backend operation."""

    command: str
    response: str = "ok"
    position: Position | None = None
    timestamp: datetime | None = None
    sequence: int = 0
    arguments: tuple[int | str, ...] = ()

    @property
    def display(self) -> str:
        if not self.arguments:
            return self.command
        return f"{self.command} {' '.join(str(value) for value in self.arguments)}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "command": self.command,
            "response": self.response,
            "position": self.position.as_dict() if self.position else None,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "arguments": list(self.arguments),
            "display": self.display,
        }


@dataclass(frozen=True, slots=True)
class AdapterInfo:
    """Metadata for a local IPv4 adapter used to reach the Tello."""

    name: str
    index: int
    local_ip: str
    is_up: bool
    is_default_route: bool
    gateway: str | None = None
    metric: int | None = None
    netmask: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "index": self.index,
            "local_ip": self.local_ip,
            "is_up": self.is_up,
            "is_default_route": self.is_default_route,
            "gateway": self.gateway,
            "metric": self.metric,
            "netmask": self.netmask,
        }


@dataclass(frozen=True, slots=True)
class RunReport:
    """Final result emitted by the runner and serialized as JSONL."""

    status: str
    mode: str
    commands: tuple[CommandRecord, ...] = ()
    video_frame: VideoFrame | None = None
    adapter: AdapterInfo | None = None
    error: str | None = None
    backend: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialize the audit metadata without embedding raw frame data."""

        return {
            "status": self.status,
            "mode": self.mode,
            "backend": self.backend or self.mode,
            "commands": [command.as_dict() for command in self.commands],
            "video_frame": self.video_frame.as_dict()
            if self.video_frame is not None
            else None,
            "adapter": self.adapter.as_dict() if self.adapter is not None else None,
            "error": self.error,
        }
