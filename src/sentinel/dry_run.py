"""Deterministic, network-free implementation of the drone client boundary."""

from collections.abc import Callable
from datetime import datetime, timezone
import math

from .client import DroneError, EmergencyAborted
from .models import CommandRecord, VideoFrame


_MOVEMENT_DIRECTIONS = frozenset({"forward", "back", "left", "right", "up", "down"})
_ROTATION_DIRECTIONS = frozenset({"cw", "ccw"})


class DryRunClient:
    """Simulate the supported Tello operations without opening a socket."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.network_opened = False
        self.connected = False
        self.streaming = False
        self.airborne = False
        self.emergency_triggered = False
        self._battery = 100
        self._height = 0
        self._speed = 10
        self._records: list[CommandRecord] = []

    @property
    def records(self) -> tuple[CommandRecord, ...]:
        """Return an immutable snapshot of the internal command history."""

        return tuple(self._records)

    def _ensure_available(self) -> None:
        if self.emergency_triggered:
            raise EmergencyAborted("dry-run emergency stop has been triggered")

    def _ensure_connected(self) -> None:
        self._ensure_available()
        if not self.connected:
            raise DroneError("dry-run client is not connected")

    def _timestamp(self) -> datetime:
        timestamp = self._clock()
        if (
            not isinstance(timestamp, datetime)
            or timestamp.tzinfo is None
            or timestamp.utcoffset() is None
        ):
            raise ValueError("clock must return a timezone-aware datetime")
        return timestamp.astimezone(timezone.utc)

    def _record(self, command: str, response: str = "ok") -> str:
        self._records.append(
            CommandRecord(
                command=command,
                response=response,
                position=None,
                timestamp=self._timestamp(),
            )
        )
        return response

    @staticmethod
    def _validate_command(command: str) -> None:
        if not isinstance(command, str) or not command.strip() or any(
            character in command for character in "\r\n"
        ):
            raise ValueError("command must be a non-empty single-line string")

    @staticmethod
    def _validate_range(value: int, name: str, minimum: int, maximum: int) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < minimum
            or value > maximum
        ):
            raise ValueError(f"{name} must be between {minimum} and {maximum}")

    def connect(self) -> None:
        self._ensure_available()
        self.connected = True
        self._record("command")

    def close(self) -> None:
        """Close the simulated resources; safe even after emergency abort."""

        self.streaming = False
        self.connected = False

    def send(self, command: str) -> str:
        self._ensure_connected()
        self._validate_command(command)
        return self._record(command)

    def battery(self) -> int:
        self._ensure_connected()
        return self._battery

    def height_cm(self) -> int:
        self._ensure_connected()
        return self._height

    def speed_cm_per_second(self) -> int:
        self._ensure_connected()
        return self._speed

    def sdk_version(self) -> str:
        self._ensure_connected()
        return "dry-run"

    def set_speed(self, cm_per_second: int) -> str:
        self._ensure_connected()
        self._validate_range(cm_per_second, "speed", 10, 100)
        self._speed = cm_per_second
        return self._record(f"speed {cm_per_second}")

    def takeoff(self) -> str:
        self._ensure_connected()
        if self.airborne:
            raise DroneError("dry-run drone is already airborne")
        self.airborne = True
        self._height = 100
        return self._record("takeoff")

    def land(self) -> str:
        self._ensure_connected()
        if not self.airborne:
            raise DroneError("dry-run drone is already grounded")
        self.airborne = False
        self._height = 0
        return self._record("land")

    def move(self, direction: str, distance_cm: int) -> str:
        self._ensure_connected()
        if not self.airborne:
            raise DroneError("dry-run movement requires an airborne drone")
        if not isinstance(direction, str) or direction not in _MOVEMENT_DIRECTIONS:
            raise ValueError(f"unsupported movement direction: {direction}")
        self._validate_range(distance_cm, "distance", 20, 500)
        if direction == "up":
            self._height += distance_cm
        elif direction == "down":
            if distance_cm > self._height:
                raise DroneError("dry-run movement cannot go below ground")
            self._height -= distance_cm
            if self._height == 0:
                self.airborne = False
        return self._record(f"{direction} {distance_cm}")

    def rotate(self, direction: str, degrees: int) -> str:
        self._ensure_connected()
        if not isinstance(direction, str) or direction not in _ROTATION_DIRECTIONS:
            raise ValueError(f"unsupported rotation direction: {direction}")
        self._validate_range(degrees, "degrees", 1, 360)
        return self._record(f"{direction} {degrees}")

    def stop(self) -> str:
        self._ensure_connected()
        return self._record("stop")

    def stream_on(self) -> str:
        self._ensure_connected()
        self.streaming = True
        return self._record("streamon")

    def stream_off(self) -> str:
        self._ensure_connected()
        self.streaming = False
        return self._record("streamoff")

    def read_frame(self, timeout: float) -> VideoFrame:
        self._ensure_connected()
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout < 0
        ):
            raise ValueError("timeout must be a non-negative finite number")
        if not self.streaming:
            raise DroneError("dry-run stream is not enabled")
        return VideoFrame(640, 480, self._timestamp())

    def emergency(self) -> str:
        if not self.emergency_triggered:
            self.emergency_triggered = True
            self._record("emergency")
        return "ok"
