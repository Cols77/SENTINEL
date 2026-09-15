import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sentinel.cli import main
from sentinel.emergency import EmergencySender
from sentinel.models import CommandRecord, RunReport, VideoFrame
from sentinel.reporting import write_report


def test_dry_run_cli_returns_zero_and_writes_jsonl(monkeypatch, tmp_path):
    output = tmp_path / "run.jsonl"
    calls = []

    @dataclass
    class FakeReport:
        status: str = "PASS"
        mode: str = "dry-run"
        commands: tuple = ()
        video_frame: object | None = None
        adapter: object | None = None
        error: str | None = None

    class FakeRunner:
        def __init__(self, client, mode, minimum_battery=40):
            calls.append((client, mode, minimum_battery))

        def run(self, *, confirm):
            assert confirm is True
            return FakeReport()

    class FakeClient:
        pass

    monkeypatch.setattr("sentinel.cli.DryRunClient", FakeClient)
    monkeypatch.setattr("sentinel.cli.FlightTestRunner", FakeRunner)

    assert main(["run", "--dry-run", "--log", str(output)]) == 0
    assert output.exists()
    assert calls[0][1:] == ("dry-run", 40)
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "PASS"


def test_real_cli_requires_explicit_confirmation_without_constructing_client(monkeypatch):
    monkeypatch.setattr(
        "sentinel.cli.build_real_client",
        lambda args: (_ for _ in ()).throw(AssertionError("must not build client")),
    )

    assert main(["run", "--real"]) == 1


def test_real_cli_writes_failure_report_when_backend_setup_fails(monkeypatch, tmp_path):
    output = tmp_path / "setup-failure.jsonl"

    def fail_setup(_args):
        raise RuntimeError("USB Tello adapter was not found")

    monkeypatch.setattr("sentinel.cli.build_real_client", fail_setup)

    assert main(["run", "--real", "--confirm", "--log", str(output)]) == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "FAIL"
    assert payload["backend"] == "real"
    assert "USB Tello adapter" in payload["error"]


def test_cli_maps_failure_and_emergency_statuses_to_exit_codes(monkeypatch, tmp_path):
    @dataclass
    class FakeReport:
        status: str
        mode: str = "dry-run"
        commands: tuple = ()
        video_frame: object | None = None
        adapter: object | None = None
        error: str | None = None

    class FakeRunner:
        status = "FAIL"

        def __init__(self, client, mode, minimum_battery=40):
            pass

        def run(self, *, confirm):
            return FakeReport(self.status)

    class FakeClient:
        pass

    monkeypatch.setattr("sentinel.cli.DryRunClient", FakeClient)
    monkeypatch.setattr("sentinel.cli.FlightTestRunner", FakeRunner)
    output = tmp_path / "status.jsonl"

    assert main(["run", "--dry-run", "--log", str(output)]) == 1
    FakeRunner.status = "EMERGENCY_ABORTED"
    assert main(["run", "--dry-run", "--log", str(output)]) == 2


def test_emergency_cli_resolves_a_local_adapter_before_sending(monkeypatch):
    sent = []

    class FakeSender:
        def __init__(self, local_ip, tello_ip="192.168.10.1"):
            sent.append((local_ip, tello_ip))

        def send(self):
            sent.append("emergency")

    monkeypatch.setattr("sentinel.cli.EmergencySender", FakeSender)
    monkeypatch.setattr("sentinel.cli.resolve_local_ip", lambda args: "192.168.10.2")

    assert main(["emergency"]) == 0
    assert sent == [("192.168.10.2", "192.168.10.1"), "emergency"]


def test_jsonl_report_contains_status_commands_and_video_metadata(tmp_path):
    report = RunReport(
        status="PASS",
        mode="dry-run",
        commands=(
            CommandRecord(
                command="takeoff",
                response="ok",
                position=None,
                timestamp=datetime.now(timezone.utc),
            ),
        ),
        video_frame=VideoFrame(640, 480, datetime.now(timezone.utc)),
        adapter=None,
        backend="djitellopy-2.5.0",
    )
    output = tmp_path / "report.jsonl"

    write_report(report, output)

    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["status"] == "PASS"
    assert payload["commands"][0]["command"] == "takeoff"
    assert payload["backend"] == "djitellopy-2.5.0"
    assert payload["video_frame"]["width"] == 640
    assert payload["video_frame"]["height"] == 480
    assert "data" not in payload["video_frame"]


def test_emergency_sender_binds_selected_ip_and_sends_exact_command(monkeypatch):
    events = []

    class FakeSocket:
        def bind(self, address):
            events.append(("bind", address))

        def sendto(self, payload, address):
            events.append(("sendto", payload, address))

        def close(self):
            events.append(("close",))

    monkeypatch.setattr("sentinel.emergency.socket.socket", lambda *args: FakeSocket())

    EmergencySender(local_ip="192.168.10.2").send()

    assert events == [
        ("bind", ("192.168.10.2", 0)),
        ("sendto", b"emergency", ("192.168.10.1", 8889)),
        ("close",),
    ]
