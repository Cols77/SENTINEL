"""Offline end-to-end: synthetic video -> pose-stream --replay -> pose.db -> pose-score."""
import json
import sqlite3

import cv2

from sentinel.cli import main
from sentinel.pose.detector import estimate_pose
from sentinel.pose.tagmap import load_tag_map
from tests.pose.test_live_stream import TAG_MAP, _render_mapped_tag


def test_replay_video_to_db_to_score_pass(tmp_path, capsys):
    frame = _render_mapped_tag(0)
    truth_report = estimate_pose(frame, load_tag_map(TAG_MAP), run_id="truth", report_id="x")
    video = tmp_path / "synthetic.mp4"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 10, (960, 720))
    assert writer.isOpened()
    for _ in range(30):
        writer.write(frame)
    writer.release()

    truth = tmp_path / "truth.json"
    truth.write_text(json.dumps({"point1": {
        "x": truth_report.point[0], "y": truth_report.point[1],
        "heading_deg": truth_report.heading_deg}}))
    db = tmp_path / "pose.db"

    assert main(["pose-stream", "--replay", str(video), "--allow-nominal",
                 "--tag-map", TAG_MAP, "--db", str(db), "--max-frames", "30",
                 "--run-id", "point1"]) == 0
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM pose_events WHERE run_id='point1'").fetchone()[0]
    conn.close()
    assert n >= 25
    capsys.readouterr()

    assert main(["pose-score", "--db", str(db), "--truth", str(truth),
                 "--tag-map", TAG_MAP, "--allow-nominal"]) == 0
    assert "PASS" in capsys.readouterr().out
