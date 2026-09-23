from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from sentinel.client import EmergencyAborted
from sentinel.envelope import EnvelopeViolation
from sentinel.models import CommandRecord, Position, VideoFrame
from sentinel.network import RouteSnapshot
from sentinel.runner import FlightTestRunner


@dataclass
class _FakeClient:
    """Small protocol-faithful client used by runner tests."""

    battery_level: int = 100
    height: int = 0
    speed: int = 10
    fail_command: str | None = None
    vertical_drift: int = 0
    takeoff_error: bool = False

    def __post_init__(self) -> None:
        self.records: list[CommandRecord] = []
        self.closed = False
        self.airborne = False
        self.emergency_triggered = False
        self.height_queries: list[int] = []
        self.after_record = None

    def _record(self, command: str, response: str = "ok") -> str:
        self.records.append(
            CommandRecord(
                command=command,
                response=response,
                position=None,
                timestamp=datetime.now(timezone.utc),
            )
        )
        callback = self.after_record
        if callback is not None:
            self.after_record = None
            callback(command)
        return response

    def connect(self) -> None:
        self._record("command")

    def close(self) -> None:
        self.closed = True

    def send(self, command: str) -> str:
        return self._record(command)

    def battery(self) -> int:
        return self.battery_level

    def height_cm(self) -> int:
        self.height_queries.append(self.height)
        return self.height

    def speed_cm_per_second(self) -> int:
        return self.speed

    def sdk_version(self) -> str:
        return "fake-sdk"

    def set_speed(self, cm_per_second: int) -> str:
        self.speed = cm_per_second
        return self._record(f"speed {cm_per_second}")

    def takeoff(self) -> str:
        self.airborne = True
        self.height = 100
        response = self._record("takeoff")
        if self.takeoff_error:
            raise RuntimeError("takeoff acknowledgement timed out")
        return response

    def land(self) -> str:
        self.airborne = False
        self.height = 0
        return self._record("land")

    def move(self, direction: str, distance_cm: int) -> str:
        response = "error" if self.fail_command == f"{direction} {distance_cm}" else "ok"
        if response == "ok":
            if direction == "up":
                self.height += distance_cm + self.vertical_drift
            elif direction == "down":
                self.height -= distance_cm + self.vertical_drift
        return self._record(f"{direction} {distance_cm}", response)

    def rotate(self, direction: str, degrees: int) -> str:
        return self._record(f"{direction} {degrees}")

    def stop(self) -> str:
        return self._record("stop")

    def stream_on(self) -> str:
        return self._record("streamon")

    def stream_off(self) -> str:
        return self._record("streamoff")

    def read_frame(self, timeout: float) -> VideoFrame:
        return VideoFrame(640, 480, datetime.now(timezone.utc))

    def emergency(self) -> str:
        self.emergency_triggered = True
        return self._record("emergency")


def test_runner_executes_approved_sequence_and_enters_altitude_band():
    client = _FakeClient()

    report = FlightTestRunner(client, mode="dry-run").run(confirm=True)

    assert report.status == "PASS"
    assert [record.command for record in client.records] == [
        "command",
        "speed 20",
        "streamon",
        "streamoff",
        "takeoff",
        "up 20",
        "up 20",
        "up 20",
        "forward 50",
        "right 50",
        "back 50",
        "left 50",
        "up 50",
        "down 50",
        "cw 90",
        "ccw 90",
        "stop",
        "land",
    ]
    assert 160 in client.height_queries
    assert report.video_frame is not None
    assert (report.video_frame.width, report.video_frame.height) == (640, 480)
    assert [record.command for record in report.commands] == [
        record.command for record in client.records
    ]
    assert client.closed is True


def test_runner_emits_progressive_preflight_and_flight_events():
    client = _FakeClient()
    events: list[tuple[str, str]] = []

    report = FlightTestRunner(
        client,
        mode="real",
        progress=lambda event, message: events.append((event, message)),
    ).run(confirm=True)

    assert report.status == "PASS"
    assert events[0] == ("run_started", "starting preflight")
    assert ("preflight", "connecting to Tello") in events
    assert ("preflight", "validating battery telemetry") in events
    assert ("preflight", "validating decoded video frame") in events
    assert ("flight", "sending takeoff") in events
    assert ("flight", "sending landing") in events
    assert events[-1] == ("run_complete", "flight sequence passed")


def test_runner_does_not_probe_unsupported_sdk_version_command():
    class Sdk13Client(_FakeClient):
        def sdk_version(self) -> str:
            raise AssertionError("SDK 1.3 has no sdk? command")

    report = FlightTestRunner(Sdk13Client(), mode="dry-run").run(confirm=True)

    assert report.status == "PASS"


def test_runner_can_retain_video_stream_for_recording():
    client = _FakeClient()

    report = FlightTestRunner(
        client,
        mode="dry-run",
        retain_video_stream=True,
    ).run(confirm=True)

    assert report.status == "PASS"
    assert "streamoff" not in [record.command for record in client.records]
    assert client.closed is True


def test_runner_video_only_does_not_send_flight_commands():
    client = _FakeClient(battery_level=20)

    report = FlightTestRunner(
        client,
        mode="real",
        retain_video_stream=True,
        video_only=True,
        video_only_duration_s=0,
    ).run(confirm=True)

    assert report.status == "PASS"
    commands = [record.command for record in client.records]
    assert "takeoff" not in commands
    assert "land" not in commands
    assert not any(
        command in {
            "forward",
            "right",
            "back",
            "left",
            "up",
            "down",
            "cw",
            "ccw",
            "stop",
        }
        for command in commands
    )
    assert client.closed is True


