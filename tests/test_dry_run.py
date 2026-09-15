from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from sentinel.client import DroneError, EmergencyAborted
from sentinel.dry_run import DryRunClient
from sentinel.models import CommandRecord, VideoFrame


def test_dry_run_has_no_network_and_returns_deterministic_telemetry():
    client = DryRunClient()

    client.connect()

    assert client.battery() == 100
    assert client.height_cm() == 0
    assert client.speed_cm_per_second() == 10
    assert client.sdk_version() == "dry-run"
    assert client.network_opened is False
    assert client.records[0].command == "command"
    assert client.records[0].response == "ok"
    assert client.records[0].position is None
    assert client.records[0].timestamp.tzinfo == timezone.utc


@pytest.mark.parametrize(
    "operation",
    [
        lambda client: client.send("command"),
        lambda client: client.battery(),
        lambda client: client.height_cm(),
        lambda client: client.speed_cm_per_second(),
        lambda client: client.sdk_version(),
        lambda client: client.set_speed(20),
        lambda client: client.takeoff(),
        lambda client: client.land(),
        lambda client: client.move("forward", 20),
        lambda client: client.rotate("cw", 90),
        lambda client: client.stop(),
        lambda client: client.stream_on(),
        lambda client: client.stream_off(),
        lambda client: client.read_frame(0.1),
    ],
)
def test_operations_require_connection(operation):
    with pytest.raises(DroneError, match="not connected"):
        operation(DryRunClient())


def test_operations_after_close_fail_clearly():
    client = DryRunClient()
    client.connect()
    client.close()

    with pytest.raises(DroneError, match="not connected"):
        client.battery()
    with pytest.raises(DroneError, match="not connected"):
        client.send("command")


def test_emergency_is_terminal_even_for_later_connect():
    client = DryRunClient()

    assert client.emergency() == "ok"
    with pytest.raises(EmergencyAborted):
        client.connect()

    client.close()
    assert client.emergency() == "ok"


def test_dry_run_simulates_video_and_command_state():
    client = DryRunClient()
    client.connect()
    client.stream_on()
    frame = client.read_frame(timeout=0.1)
    client.stream_off()

    assert frame == VideoFrame(640, 480, frame.captured_at)
    assert client.streaming is False
    client.takeoff()
    client.move("up", 20)
    assert client.height_cm() == 120
    assert [record.command for record in client.records] == [
        "command",
        "streamon",
        "streamoff",
        "takeoff",
        "up 20",
    ]


def test_movement_requires_airborne_state():
    client = DryRunClient()
    client.connect()

    with pytest.raises(DroneError, match="airborne"):
        client.move("forward", 20)


def test_takeoff_and_land_enforce_physical_transitions():
    client = DryRunClient()
    client.connect()

    with pytest.raises(DroneError, match="grounded"):
        client.land()

    client.takeoff()
    assert client.airborne is True
    assert client.height_cm() == 100

    with pytest.raises(DroneError, match="already airborne"):
        client.takeoff()

    client.land()
    assert client.airborne is False
    assert client.height_cm() == 0

    with pytest.raises(DroneError, match="already grounded"):
        client.land()
    with pytest.raises(DroneError, match="airborne"):
        client.move("up", 20)


def test_down_movement_cannot_cross_below_ground():
    client = DryRunClient()
    client.connect()
    client.takeoff()

    with pytest.raises(DroneError, match="ground"):
        client.move("down", 120)

    assert client.height_cm() == 100
    assert client.airborne is True


def test_records_are_exposed_as_a_read_only_tuple():
    client = DryRunClient()
    client.connect()

    assert isinstance(client.records, tuple)
    with pytest.raises(AttributeError):
        client.records.append(client.records[0])
    with pytest.raises(AttributeError):
        client.records = ()


def test_models_added_for_the_client_boundary_are_frozen():
    frame = VideoFrame(640, 480, datetime.now(timezone.utc))
    assert isinstance(frame, VideoFrame)
    with pytest.raises(FrozenInstanceError):
        frame.width = 1280

    record = CommandRecord("stop", "ok", None, frame.captured_at)
    with pytest.raises(FrozenInstanceError):
        record.command = "land"


def test_dry_run_records_supported_commands_exactly():
    client = DryRunClient()
    client.connect()

    client.set_speed(20)
    client.takeoff()
    client.move("forward", 50)
    client.rotate("cw", 90)
    client.stop()
    client.land()

    assert [record.command for record in client.records] == [
        "command",
        "speed 20",
        "takeoff",
        "forward 50",
        "cw 90",
        "stop",
        "land",
    ]
    assert client.airborne is False
    assert client.height_cm() == 0


