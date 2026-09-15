from __future__ import annotations

import socket

import pytest

from sentinel.client import DroneClient
from sentinel.dry_run import DryRunClient, EmergencyLatchedError


def test_dry_run_is_a_drone_client_and_records_deterministic_setup_sequence() -> None:
    client = DryRunClient()

    assert isinstance(client, DroneClient)

    client.connect()
    client.enter_command_mode()
    telemetry = client.telemetry()
    client.set_speed(20)
    assert client.speed_cm_per_second() == 20
    client.start_video()
    frame = client.read_frame()
    client.stop_video()

    assert telemetry.battery_percent == 100
    assert telemetry.height_cm == 200
    assert frame.width == 960
    assert frame.height == 720
    assert frame.frame_number == 1
    assert client.commands == (
        "command",
        "telemetry",
        "speed 20",
        "streamon",
        "video frame",
        "streamoff",
    )


def test_dry_run_never_constructs_a_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_socket_is_constructed(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry-run must not construct a socket")

    monkeypatch.setattr(socket, "socket", fail_if_socket_is_constructed)

    client = DryRunClient()
    client.connect()
    client.enter_command_mode()
    client.telemetry()
    client.start_video()
    client.read_frame()
    client.stop_video()
    client.close()


def test_emergency_is_terminal_and_rejects_later_flight_commands() -> None:
    client = DryRunClient()
    client.connect()
    client.takeoff()
    client.emergency()

    assert client.emergency_latched
    assert client.commands == ("command", "takeoff", "emergency")

    with pytest.raises(EmergencyLatchedError):
        client.land()

    with pytest.raises(EmergencyLatchedError):
        client.move("forward", 20)

    assert client.commands == ("command", "takeoff", "emergency")