def test_runner_prevalidates_envelope_before_transmitting_movement():
    client = _FakeClient()
    runner = FlightTestRunner(client, mode="dry-run")
    runner.envelope.position = Position(50, 0, 150)

    with pytest.raises(EnvelopeViolation):
        runner.move_checked("right", 20)

    assert [record.command for record in client.records] == []


def test_low_battery_fails_preflight_before_takeoff():
    client = _FakeClient(battery_level=39)

    report = FlightTestRunner(client, mode="dry-run").run(confirm=True)

    assert report.status == "FAIL"
    assert "takeoff" not in [record.command for record in client.records]
    assert client.closed is True


def test_connectivity_failure_fails_hard_before_takeoff():
    client = _FakeClient()

    def fail_connect() -> None:
        raise TimeoutError("Tello did not acknowledge command mode")

    client.connect = fail_connect  # type: ignore[method-assign]

    report = FlightTestRunner(client, mode="real").run(confirm=True)

    assert report.status == "FAIL"
    assert report.error == "Tello did not acknowledge command mode"
    assert [record.command for record in client.records] == []
    assert client.closed is True


def test_recoverable_failure_attempts_normal_landing():
    client = _FakeClient(fail_command="forward 50")

    report = FlightTestRunner(client, mode="dry-run").run(confirm=True)

    commands = [record.command for record in client.records]
    assert report.status == "FAIL"
    assert "land" in commands
    assert commands.index("land") > commands.index("takeoff")
    assert "right 50" not in commands
    assert client.closed is True


def test_emergency_is_terminal_and_never_lands_or_sends_later_flight_commands():
    client = _FakeClient()
    runner = FlightTestRunner(client, mode="dry-run")

    def trigger_on_takeoff(command: str) -> None:
        if command == "takeoff":
            runner.trigger_emergency()
        else:
            # The fake consumes a callback after each record; keep the hook
            # armed while preflight records are being emitted.
            client.after_record = trigger_on_takeoff

    client.after_record = trigger_on_takeoff

    report = runner.run(confirm=True)

    commands = [record.command for record in client.records]
    assert report.status == "EMERGENCY_ABORTED"
    takeoff_index = commands.index("takeoff")
    assert commands[takeoff_index : takeoff_index + 2] == ["takeoff", "emergency"]
    assert "land" not in commands
    assert not any(command in {"forward 50", "right 50", "up 50"} for command in commands)
    assert client.closed is True


def test_emergency_send_failure_is_retained_in_terminal_report():
    client = _FakeClient()

    def failed_emergency() -> str:
        client.emergency_triggered = True
        client._record("emergency", "error: socket send failed")
        raise OSError("socket send failed")

    client.emergency = failed_emergency  # type: ignore[method-assign]
    runner = FlightTestRunner(client, mode="real")
    runner.trigger_emergency()

    report = runner.run(confirm=True)

    assert report.status == "EMERGENCY_ABORTED"
    assert report.error is not None
    assert "emergency send failed: socket send failed" in report.error


def test_post_emergency_runner_calls_are_rejected_without_backend_transmission():
    client = _FakeClient()
    runner = FlightTestRunner(client, mode="dry-run")
    runner.trigger_emergency()

    with pytest.raises(EmergencyAborted):
        runner.move_checked("forward", 50)

    assert [record.command for record in client.records] == ["emergency"]


def test_runner_checks_measured_height_after_vertical_moves():
    client = _FakeClient(vertical_drift=50)

    report = FlightTestRunner(client, mode="dry-run").run(confirm=True)

    commands = [record.command for record in client.records]
    assert report.status == "FAIL"
    assert "up 50" in commands
    assert "land" in commands


def test_takeoff_failure_is_treated_as_airborne_for_recovery():
    client = _FakeClient(takeoff_error=True)

    report = FlightTestRunner(client, mode="dry-run").run(confirm=True)

    assert report.status == "FAIL"
    assert [record.command for record in client.records][-2:] == [
        "takeoff",
        "land",
    ]
    assert [record.command for record in report.commands][-2:] == [
        "takeoff",
        "land",
    ]


def test_runner_rechecks_default_route_before_transmitting():
    client = _FakeClient()
    before = RouteSnapshot("192.168.1.44", 7)
    changed = RouteSnapshot("192.168.1.99", 8)

    report = FlightTestRunner(
        client,
        mode="real",
        route_before=before,
        route_snapshot_provider=lambda: changed,
    ).run(confirm=True)

    assert report.status == "FAIL"
    assert [record.command for record in client.records] == []


def test_envelope_is_injectable():
    from sentinel.envelope import FlightEnvelope

    envelope = FlightEnvelope(min_z_cm=80, max_z_cm=180)
    runner = FlightTestRunner(_FakeClient(), "dry-run", envelope=envelope)
    assert runner.envelope is envelope


def test_fly_sequence_is_the_override_point():
    calls = []

    class _Custom(FlightTestRunner):
        def _fly_sequence(self):
            calls.append("custom")

    client = _FakeClient()
    report = _Custom(client, "dry-run").run(confirm=True)
    assert calls == ["custom"] and report.status == "PASS"
    commands = [r.command for r in client.records]
    assert "forward 50" not in commands and "land" in commands
