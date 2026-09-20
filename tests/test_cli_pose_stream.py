import sqlite3
from types import SimpleNamespace

from sentinel.cli import build_parser, main



TAG_MAP = "docs/superpowers/specs/contracts/fixtures/tag-map-v1.valid.json"


def test_pose_stream_parser_accepts_safe_stream_options(tmp_path):
    args = build_parser().parse_args(
        [
            "pose-stream",
            "--real",
            "--confirm",
            "--allow-nominal",
            "--tag-map",
            TAG_MAP,
            "--db",
            str(tmp_path / "pose.db"),
            "--max-frames",
            "3",
            "--tello-ip",
            "192.168.10.1",
        ]
    )
    assert args.command == "pose-stream"
    assert args.max_frames == 3
    assert args.duration is None
    assert args.db == tmp_path / "pose.db"


def test_pose_stream_fake_client_persists_and_prints_pose(monkeypatch, tmp_path, capsys):
    calls = []
    kwargs_seen = []

    class FakeClient:
        def connect(self):
            calls.append("connect")

        def close(self):
            calls.append("close")

    def fake_stream(client, tag_map, conn, *, run_id, max_frames, duration_s, **kwargs):
        kwargs_seen.append(kwargs)
        assert client is fake_client
        assert max_frames == 1
        assert duration_s is None
        assert tag_map.has_tag(0)
        assert isinstance(conn, sqlite3.Connection)
        calls.append(("stream", run_id))
        reports = [
            SimpleNamespace(
                point=(1.25, -0.5),
                altitude_m=0.8,
                heading_deg=-90.0,
            )
        ]
        for r in reports:  # the real stream reports each pose live via on_report
            kwargs["on_report"](r)
        return reports

    fake_client = FakeClient()
    monkeypatch.setattr("sentinel.cli.build_real_client", lambda _args: fake_client)
    monkeypatch.setattr("sentinel.cli.run_pose_stream", fake_stream)

    db = tmp_path / "pose.db"
    assert (
        main(
            [
                "pose-stream",
                "--real",
                "--confirm",
                "--allow-nominal",
                "--tag-map",
                TAG_MAP,
                "--db",
                str(db),
                "--max-frames",
                "1",
            ]
        )
        == 0
    )

    assert calls[0] == "connect"
    assert calls[1][0] == "stream"
    assert kwargs_seen[0]["source_kind"] == "LIVE"
    assert calls[-1] == "close"
    output = capsys.readouterr().out
    assert "x=1.25" in output
    assert "y=-0.5" in output
    assert "z=0.8" in output
    assert "heading=-90.0" in output
    conn = sqlite3.connect(db)
    try:
        conn.execute("SELECT 1")
    finally:
        conn.close()


def test_pose_stream_requires_bound(tmp_path, capsys):
    assert (
        main(
            [
                "pose-stream",
                "--real",
                "--confirm",
                "--allow-nominal",
                "--tag-map",
                TAG_MAP,
                "--db",
                str(tmp_path / "pose.db"),
            ]
        )
        == 1
    )
    assert "requires --max-frames and/or --duration" in capsys.readouterr().out


def _fake_real(monkeypatch, stream):
    class C:
        closed = False

        def connect(self):
            pass

        def close(self):
            C.closed = True

    monkeypatch.setattr("sentinel.cli.build_real_client", lambda _a: C())
    monkeypatch.setattr("sentinel.cli.run_pose_stream", stream)


def test_pose_stream_refuses_nominal_calibration_without_flag(monkeypatch, tmp_path, capsys):
    called = []
    _fake_real(monkeypatch, lambda *a, **k: called.append(1) or [])
    rc = main(["pose-stream", "--real", "--confirm", "--tag-map", TAG_MAP,
               "--db", str(tmp_path / "p.db"), "--max-frames", "1"])
    assert rc == 1
    assert called == []
    assert "nominal" in capsys.readouterr().out


def test_pose_stream_accepts_calibrated_map_without_flag(monkeypatch, tmp_path, capsys):
    import json
    doc = json.load(open(TAG_MAP, encoding="utf-8"))
    doc["camera_calibration"]["calibration_source"] = "chessboard"
    tm = tmp_path / "tm.json"
    tm.write_text(json.dumps(doc))
    _fake_real(monkeypatch, lambda *a, **k: [])
    rc = main(["pose-stream", "--real", "--confirm", "--tag-map", str(tm),
               "--db", str(tmp_path / "p.db"), "--max-frames", "1"])
    assert rc == 0


