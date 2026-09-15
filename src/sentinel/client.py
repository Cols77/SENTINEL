"""The backend protocol shared by dry-run, DJITelloPy, and the runner."""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from .models import CommandRecord, Direction, Telemetry, VideoFrame


class EmergencyAborted(RuntimeError):
    """Raised when a normal command follows a terminal emergency command."""


@runtime_checkable
class DroneClient(Protocol):
    """Stable interface consumed by SENTINEL's safety runner.

    Implementations own transport details.  The runner only uses these
    explicit operations and never reaches into a backend's socket or SDK
    state.
    """

    backend_name: str

    @property
    def command_history(self) -> Sequence[CommandRecord]:
        """Return immutable audit records in transmission order."""

    @property
    def emergency_latched(self) -> bool:
        """Whether the terminal emergency state has been entered."""

    def connect(self) -> None: ...

    def send(self, command: str) -> str: ...

    def enter_command_mode(self) -> None: ...

    def telemetry(self) -> Telemetry: ...

    def battery(self) -> int: ...

    def height_cm(self) -> int: ...

    def speed_cm_per_second(self) -> int: ...

    def sdk_version(self) -> str: ...

    def set_speed(self, cm_per_second: int) -> str: ...

    def start_video(self) -> None: ...

    def stream_on(self) -> str: ...

    def read_frame(self, timeout: float | None = None) -> VideoFrame: ...

    def stop_video(self) -> None: ...

    def stream_off(self) -> str: ...

    def takeoff(self) -> str: ...

    def move(self, direction: Direction | str, distance_cm: int) -> str: ...

    def rotate(self, direction: str, degrees: int) -> str: ...

    def stop(self) -> str: ...

    def land(self) -> str: ...

    def emergency(self) -> str: ...

    def close(self) -> None: ...