def test_dry_run_emergency_prevents_later_flight_commands():
    client = DryRunClient()
    client.connect()
    client.takeoff()
    assert client.emergency() == "ok"
    assert client.emergency() == "ok"

    with pytest.raises(EmergencyAborted):
        client.move("forward", 50)

    assert client.emergency_triggered is True
    assert [record.command for record in client.records] == [
        "command",
        "takeoff",
        "emergency",
    ]
    client.close()


def test_injected_clock_makes_records_and_frames_deterministic():
    timestamp = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    client = DryRunClient(clock=lambda: timestamp)
    client.connect()
    client.stream_on()

    frame = client.read_frame(0.1)

    assert frame.captured_at == timestamp
    assert all(record.timestamp == timestamp for record in client.records)


@pytest.mark.parametrize(
    "operation",
    [
        lambda client: client.battery(),
        lambda client: client.height_cm(),
        lambda client: client.speed_cm_per_second(),
        lambda client: client.sdk_version(),
        lambda client: client.set_speed(20),
        lambda client: client.takeoff(),
        lambda client: client.land(),
        lambda client: client.move("up", 20),
        lambda client: client.rotate("cw", 90),
        lambda client: client.stop(),
        lambda client: client.stream_on(),
        lambda client: client.stream_off(),
        lambda client: client.read_frame(0.1),
        lambda client: client.send("command"),
    ],
)
def test_emergency_aborts_later_flight_telemetry_and_stream_operations(operation):
    client = DryRunClient()
    client.connect()
    client.emergency()

    with pytest.raises(EmergencyAborted):
        operation(client)


@pytest.mark.parametrize("command", [None, "", "   ", "command\nland"])
def test_send_rejects_malformed_commands(command):
    client = DryRunClient()
    client.connect()
    with pytest.raises(ValueError, match="command"):
        client.send(command)


@pytest.mark.parametrize("direction", [None, "hover", "UP", ""])
def test_move_rejects_unsupported_directions(direction):
    client = DryRunClient()
    client.connect()
    client.takeoff()
    with pytest.raises(ValueError, match="direction"):
        client.move(direction, 20)


@pytest.mark.parametrize("distance_cm", [None, True, 0, 19, 501, 20.5, "20"])
def test_move_rejects_invalid_distances(distance_cm):
    client = DryRunClient()
    client.connect()
    client.takeoff()
    with pytest.raises(ValueError, match="distance"):
        client.move("up", distance_cm)


@pytest.mark.parametrize("direction", [None, "clockwise", "CW", ""])
def test_rotate_rejects_unsupported_directions(direction):
    client = DryRunClient()
    client.connect()
    with pytest.raises(ValueError, match="rotation direction"):
        client.rotate(direction, 90)


@pytest.mark.parametrize("degrees", [None, True, 0, 361, 90.5, "90"])
def test_rotate_rejects_invalid_degrees(degrees):
    client = DryRunClient()
    client.connect()
    with pytest.raises(ValueError, match="degrees"):
        client.rotate("cw", degrees)


@pytest.mark.parametrize("speed", [None, True, 9, 101, 20.5, "20"])
def test_set_speed_rejects_invalid_speeds(speed):
    client = DryRunClient()
    client.connect()
    with pytest.raises(ValueError, match="speed"):
        client.set_speed(speed)


def test_read_frame_requires_streaming_and_valid_timeout():
    client = DryRunClient()
    client.connect()

    with pytest.raises(DroneError, match="stream"):
        client.read_frame(0.1)

    client.stream_on()
    with pytest.raises(ValueError, match="timeout"):
        client.read_frame(-1)


@pytest.mark.parametrize(
    ("width", "height"),
    [(0, 480), (-1, 480), (640, 0), (640, -1), (True, 480), (640, 480.0)],
)
def test_video_frame_rejects_invalid_dimensions(width, height):
    with pytest.raises(ValueError, match="dimensions"):
        VideoFrame(width, height, datetime.now(timezone.utc))


@pytest.mark.parametrize("timestamp", [None, datetime(2026, 9, 15, 12, 0)])
def test_video_frame_rejects_naive_or_missing_timestamps(timestamp):
    with pytest.raises(ValueError, match="timezone-aware"):
        VideoFrame(640, 480, timestamp)


def test_command_record_rejects_invalid_timestamp_and_position():
    with pytest.raises(ValueError, match="timezone-aware"):
        CommandRecord("command", "ok", None, datetime(2026, 9, 15, 12, 0))
    with pytest.raises(ValueError, match="position"):
        CommandRecord("command", "ok", (0, 0, 0), datetime.now(timezone.utc))
