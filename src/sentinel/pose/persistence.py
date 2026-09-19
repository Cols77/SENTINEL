"""WP5 Task 3.1: run-scoped, append-only pose event persistence.

Pose events are immutable `DRONE_POSE_REPORTED` rows keyed by
`(run_id, sequence_no)`; staleness is derived read-time per WP0 §3.4 and is
never persisted.
"""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from sentinel.contracts.pose import DronePoseReport

EVENT_TYPE = "DRONE_POSE_REPORTED"
DEFAULT_POSE_STALE_AFTER_MS = 2000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pose_events (
  run_id TEXT NOT NULL, sequence_no INTEGER NOT NULL,
  event_type TEXT NOT NULL CHECK (event_type = 'DRONE_POSE_REPORTED'),
  payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  PRIMARY KEY (run_id, sequence_no));
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


def _canonical_payload(report: DronePoseReport) -> str:
    d = {
        "event_type": EVENT_TYPE,
        "report_id": report.report_id,
        "run_id": report.run_id,
        "observed_at": report.observed_at.isoformat(),
        "source_kind": report.source_kind,
        "point": list(report.point),
        "heading_deg": report.heading_deg,
        "altitude_m": report.altitude_m,
        "method": report.method,
        "tag_observations": [
            {
                "tag_id": o.tag_id,
                "zone_id": o.zone_id,
                "distance_m": o.distance_m,
                "reprojection_error_px": o.reprojection_error_px,
            }
            for o in report.tag_observations
        ],
        "reprojection_error_px": report.reprojection_error_px,
    }
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def append_pose_event(conn: sqlite3.Connection, report: DronePoseReport) -> int:
    """Append one pose event; sequence_no is MAX+1 scoped to report.run_id."""
    payload_json = _canonical_payload(report)
    payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    recorded_at = (
        report.observed_at
        if report.observed_at.tzinfo
        else report.observed_at.replace(tzinfo=timezone.utc)
    ).isoformat()
    cur = conn.execute(
        "INSERT INTO pose_events (run_id, sequence_no, event_type, payload_json,"
        " payload_sha256, recorded_at) VALUES (?,"
        " (SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM pose_events WHERE run_id = ?),"
        " ?, ?, ?, ?)",
        (report.run_id, report.run_id, EVENT_TYPE, payload_json, payload_sha256, recorded_at),
    )
    conn.commit()
    return cur.lastrowid if cur.lastrowid else 0


def latest_pose(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    stale_after_ms: int = DEFAULT_POSE_STALE_AFTER_MS,
    now: datetime | None = None,
):
    """Newest pose snapshot for the run, or None.

    `stale` is computed read-time per WP0 §3.4: age of the newest event
    exceeds `stale_after_ms`. `now` is injectable for tests; defaults to
    the current UTC time.
    """
    row = conn.execute(
        "SELECT sequence_no, payload_json, recorded_at FROM pose_events"
        " WHERE run_id = ? ORDER BY sequence_no DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    sequence_no, payload_json, recorded_at = row
    payload = json.loads(payload_json)
    ref = now if now is not None else datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    rec = datetime.fromisoformat(recorded_at)
    if rec.tzinfo is None:
        rec = rec.replace(tzinfo=timezone.utc)
    age_ms = max(0, round((ref - rec).total_seconds() * 1000))
    return {
        "pose": payload,
        "stale": age_ms > stale_after_ms,
        "snapshot_version": sequence_no,
    }
