"""WP5 Task 3.3: end-to-end proof — synthetic frame -> database pose artifact."""
import hashlib
import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest
from jsonschema import Draft202012Validator

from sentinel.contracts.pose import DronePoseReport
from sentinel.pose.detector import estimate_pose
from sentinel.pose.persistence import append_pose_event, ensure_schema, latest_pose
from sentinel.pose.tagmap import TagMap
from tests.pose.test_detector import TAG_MAP_DOC, render_frontal_tag

ROOT = Path(__file__).parents[2]
_wire = json.loads(
    (ROOT / "docs" / "superpowers" / "specs" / "contracts" / "sentinel-v0-wire.schema.json")
    .read_text(encoding="utf-8")
)
_validator = Draft202012Validator(
    {"$ref": "#/$defs/DronePoseReport", "$defs": _wire["$defs"]}
)


def test_synthetic_frame_to_database_pose_artifact(tmp_path):
    db_path = tmp_path / "pose.sqlite3"
    conn = sqlite3.connect(db_path)
    ensure_schema(conn)

    # LIVE estimate from a rendered synthetic frame (ground truth: tag 0 at
    # (5, 10) units, drone 0.5 m out along +x => room point (10, 10) units).
    report = estimate_pose(
        render_frontal_tag(0), TagMap(TAG_MAP_DOC),
        run_id="4f6b0a1e-7c2d-4e8a-9b1f-0a2c3d4e5f60",
        report_id="6f6b0a1e-7c2d-4e8a-9b1f-0a2c3d4e5f60",
    )
    append_pose_event(conn, report)

    latest = latest_pose(conn, report.run_id)
    assert latest is not None
    assert latest["stale"] is False

    pose = latest["pose"]
    assert (pose["pose"]["point"]["x"], pose["pose"]["point"]["y"]) == pytest.approx(
        (10.0, 10.0), abs=1.0)
    assert pose["method"] == "APRILTAG_36H11_PNP"

    # stored row: hash matches sha256 of the canonical payload
    row = conn.execute(
        "SELECT payload_json, payload_sha256 FROM pose_events WHERE run_id = ?",
        (report.run_id,),
    ).fetchone()
    payload_json, payload_sha256 = row
    assert payload_sha256 == hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

    # stored payload re-validates against the wire schema after round-trip
    stored_payload = json.loads(payload_json)
    assert "event_type" not in stored_payload
    assert not list(_validator.iter_errors(stored_payload))  # validates as stored
    rebuilt = DronePoseReport.from_dict(stored_payload)
    wire = rebuilt.to_dict()
    errors = list(_validator.iter_errors(wire))
    assert not errors, [e.message for e in errors]
    conn.close()
