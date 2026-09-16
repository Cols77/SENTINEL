"""Fail-closed allow-list tools for a stationary Tello yaw scan.

This module deliberately does not expose translational flight commands.  The
existing low-level client still implements the complete SDK contract for the
legacy runner, but callers that use this controller can only prepare, take off,
record, rotate about Z within a bounded budget, land, or emergency-stop.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from pathlib import Path
from time import sleep
from typing import Callable, NoReturn, Protocol, Sequence

from .models import VideoFrame


class ToolRefusal(ValueError):
    """Raised when a safe tool rejects malformed or unsafe input/state."""


class ToolState(str, Enum):
    NEW = "new"
    PREPARED = "prepared"
    AIRBORNE = "airborne"
    GROUNDED = "grounded"
    EMERGENCY = "emergency"
    FAILED = "failed"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class YawCommand:
    """One bounded rotation permitted by the stationary-scan policy."""

    direction: str
    degrees: int

    def __post_init__(self) -> None:
        if type(self.direction) is not str or self.direction not in {"cw", "ccw"}:
            raise ToolRefusal("yaw direction must be exactly 'cw' or 'ccw'")
        if type(self.degrees) is not int:
            raise ToolRefusal("yaw degrees must be an integer")
        if not 1 <= self.degrees <= 90:
            raise ToolRefusal("one yaw command is limited to 1..90 degrees")


class SafeYawClient(Protocol):
    """Small capability boundary required by :class:`FixedPositionYawTools`."""

    backend_name: str

    def connect(self) -> None: ...

    def close(self) -> None: ...

    def battery(self) -> int: ...

    def height_cm(self) -> int: ...

    def set_speed(self, cm_per_second: int) -> str: ...

    def speed_cm_per_second(self) -> int: ...

    def set_video_record_path(self, path: Path) -> None: ...

    def stream_on(self) -> str: ...

    def stream_off(self) -> str: ...

    def read_frame(self, timeout: float | None = None) -> VideoFrame: ...

    def takeoff(self) -> str: ...

    def rotate(self, direction: str, degrees: int) -> str: ...

    def land(self) -> str: ...

    def emergency(self) -> str: ...


class FixedPositionYawTools:
    """Allow-list controller for a no-translation, yaw-only scene scan.

    The controller guarantees that it never calls the client's ``move`` method,
    and it rejects all four directional tilt tools.  It cannot physically
    guarantee an exact world-frame ``(x, y, z)`` position: Tello has no position
    hold in this interface and wind/estimation drift can occur.  Its guarantee
    is command-level: after takeoff it sends no translational or altitude-change
    command, only bounded Z-axis rotations.
    """

    MIN_BATTERY_PERCENT = 40
    GROUND_HEIGHT_MAX_CM = 20
    MAX_YAW_PER_COMMAND = 90
    MAX_TOTAL_YAW = 360
    MAX_RECORD_SECONDS = 120.0
    FRAME_TIMEOUT_SECONDS = 10.0

    def __init__(
        self,
        client: SafeYawClient,
        *,
        record_path: str | Path,
        sleep_fn: Callable[[float], None] = sleep,
    ) -> None:
        self.client = client
        self.record_path = self._validate_record_path(record_path)
        self.sleep_fn = sleep_fn
        self.state = ToolState.NEW
        self.total_yaw_degrees = 0
        self._streaming = False

    @staticmethod
    def _validate_record_path(value: str | Path) -> Path:
        if not isinstance(value, (str, Path)) or not str(value).strip():
            raise ToolRefusal("record_path must be a non-empty path")
        path = Path(value)
        if path.suffix.lower() != ".h264":
            raise ToolRefusal("record_path must end with .h264")
        if any(part in {"", ".", ".."} for part in path.parts):
            raise ToolRefusal("record_path contains an unsafe path component")
        return path

    @staticmethod
    def _validate_record_seconds(value: object, maximum: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ToolRefusal("record duration must be a finite number")
        seconds = float(value)
        if not isfinite(seconds) or seconds <= 0 or seconds > maximum:
            raise ToolRefusal(f"record duration must be >0 and <= {maximum:g} seconds")
        return seconds

    @staticmethod
    def _require_ok(response: object, operation: str) -> None:
        if str(response).strip().lower() != "ok":
            raise ToolRefusal(f"{operation} was not acknowledged: {response!r}")

    def _require_state(self, *allowed: ToolState) -> None:
        if self.state not in allowed:
            names = ", ".join(state.value for state in allowed)
            raise ToolRefusal(
                f"tool state is {self.state.value!r}; required one of: {names}"
            )

    def _require_battery(self) -> None:
        battery = self.client.battery()
        if type(battery) is not int or not 0 <= battery <= 100:
            raise ToolRefusal(f"battery telemetry is invalid: {battery!r}")
        if battery < self.MIN_BATTERY_PERCENT:
            raise ToolRefusal(
                f"battery is {battery}%; takeoff requires at least "
                f"{self.MIN_BATTERY_PERCENT}%"
            )

    def _latch_emergency(self, original: BaseException) -> None:
        self.state = ToolState.EMERGENCY
        try:
            self.client.emergency()
        except Exception as emergency_error:
            raise ToolRefusal(
                f"operation failed ({original}); emergency transmission also failed "
                f"({emergency_error})"
            ) from emergency_error

    def prepare(self) -> None:
        """Connect and prove grounded, charged, speed-controlled readiness."""

        self._require_state(ToolState.NEW)
        # Configure recording before any stream command.  The backend rejects
        # changing this while streaming, so a path cannot silently be swapped.
        self.client.set_video_record_path(self.record_path)
        self.client.connect()
        try:
            self._require_battery()
            height = self.client.height_cm()
            if type(height) is not int or not 0 <= height <= self.GROUND_HEIGHT_MAX_CM:
                raise ToolRefusal(
                    f"takeoff requires grounded height <= {self.GROUND_HEIGHT_MAX_CM} cm; "
                    f"telemetry was {height!r}"
                )
            self._require_ok(self.client.set_speed(20), "speed configuration")
            if self.client.speed_cm_per_second() != 20:
                raise ToolRefusal("speed readback did not confirm 20 cm/s")
        except Exception:
            self.state = ToolState.FAILED
            self.client.close()
            raise
        self.state = ToolState.PREPARED

    def takeoff(self) -> None:
        """Take off only after :meth:`prepare` has passed all gates."""

        self._require_state(ToolState.PREPARED)
        try:
            self._require_battery()
            self._require_ok(self.client.takeoff(), "takeoff")
            height = self.client.height_cm()
            if type(height) is not int or height < 0:
                raise ToolRefusal(f"post-takeoff height telemetry is invalid: {height!r}")
        except Exception as exc:
            self._latch_emergency(exc)
            raise
        self.state = ToolState.AIRBORNE

    def record_scene(self, duration_seconds: object) -> VideoFrame:
        """Record a bounded scene interval while the aircraft remains airborne."""

        self._require_state(ToolState.AIRBORNE)
        seconds = self._validate_record_seconds(duration_seconds, self.MAX_RECORD_SECONDS)
        try:
            self._require_battery()
            if not self._streaming:
                self._require_ok(self.client.stream_on(), "video stream start")
                self._streaming = True
            frame = self.client.read_frame(timeout=self.FRAME_TIMEOUT_SECONDS)
            if not isinstance(frame, VideoFrame):
                raise ToolRefusal("video backend returned an invalid frame")
            # VideoReceiver records in its own receive/decode path.  Holding the
            # tool here preserves the requested scene interval without issuing
            # any flight command.
            self.sleep_fn(seconds)
            return frame
        except Exception as exc:
            self._latch_emergency(exc)
            raise

    def _rotate(self, command: YawCommand) -> None:
        self._require_state(ToolState.AIRBORNE)
        if self.total_yaw_degrees + command.degrees > self.MAX_TOTAL_YAW:
            raise ToolRefusal(
                f"total yaw budget is {self.MAX_TOTAL_YAW} degrees; "
                f"requested {self.total_yaw_degrees + command.degrees}"
            )
        try:
            self._require_battery()
            self._require_ok(
                self.client.rotate(command.direction, command.degrees),
                f"{command.direction} {command.degrees}",
            )
        except Exception as exc:
            self._latch_emergency(exc)
            raise
        self.total_yaw_degrees += command.degrees

    def rotate_cw(self, degrees: object) -> None:
        """Rotate clockwise by at most 90 degrees; never translate."""

        self._rotate(YawCommand("cw", self._validate_integer(degrees, "yaw degrees")))

    def rotate_ccw(self, degrees: object) -> None:
        """Rotate counter-clockwise by at most 90 degrees; never translate."""

        self._rotate(YawCommand("ccw", self._validate_integer(degrees, "yaw degrees")))

    @staticmethod
    def _validate_integer(value: object, name: str) -> int:
        if type(value) is not int:
            raise ToolRefusal(f"{name} must be an integer")
        return value

    def _reject_translation(self, direction: str, _distance_cm: object) -> NoReturn:
        raise ToolRefusal(
            f"translation is disabled: tilt_{direction} would change position; "
            "the fixed-position scan allow-list permits Z rotation only"
        )

    def tilt_front(self, distance_cm: object) -> NoReturn:
        self._reject_translation("front", distance_cm)

    def tilt_left(self, distance_cm: object) -> NoReturn:
        self._reject_translation("left", distance_cm)

    def tilt_right(self, distance_cm: object) -> NoReturn:
        self._reject_translation("right", distance_cm)

    def tilt_back(self, distance_cm: object) -> NoReturn:
        self._reject_translation("back", distance_cm)

    def land(self) -> None:
        """Stop recording first, then land; emergency-stop on any failure."""

        self._require_state(ToolState.AIRBORNE)
        try:
            if self._streaming:
                self._require_ok(self.client.stream_off(), "video stream stop")
                self._streaming = False
            self._require_ok(self.client.land(), "landing")
        except Exception as exc:
            self._latch_emergency(exc)
            raise
        self.state = ToolState.GROUNDED

    def emergency_stop(self) -> str:
        """Latch the terminal emergency path; no normal command may follow."""

        if self.state is ToolState.CLOSED:
            raise ToolRefusal("controller is closed")
        self.state = ToolState.EMERGENCY
        return self.client.emergency()

    def close(self) -> None:
        """Close only after landing; airborne close escalates to emergency."""

        if self.state is ToolState.AIRBORNE:
            self.emergency_stop()
        if self.state is not ToolState.CLOSED:
            self.client.close()
            self.state = ToolState.CLOSED

    def scan(self, commands: Sequence[YawCommand], dwell_seconds: object) -> tuple[VideoFrame, ...]:
        """Run one bounded takeoff/record/yaw/land sequence.

        All command and budget validation happens before ``prepare`` so an
        invalid scan cannot reach the network or takeoff path.
        """

        if isinstance(commands, (str, bytes)):
            raise ToolRefusal("scan commands must be a sequence of YawCommand values")
        sequence = tuple(commands)
        if not sequence:
            raise ToolRefusal("scan requires at least one yaw command")
        if any(not isinstance(command, YawCommand) for command in sequence):
            raise ToolRefusal("scan commands must be YawCommand values")
        if sum(command.degrees for command in sequence) > self.MAX_TOTAL_YAW:
            raise ToolRefusal("scan exceeds the total yaw budget of 360 degrees")
        seconds = self._validate_record_seconds(dwell_seconds, self.MAX_RECORD_SECONDS)

        frames: list[VideoFrame] = []
        try:
            self.prepare()
            self.takeoff()
            frames.append(self.record_scene(seconds))
            for command in sequence:
                self._rotate(command)
                frames.append(self.record_scene(seconds))
            self.land()
            return tuple(frames)
        except Exception:
            if self.state is ToolState.AIRBORNE:
                self.emergency_stop()
            raise
        finally:
            if self.state is not ToolState.CLOSED:
                self.client.close()
                self.state = ToolState.CLOSED
