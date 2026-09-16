from __future__ import annotations

from pathlib import Path

import pytest

from sentinel.models import VideoFrame
from sentinel.safe_tools import FixedPositionYawTools, ToolRefusal, YawCommand


class FakeClient:
    backend_name = "fake"

    def __init__(self, *, battery: int = 80, height: int = 0) -> None:
        self.battery_level = battery
        self.height_level = height
        self.commands: list[str] = []
        self.record_path: Path | None = None
        self.closed = False
        self.emergency_latched = False

    def connect(self) -> None:
        self.commands.append("command")

    def close(self) -> None:
        self.closed = True

    def battery(self) -> int:
        return self.battery_level

    def height_cm(self) -> int:
        return self.height_level

    def set_speed(self, speed: int) -> str:
        self.commands.append(f"speed {speed}")
        return "ok"

    def speed_cm_per_second(self) -> int:
        return 20

    def set_video_record_path(self, path: Path) -> None:
        self.record_path = path

    def stream_on(self) -> str:
        self.commands.append("streamon")
        return "ok"

    def read_frame(self, timeout: float) -> VideoFrame:
        return VideoFrame(width=960, height=720)

    def stream_off(self) -> str:
        self.commands.append("streamoff")
        return "ok"

    def takeoff(self) -> str:
        self.commands.append("takeoff")
        self.height_level = 100
        return "ok"

    def rotate(self, direction: str, degrees: int) -> str:
        self.commands.append(f"{direction} {degrees}")
        return "ok"

    def land(self) -> str:
        self.commands.append("land")
        self.height_level = 0
        return "ok"

    def emergency(self) -> str:
        self.commands.append("emergency")
        self.emergency_latched = True
        return "sent 9 bytes"


def make_tools(client: FakeClient) -> FixedPositionYawTools:
    return FixedPositionYawTools(
        client,
        record_path=Path("fixtures/perception/live/test.h264"),
        sleep_fn=lambda _seconds: None,
    )


def test_translation_and_tilt_tools_refuse_without_transmission() -> None:
    client = FakeClient()
    tools = make_tools(client)

    with pytest.raises(ToolRefusal, match="translation"):
        tools.tilt_front(20)
    with pytest.raises(ToolRefusal, match="translation"):
        tools.tilt_left(20)
    with pytest.raises(ToolRefusal, match="translation"):
        tools.tilt_right(20)
    with pytest.raises(ToolRefusal, match="translation"):
        tools.tilt_back(20)

    assert client.commands == []


def test_yaw_scan_allow_list_has_no_translation_commands() -> None:
    client = FakeClient()
    tools = make_tools(client)

    tools.prepare()
    tools.takeoff()
    tools.record_scene(1.0)
    for _ in range(4):
        tools.rotate_cw(90)
    tools.land()

    assert client.commands == [
        "command",
        "speed 20",
        "takeoff",
        "streamon",
        "cw 90",
        "cw 90",
        "cw 90",
        "cw 90",
        "streamoff",
        "land",
    ]
    assert client.emergency_latched is False


def test_yaw_format_and_total_budget_are_rejected() -> None:
    client = FakeClient()
    tools = make_tools(client)
    tools.prepare()
    tools.takeoff()

    with pytest.raises(ToolRefusal, match="integer"):
        tools.rotate_cw("90")  # type: ignore[arg-type]
    with pytest.raises(ToolRefusal, match="90"):
        tools.rotate_cw(91)

    for _ in range(4):
        tools.rotate_cw(90)
    with pytest.raises(ToolRefusal, match="total yaw"):
        tools.rotate_cw(1)


def test_low_battery_refuses_before_takeoff() -> None:
    client = FakeClient(battery=39)
    tools = make_tools(client)

    with pytest.raises(ToolRefusal, match="battery"):
        tools.prepare()
    with pytest.raises(ToolRefusal, match="prepared"):
        tools.takeoff()

    assert client.commands == ["command"]


def test_airborne_yaw_failure_sends_emergency() -> None:
    client = FakeClient()

    def fail_rotate(direction: str, degrees: int) -> str:
        client.commands.append(f"{direction} {degrees}")
        raise OSError("rotate transport failed")

    client.rotate = fail_rotate  # type: ignore[method-assign]
    tools = make_tools(client)
    tools.prepare()
    tools.takeoff()

    with pytest.raises(OSError, match="rotate transport failed"):
        tools.rotate_cw(90)

    assert client.commands[-2:] == ["cw 90", "emergency"]
    assert client.emergency_latched is True
