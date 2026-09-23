import json
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from sentinel.cli import main
from sentinel.emergency import EmergencySender
from sentinel.models import CommandRecord, RunReport, VideoFrame
from sentinel.reporting import write_report, write_video_manifest

SITE_DOC = {
    "schema_version": "cube-site-v1",
    "line_y_m": 5.0,
    "stands": [{"x_m": x, "first_tag_id": 2 * i, "heights_m": [0.90, 1.45], "size_m": 0.192}
               for i, x in enumerate([0.25, 0.75, 2.3, 2.7, 3.1, 4.65, 5.15])],
    "cube": {"min_x_m": 0.3, "max_x_m": 5.1, "min_y_m": 2.0, "max_y_m": 4.2, "max_z_m": 2.0},
    "commanded": {"inset_m": 0.3, "min_z_cm": 80, "max_z_cm": 180},
}
CALIBRATION = {"width": 960, "height": 720, "fx": 893.0, "fy": 894.0, "cx": 468.0,
               "cy": 354.0, "dist_coeffs": [], "calibration_source": "chessboard"}


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


def test_yaw_scan_dry_run_is_limited_to_z_rotation(tmp_path):
    output = tmp_path / "yaw-scan.jsonl"
    assert (
        main(
            [
                "yaw-scan",
                "--dry-run",
                "--video-path",
                str(tmp_path / "capture.h264"),
                "--dwell-seconds",
                "0.01",
                "--turn",
                "cw:90",
                "--turn",
                "cw:90",
                "--log",
                str(output),
            ]
        )
        == 0
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    displays = [command["display"] for command in payload["commands"]]
    assert displays == [
        "command",
        "speed 20",
        "takeoff",
        "streamon",
        "video frame",
        "cw 90",
        "video frame",
        "cw 90",
        "video frame",
        "streamoff",
        "land",
    ]
    assert not any(
        display.startswith(("forward ", "back ", "left ", "right ", "up ", "down "))
        for display in displays
    )


def test_yaw_scan_rejects_directional_turn_before_client_creation(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "sentinel.cli.DryRunClient",
        lambda: (_ for _ in ()).throw(AssertionError("client must not be created")),
    )
    with pytest.raises(SystemExit):
        main(
            [
                "yaw-scan",
                "--dry-run",
                "--video-path",
                str(tmp_path / "capture.h264"),
                "--turn",
                "front:90",
            ]
        )


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


def test_real_cli_creates_progress_log_before_backend_setup_fails(
    monkeypatch, tmp_path, capsys
):
    output = tmp_path / "run.jsonl"
    progress = tmp_path / "run.progress.jsonl"

    def fail_setup(_args):
        raise RuntimeError("Tello connectivity failed")

    monkeypatch.setattr("sentinel.cli.build_real_client", fail_setup)

    assert (
        main(
            [
                "run",
                "--real",
                "--confirm",
                "--log",
                str(output),
                "--progress-log",
                str(progress),
            ]
        )
        == 1
    )

    events = [json.loads(line) for line in progress.read_text().splitlines()]
    assert events[0]["event"] == "run_started"
    assert events[-1]["event"] == "run_failed"
    assert "Tello connectivity failed" in events[-1]["message"]
    assert "Tello connectivity failed" in capsys.readouterr().out


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
    monkeypatch.setattr(
        "sentinel.cli.resolve_local_ip",
        lambda args, **_kwargs: "192.168.10.2",
    )

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


def test_video_manifest_records_hash_and_decoder_probe(tmp_path):
    video = tmp_path / "capture.h264"
    video.write_bytes(b"raw-h264")
    report = RunReport(
        status="FAIL",
        mode="real",
        video_frame=VideoFrame(width=960, height=720, data=b"abc"),
    )

    manifest_path = write_video_manifest(video, report)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert payload["format"] == "h264-annex-b"
    assert payload["size_bytes"] == len(b"raw-h264")
    assert payload["sha256"] == "d2666a33ee2ec446628af320470961921a4e558e5ae0d0445320bdc5565dc11d"
    assert payload["decoded_frame_probe"]["width"] == 960

def test_emergency_sender_binds_selected_ip_and_sends_exact_command(monkeypatch):
    events = []

    class FakeSocket:
        def bind(self, address):
            events.append(("bind", address))

        def sendto(self, payload, address):
            events.append(("sendto", payload, address))
            return len(payload)

        def close(self):
            events.append(("close",))

    monkeypatch.setattr("sentinel.emergency.socket.socket", lambda *args: FakeSocket())

    assert EmergencySender(local_ip="192.168.10.2").send() == len(b"emergency")

    assert events == [
        ("bind", ("192.168.10.2", 0)),
        ("sendto", b"emergency", ("192.168.10.1", 8889)),
        ("close",),
    ]


def test_emergency_sender_rejects_partial_datagram():
    class PartialSocket:
        def bind(self, _address):
            pass

        def sendto(self, payload, _address):
            return len(payload) - 1

        def close(self):
            pass

    sender = EmergencySender(
        local_ip="192.168.10.2",
        socket_factory=lambda *_args: PartialSocket(),
    )

    with pytest.raises(OSError, match="emergency datagram incomplete"):
        sender.send()


def test_emergency_cli_reports_success(capsys, monkeypatch):
    class FakeSender:
        def __init__(self, local_ip, tello_ip="192.168.10.1"):
            assert local_ip == "192.168.10.2"
            assert tello_ip == "192.168.10.1"

        def send(self):
            return len(b"emergency")

    monkeypatch.setattr("sentinel.cli.EmergencySender", FakeSender)
    monkeypatch.setattr(
        "sentinel.cli.resolve_local_ip",
        lambda _args, **_kwargs: "192.168.10.2",
    )

    assert main(["emergency"]) == 0
    output = capsys.readouterr().out
    assert "emergency" in output.lower()
    assert "192.168.10.2" in output
    assert "sent" in output.lower()


def test_emergency_cli_bypasses_ack_reachability_probe(monkeypatch):
    class FakeSender:
        def __init__(self, local_ip, tello_ip="192.168.10.1"):
            assert local_ip == "192.168.10.2"
            assert tello_ip == "192.168.10.1"

        def send(self):
            return len(b"emergency")

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("explicit emergency IP must not resolve the default route")

    monkeypatch.setattr("sentinel.cli._select_adapter", fail_if_called)
    monkeypatch.setattr("sentinel.cli.EmergencySender", FakeSender)

    assert main(["emergency", "--local-ip", "192.168.10.2"]) == 0


def test_explicit_emergency_ip_reaches_sender_when_route_discovery_fails(monkeypatch):
    class FakeSender:
        def __init__(self, local_ip, tello_ip="192.168.10.1"):
            assert local_ip == "192.168.10.2"
            assert tello_ip == "192.168.10.1"

        def send(self):
            return len(b"emergency")

    monkeypatch.setattr(
        "sentinel.cli._select_adapter",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("default route unavailable")
        ),
    )
    monkeypatch.setattr("sentinel.cli.EmergencySender", FakeSender)

    assert main(["emergency", "--local-ip", "192.168.10.2"]) == 0


