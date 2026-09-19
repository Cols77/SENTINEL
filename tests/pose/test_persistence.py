"""WP5 Task 3.1: run-scoped pose event persistence with read-time staleness."""
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from sentinel.pose.persistence import append_pose_event, latest_pose

RUN_A = "4f6b0a1e-7c2d-4e8a-9b1f-0a2c3d4e5f60"
RUN_B = "5f6b0a1e-7c2d-4e8a-9b1f-0a2c3d4e5f60"


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
      CREATE TABLE pose_events (
        run_id TEXT NOT NULL, sequence_no INTEGER NOT NULL,
        event_type TEXT NOT NULL CHECK (event_type = 'DRONE_POSE_REPORTED'),
        payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        PRIMARY KEY (run_id, sequence_no));
    """
    )
    yield conn


def _report(run_id=RUN_A, point=(12.5, 40.0), **over):
    from tests.contracts.test_pose_contract import _report as contract_report

    base = {"run_id": run_id, "point": point}
    base.update(over)
    return contract_report(**base)


def test_appends_monotonic_sequence(db):
    append_pose_event(db, _report())
    append_pose_event(db, _report(point=(13.0, 41.0)))
    rows = db.execute(
        "SELECT sequence_no, payload_json, payload_sha256 FROM pose_events "
        "ORDER BY sequence_no"
    ).fetchall()
    assert [r[0] for r in rows] == [1, 2]
    # canonical payload + verifiable hash
    import hashlib

    for _, payload_json, payload_sha256 in rows:
        payload = json.loads(payload_json)
        assert payload["event_type"] == "DRONE_POSE_REPORTED"
        assert hashlib.sha256(payload_json.encode("utf-8")).hexdigest() == payload_sha256


def test_latest_pose_returns_newest_point(db):
    now = datetime.now(timezone.utc)
    append_pose_event(db, _report(point=(12.5, 40.0), observed_at=now))
    append_pose_event(db, _report(point=(13.0, 41.0), observed_at=now))
    snap = latest_pose(db, RUN_A)
    assert snap["pose"]["point"] == [13.0, 41.0]
    assert snap["stale"] is False
    assert snap["snapshot_version"] == 2


def test_foreign_run_is_isolated(db):
    append_pose_event(db, _report())
    assert latest_pose(db, RUN_B) is None


def test_latest_pose_none_on_empty(db):
    assert latest_pose(db, RUN_A) is None


def test_staleness_boundary_both_sides(db):
    observed = datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc)
    append_pose_event(db, _report(observed_at=observed))
    stale_after_ms = 2000
    # age exactly == stale_after_ms -> fresh (not stale)
    at_boundary = datetime(2026, 9, 18, 10, 0, 2, tzinfo=timezone.utc)
    snap = latest_pose(db, RUN_A, stale_after_ms=stale_after_ms, now=at_boundary)
    assert snap["stale"] is False
    # age one ms past the boundary -> stale
    just_over = datetime(2026, 9, 18, 10, 0, 2, 1000, tzinfo=timezone.utc)
    snap = latest_pose(db, RUN_A, stale_after_ms=stale_after_ms, now=just_over)
    assert snap["stale"] is True
