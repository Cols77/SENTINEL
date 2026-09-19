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

    class FakeClient:
        def connect(self):
            calls.append("connect")

        def close(self):
            calls.append("close")

    def fake_stream(client, tag_map, conn, *, run_id, max_frames, duration_s):
        assert client is fake_client
        assert max_frames == 1
        assert duration_s is None
        assert tag_map.has_tag(0)
        assert isinstance(conn, sqlite3.Connection)
        calls.append(("stream", run_id))
        return [
            SimpleNamespace(
                point=(1.25, -0.5),
                altitude_m=0.8,
            )
        ]

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
    assert calls[-1] == "close"
    output = capsys.readouterr().out
    assert "x=1.25" in output
    assert "y=-0.5" in output
    assert "z=0.8" in output
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
                "--tag-map",
                TAG_MAP,
                "--db",
                str(tmp_path / "pose.db"),
            ]
        )
        == 1
    )
    assert "requires --max-frames and/or --duration" in capsys.readouterr().out