def _cube_files(tmp_path):
    import json

    site = tmp_path / "site.json"
    site.write_text(json.dumps(SITE_DOC))
    mission = tmp_path / "mission.json"
    mission.write_text(json.dumps({
        "schema_version": "cube-mission-v1", "start": {"x_cm": 270, "y_cm": 250},
        "waypoints": [{"name": "WP1", "x_cm": 270, "y_cm": 300, "z_cm": 120,
                       "looks_deg": [-30, 30]}]}))
    return site, mission


def test_cube_run_dry_run_off_mode(tmp_path, monkeypatch):
    import json

    from sentinel.cli import main

    monkeypatch.setattr("sentinel.cube.mission.SETTLE_S", 0.0)
    site, mission = _cube_files(tmp_path)
    log = tmp_path / "mission.jsonl"
    code = main(["cube-run", "--dry-run", "--site", str(site), "--mission", str(mission),
                 "--mission-log", str(log), "--log", str(tmp_path / "run.jsonl")])
    assert code == 0
    assert [json.loads(line)["status"] for line in log.read_text().splitlines()] == ["OPEN_LOOP"]


def test_cube_run_passive_dry_run_is_honestly_unlocalised(tmp_path, monkeypatch):
    import json

    from sentinel.cli import main

    monkeypatch.setattr("sentinel.cube.mission.SETTLE_S", 0.0)
    site, mission = _cube_files(tmp_path)
    tag_map = tmp_path / "map.json"
    assert main(["cube-site-map", "--site", str(site), "--calibration-from",
                 "calibration/tag-map-calibrated.json", "--out", str(tag_map)]) == 0
    log = tmp_path / "mission.jsonl"
    code = main(["cube-run", "--dry-run", "--site", str(site), "--mission", str(mission),
                 "--pose", "passive", "--tag-map", str(tag_map),
                 "--db", str(tmp_path / "pose.db"), "--mission-log", str(log),
                 "--log", str(tmp_path / "run.jsonl")])
    assert code == 0
    assert [json.loads(line)["status"] for line in log.read_text().splitlines()] == ["UNLOCALISED"]


def test_cube_run_refuses_a_tag_map_of_another_room(tmp_path, capsys):
    """The coverage guard predicts from the site file and localisation solves against the tag
    map: a flight where they describe different tags is refused before takeoff."""
    from sentinel.cli import main

    site, mission = _cube_files(tmp_path)
    code = main(["cube-run", "--dry-run", "--site", str(site), "--mission", str(mission),
                 "--pose", "passive", "--tag-map", "calibration/tag-map-calibrated.json",
                 "--db", str(tmp_path / "pose.db"), "--mission-log", str(tmp_path / "m.jsonl"),
                 "--log", str(tmp_path / "run.jsonl")])
    assert code == 1 and "differ from the site file" in capsys.readouterr().out


def test_cube_run_reanchor_requires_the_amendment_flag(tmp_path, capsys):
    from sentinel.cli import main

    site, mission = _cube_files(tmp_path)
    code = main(["cube-run", "--dry-run", "--site", str(site), "--mission", str(mission),
                 "--pose", "reanchor", "--tag-map", "calibration/tag-map-calibrated.json",
                 "--log", str(tmp_path / "run.jsonl")])
    assert code == 1 and "SR-063" in capsys.readouterr().out
