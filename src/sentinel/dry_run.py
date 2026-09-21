"""Deterministic, no-network implementation of :class:`DroneClient`."""

from __future__ import annotations

from pathlib import Path

from .client import EmergencyAborted
from .envelope import FlightEnvelope
from .models import CommandRecord, Direction, Telemetry, VideoFrame


class ClientStateError(RuntimeError):
    """Raised when an operation is used outside the client's lifecycle."""


EmergencyLatchedError = EmergencyAborted


class DryRunClient:
    """A deterministic client that never imports or creates a socket."""

    backend_name = "dry-run"

    def __init__(self) -> None:
        self._connected = False
        self._closed = False
        self._video_active = False
        self._emergency_latched = False
        self._records: list[CommandRecord] = []
        self._frame_number = 0
        self._envelope = FlightEnvelope()
        self._height = 0
        self._speed = 10
        self._video_record_path: Path | None = None

    @property
    def command_history(self) -> tuple[CommandRecord, ...]:
        return tuple(self._records)

    @property
    def commands(self) -> tuple[str, ...]:
        """Return compact command strings for assertions and simple reports."""

        return tuple(record.display for record in self._records)

    @property
    def emergency_latched(self) -> bool:
        return self._emergency_latched

    def connect(self) -> None:
        # Mirrors djitellopy_backend.DjiTelloPyClient.connect(), which
        # genuinely reconnects and resets ``_closed`` -- a caller that
        # flies more than once in a session (survey.cube_bridge.CubeFlight,
        # up to two approved sweeps) closes and reconnects the same client
        # object between flights, and the dry-run double must accept that
        # too, not diverge into a stricter one-shot contract.
        self._closed = False
        if self._connected:
            return
        self._connected = True
        self._record("command")

    def close(self) -> None:
        self._video_active = False
        self._closed = True
        self._connected = False

    def enter_command_mode(self) -> None:
        self._ensure_ready()
        if not self._records or self._records[-1].command != "command":
            self._send("command")

    def telemetry(self) -> Telemetry:
        self._send("telemetry")
        return Telemetry(
            battery_percent=100,
            height_cm=200,
            flight_time_s=0,
            temperature_c=22,
        )

    def battery(self) -> int:
        self._ensure_ready()
        return 100

    def height_cm(self) -> int:
        self._ensure_ready()
        return self._height

    def speed_cm_per_second(self) -> int:
        self._ensure_ready()
        return self._speed

    def sdk_version(self) -> str:
        self._ensure_ready()
        return "dry-run"

    def set_speed(self, cm_per_second: int) -> str:
        if isinstance(cm_per_second, bool) or not isinstance(cm_per_second, int):
            raise ValueError("speed must be an integer number of centimetres per second")
        if not 10 <= cm_per_second <= 100:
            raise ValueError("speed must be between 10 and 100 cm/s")
        response = self._send("speed", cm_per_second)
        self._speed = cm_per_second
        return response

    def set_video_record_path(self, path: str | Path) -> None:
        if self._video_active:
            raise ClientStateError("video record path cannot change while streaming")
        self._video_record_path = Path(path)

    def start_video(self) -> None:
        self.stream_on()

    def stream_on(self) -> str:
        response = self._send("streamon")
        self._video_active = True
        return response

    def read_frame(self, timeout: float | None = None) -> VideoFrame:
        self._ensure_ready()
        if not self._video_active:
            raise ClientStateError("video stream is not active")
        self._frame_number += 1
        self._record("video frame")
        return VideoFrame(
            width=960,
            height=720,
            pixel_format="rgb24",
            frame_number=self._frame_number,
            timestamp_ms=0,
            data=b"synthetic-rgb-frame",
        )

    def stop_video(self) -> None:
        self.stream_off()

    def stream_off(self) -> str:
        response = self._send("streamoff")
        self._video_active = False
        return response

    def takeoff(self) -> str:
        response = self._send("takeoff")
        self._height = 100
        return response

    def move(self, direction: Direction | str, distance_cm: int) -> str:
        direction = Direction(direction)
        self._envelope.validate_distance(distance_cm)
        response = self._send(direction.value, distance_cm)
        if direction is Direction.UP:
            self._height += distance_cm
        elif direction is Direction.DOWN:
            self._height -= distance_cm
        return response

    def rotate(
        self,
        direction_or_degrees: Direction | str | int,
        degrees: int | None = None,
        *,
        clockwise: bool | None = None,
    ) -> str:
        if degrees is None:
            if clockwise is None:
                raise TypeError("clockwise is required for a numeric rotation")
            degrees = direction_or_degrees  # type: ignore[assignment]
            direction = "cw" if clockwise else "ccw"
        else:
            direction = str(direction_or_degrees)
        if isinstance(degrees, bool) or not isinstance(degrees, int):
            raise ValueError("rotation must be an integer number of degrees")
        if not 1 <= degrees <= 360:
            raise ValueError("rotation must be between 1 and 360 degrees")
        if direction not in {"cw", "ccw"}:
            raise ValueError("rotation direction must be 'cw' or 'ccw'")
        return self._send(direction, degrees)

    def stop(self) -> str:
        return self._send("stop")

    def land(self) -> str:
        response = self._send("land")
        self._height = 0
        return response

    def emergency(self) -> str:
        self._ensure_ready()
        if self._emergency_latched:
            raise EmergencyLatchedError("emergency state is already latched")
        self._record("emergency")
        self._emergency_latched = True
        return "ok"

    def send(self, command: str) -> str:
        if command == "emergency":
            return self.emergency()
        return self._send(command)

    def _ensure_ready(self) -> None:
        if self._closed:
            raise ClientStateError("client is closed")
        if not self._connected:
            raise ClientStateError("client is not connected")

    def _send(self, command: str, *arguments: int | str) -> str:
        self._ensure_ready()
        if self._emergency_latched:
            raise EmergencyLatchedError(
                "emergency state is terminal; normal commands are disabled"
            )
        self._record(command, *arguments)
        return "ok"

    def _record(self, command: str, *arguments: int | str) -> None:
        command_text = command
        if arguments:
            command_text = f"{command} {' '.join(str(value) for value in arguments)}"
        self._records.append(
            CommandRecord(
                sequence=len(self._records) + 1,
                command=command_text,
            )
        )
