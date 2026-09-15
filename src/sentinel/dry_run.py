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
        self._clock = clock if clock is not None else lambda: datetime.now(timezone.utc)
        self.network_opened = False
        self.connected = False
        self.streaming = False
        self.airborne = False
        self._emergency_triggered = False
        self._battery = 100
        self._height = 0
        self._speed = 10
        self._records: list[CommandRecord] = []

    @property
    def emergency_triggered(self) -> bool:
        return self._emergency_triggered

    @property
    def records(self) -> tuple[CommandRecord, ...]:
        """Return an immutable snapshot of the internal command history."""

        return tuple(self._records)

    def _ensure_available(self) -> None:
        if self._emergency_triggered:
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

    def _make_record(self, command: str, response: str = "ok") -> CommandRecord:
        return CommandRecord(
            command=command,
            response=response,
            position=None,
            timestamp=self._timestamp(),
        )

    def _append_record(self, record: CommandRecord) -> None:
        self._records.append(record)

    def _record(self, command: str, response: str = "ok") -> str:
        self._append_record(self._make_record(command, response))
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
        record = self._make_record("command")
        self.connected = True
        self._append_record(record)

    def close(self) -> None:
        """Close the simulated resources; safe even after emergency abort."""

        self.streaming = False
        self.connected = False

    def send(self, command: str) -> str:
        if isinstance(command, str) and command.strip() == "emergency":
            self._validate_command(command)
            return self.emergency()
        self._ensure_connected()
        self._validate_command(command)
        parts = command.strip().split()
        verb = parts[0]

        if verb == "command" and len(parts) == 1:
            return self._record(command)
        if verb == "takeoff" and len(parts) == 1:
            return self.takeoff()
        if verb == "land" and len(parts) == 1:
            return self.land()
        if verb in {"stop", "streamon", "streamoff"} and len(parts) == 1:
            return {
                "stop": self.stop,
                "streamon": self.stream_on,
                "streamoff": self.stream_off,
            }[verb]()
        if verb in _MOVEMENT_DIRECTIONS and len(parts) == 2:
            return self.move(verb, self._parse_raw_int(parts[1], "distance"))
        if verb in _ROTATION_DIRECTIONS and len(parts) == 2:
            return self.rotate(verb, self._parse_raw_int(parts[1], "degrees"))
        if verb == "speed" and len(parts) == 2:
            return self.set_speed(self._parse_raw_int(parts[1], "speed"))
        raise ValueError(f"unsupported raw command: {command}")

    @staticmethod
    def _parse_raw_int(value: str, name: str) -> int:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid raw command {name}: {value}") from exc

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
        record = self._make_record(f"speed {cm_per_second}")
        self._speed = cm_per_second
        self._append_record(record)
        return record.response

    def takeoff(self) -> str:
        self._ensure_connected()
        if self.airborne:
            raise DroneError("dry-run drone is already airborne")
        record = self._make_record("takeoff")
        self.airborne = True
        self._height = 100
        self._append_record(record)
        return record.response

    def land(self) -> str:
        self._ensure_connected()
        if not self.airborne:
            raise DroneError("dry-run drone is already grounded")
        record = self._make_record("land")
        self.airborne = False
        self._height = 0
        self._append_record(record)
        return record.response

    def move(self, direction: str, distance_cm: int) -> str:
        self._ensure_connected()
        if not self.airborne:
            raise DroneError("dry-run movement requires an airborne drone")
        if not isinstance(direction, str) or direction not in _MOVEMENT_DIRECTIONS:
            raise ValueError(f"unsupported movement direction: {direction}")
        self._validate_range(distance_cm, "distance", 20, 500)
        new_height = self._height
        new_airborne = self.airborne
        if direction == "up":
            new_height += distance_cm
        elif direction == "down":
            if distance_cm > new_height:
                raise DroneError("dry-run movement cannot go below ground")
            new_height -= distance_cm
            if new_height == 0:
                new_airborne = False
        record = self._make_record(f"{direction} {distance_cm}")
        self._height = new_height
        self.airborne = new_airborne
        self._append_record(record)
        return record.response

    def rotate(self, direction: str, degrees: int) -> str:
        self._ensure_connected()
        if not self.airborne:
            raise DroneError("dry-run rotation requires an airborne drone")
        if not isinstance(direction, str) or direction not in _ROTATION_DIRECTIONS:
            raise ValueError(f"unsupported rotation direction: {direction}")
        self._validate_range(degrees, "degrees", 1, 360)
        return self._record(f"{direction} {degrees}")

    def stop(self) -> str:
        self._ensure_connected()
        return self._record("stop")

    def stream_on(self) -> str:
        self._ensure_connected()
        record = self._make_record("streamon")
        self.streaming = True
        self._append_record(record)
        return record.response

    def stream_off(self) -> str:
        self._ensure_connected()
        record = self._make_record("streamoff")
        self.streaming = False
        self._append_record(record)
        return record.response

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
        if self._emergency_triggered:
            return "ok"
        record = self._make_record("emergency")
        self._emergency_triggered = True
        self._append_record(record)
        return record.response