def test_pose_stream_real_and_replay_mutually_exclusive(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        build_parser().parse_args(["pose-stream", "--real", "--replay", str(tmp_path),
                                   "--tag-map", TAG_MAP, "--max-frames", "1"])


def test_pose_stream_requires_real_or_replay(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        build_parser().parse_args(["pose-stream", "--tag-map", TAG_MAP, "--max-frames", "1"])


def test_pose_stream_replay_needs_no_confirm_and_marks_replayed(monkeypatch, tmp_path, capsys):
    seen = {}

    def fake_stream(client, tag_map, conn, **kw):
        seen.update(kw)
        seen["client"] = client
        reports = [SimpleNamespace(point=(1.0, 2.0), altitude_m=None, heading_deg=12.5)]
        for r in reports:
            kw["on_report"](r)
        return reports

    monkeypatch.setattr("sentinel.cli.run_pose_stream", fake_stream)
    monkeypatch.setattr("sentinel.cli.build_real_client",
                        lambda _a: (_ for _ in ()).throw(AssertionError("no drone in replay")))
    rc = main(["pose-stream", "--replay", str(tmp_path), "--allow-nominal", "--tag-map", TAG_MAP,
               "--db", str(tmp_path / "p.db"), "--max-frames", "5"])
    assert rc == 0
    assert seen["source_kind"] == "REPLAYED"
    assert seen["altitude_source"] is None
    out = capsys.readouterr().out
    assert "z=n/a" in out and "heading=12.5" in out
    import re
    assert re.search(r"run_id=[0-9a-f-]{36}", out)


def test_pose_stream_live_wires_recorder_and_altitude(monkeypatch, tmp_path):
    seen = {}
    _fake_real(monkeypatch, lambda *a, **k: seen.update(k) or [])
    rc = main(["pose-stream", "--real", "--confirm", "--allow-nominal", "--tag-map", TAG_MAP,
               "--db", str(tmp_path / "p.db"), "--max-frames", "1",
               "--record-dir", str(tmp_path / "rec")])
    assert rc == 0
    assert seen["recorder"] is not None
    assert callable(seen["altitude_source"])


def test_pose_score_cli(tmp_path, capsys):
    import json
    from datetime import datetime, timezone
    from sentinel.contracts.pose import DronePoseReport, TagObservation
    from sentinel.pose.persistence import append_pose_event, ensure_schema

    db = tmp_path / "s.db"
    conn = sqlite3.connect(db)
    ensure_schema(conn)
    for i in range(3):
        append_pose_event(conn, DronePoseReport(
            report_id=f"00000000-0000-4000-8000-{i:012d}", run_id="p1",
            observed_at=datetime.now(timezone.utc), source_kind="SIMULATED",
            point=(1.0, 2.0), heading_deg=10.0, altitude_m=None,
            method="APRILTAG_36H11_PNP",
            tag_observations=(TagObservation(tag_id=0, zone_id="Z", distance_m=0.5,
                                             reprojection_error_px=0.5),),
            reprojection_error_px=0.5))
    conn.close()
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"p1": {"x": 1.0, "y": 2.0, "heading_deg": 10.0}}))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"p1": {"x": 9.0, "y": 2.0, "heading_deg": 10.0}}))
    tm = ["--tag-map", "docs/superpowers/specs/contracts/fixtures/tag-map-v1.valid.json"]
    assert main(["pose-score", "--db", str(db), "--truth", str(good), *tm, "--allow-nominal"]) == 0
    assert "PASS" in capsys.readouterr().out
    assert main(["pose-score", "--db", str(db), "--truth", str(bad), *tm, "--allow-nominal"]) == 1
    assert "FAIL" in capsys.readouterr().out
    # nominal calibration is rejected even when the errors are within thresholds
    assert main(["pose-score", "--db", str(db), "--truth", str(good), *tm]) == 1
    assert "nominal" in capsys.readouterr().out.lower()


def test_pose_score_parser_accepts_min_samples(tmp_path):
    args = build_parser().parse_args(
        ["pose-score", "--db", str(tmp_path / "d.db"), "--truth", str(tmp_path / "t.json"),
         "--tag-map", "docs/superpowers/specs/contracts/fixtures/tag-map-v1.valid.json",
         "--min-samples", "30"]
    )
    assert args.min_samples == 30


def test_pose_score_cli_min_samples_flips_exit_code(tmp_path, capsys):
    import json
    from datetime import datetime, timezone
    from sentinel.contracts.pose import DronePoseReport, TagObservation
    from sentinel.pose.persistence import append_pose_event, ensure_schema

    db = tmp_path / "s.db"
    conn = sqlite3.connect(db)
    ensure_schema(conn)
    for i in range(2):
        append_pose_event(conn, DronePoseReport(
            report_id=f"00000000-0000-4000-8000-{i:012d}", run_id="p1",
            observed_at=datetime.now(timezone.utc), source_kind="SIMULATED",
            point=(1.0, 2.0), heading_deg=10.0, altitude_m=None,
            method="APRILTAG_36H11_PNP",
            tag_observations=(TagObservation(tag_id=0, zone_id="Z", distance_m=0.5,
                                             reprojection_error_px=0.5),),
            reprojection_error_px=0.5))
    conn.close()
    truth = tmp_path / "truth.json"
    truth.write_text(json.dumps({"p1": {"x": 1.0, "y": 2.0, "heading_deg": 10.0}}))
    tm = ["--tag-map", "docs/superpowers/specs/contracts/fixtures/tag-map-v1.valid.json"]

    assert main(["pose-score", "--db", str(db), "--truth", str(truth), *tm,
                 "--allow-nominal"]) == 0
    capsys.readouterr()

    rc = main(["pose-score", "--db", str(db), "--truth", str(truth), *tm,
               "--allow-nominal", "--min-samples", "3"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "below min_samples=3" in out
