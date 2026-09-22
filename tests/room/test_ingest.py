"""Decision-grade perception, persisted independently of provider and flight."""
from dataclasses import asdict, replace
from datetime import datetime
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from sentinel.room.ingest import (
    DetectionEvidence, PerceptionRequest, PerceptionResult, InvalidPerception,
    append_inbox_result, canonical_line, detection_digest, ingest_result,
    poll_inbox, result_from_dict,
)
from sentinel.room.models import CapabilityProfile
from sentinel.room.store import IdempotencyConflict, RoomStore


def sample(tmp_path, *, session="s1", observation="o1", snapshot=0):
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"immutable frame")
    request = PerceptionRequest(
        "room-perception-v1", session, "a1", observation, "f1", "run1", snapshot,
        "a" * 64, str(frame), hashlib.sha256(frame.read_bytes()).hexdigest(),
        "2026-09-22T00:00:00+00:00", "pose1", "pinned-model",
    )
    evidence = DetectionEvidence(
        "d1", "computer", "high", None, "VLM_CONFIDENCE_BAND", (1, 2, 30, 40),
        "FULL_FRAME_PIXELS", 640, 480, None, "",
    )
    evidence = replace(evidence, evidence_sha256=detection_digest(evidence))
    return PerceptionResult(request, "SUCCEEDED", "fixture", "pinned-model", "VERIFIED",
                            "2026-09-22T00:00:01+00:00", (evidence,))


def store_for(tmp_path):
    store = RoomStore.open(tmp_path / "room.sqlite3")
    store.create_session("s1", "look", CapabilityProfile(()))
    store.create_session("s2", "look", CapabilityProfile(()))
    return store


def count(store, table):
    return store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_ingest_preserves_correlation_evidence_and_replays_after_reopen(tmp_path):
    store, result = store_for(tmp_path), sample(tmp_path)
    outcome = ingest_result(store, result, current_snapshot_version=0)
    assert outcome.authorizes_current_state and not outcome.duplicate
    assert len(outcome.hypothesis_ids) == 1
    observation = store.connection.execute("SELECT * FROM room_observations").fetchone()
    assert json.loads(observation["request_json"]) == asdict(result.request)
    evidence = store.connection.execute("SELECT * FROM room_detection_evidence").fetchone()
    assert evidence["confidence_band"] == "high" and evidence["raw_score"] is None
    assert evidence["score_kind"] == "VLM_CONFIDENCE_BAND"
    assert json.loads(evidence["full_frame_bbox_json"]) == [1, 2, 30, 40]
    store.close()
    reopened = RoomStore.open(tmp_path / "room.sqlite3")
    replay = ingest_result(reopened, result, current_snapshot_version=0)
    assert replay.duplicate and replay.result_id == outcome.result_id
    assert count(reopened, "room_hypothesis_revisions") == 1
    assert not ingest_result(reopened, result, current_snapshot_version=1).authorizes_current_state


def test_payload_deduplicates_but_session_and_observation_evidence_never_collapse(tmp_path):
    store, result = store_for(tmp_path), sample(tmp_path)
    first = ingest_result(store, result, current_snapshot_version=0)
    second_result = replace(result, request=replace(result.request, session_id="s2"))
    second = ingest_result(store, second_result, current_snapshot_version=0)
    third = ingest_result(store, replace(result, request=replace(result.request, observation_id="o2")),
                          current_snapshot_version=0)
    assert first.payload_id == second.payload_id == third.payload_id
    assert len({first.result_id, second.result_id, third.result_id}) == 3
    assert first.hypothesis_ids != second.hypothesis_ids
    for table in ("room_observations", "room_perception_results", "room_detection_evidence",
                  "room_object_hypotheses", "room_hypothesis_revisions"):
        assert count(store, table) == 3
    assert count(store, "room_perception_payloads") == 1


def test_conflicting_delivery_or_cached_payload_is_rejected_without_writes(tmp_path):
    store, result = store_for(tmp_path), sample(tmp_path)
    ingest_result(store, result, current_snapshot_version=0)
    with pytest.raises(IdempotencyConflict):
        ingest_result(store, replace(result, completed_at="2026-09-22T00:00:02+00:00"),
                      current_snapshot_version=0)
    altered = replace(result.detections[0], confidence_band="low", evidence_sha256="")
    altered = replace(altered, evidence_sha256=detection_digest(altered))
    with pytest.raises(IdempotencyConflict):
        ingest_result(store, replace(result, request=replace(result.request, session_id="s2"),
                                     detections=(altered,)), current_snapshot_version=0)
    assert count(store, "room_observations") == 1


@pytest.mark.parametrize("category", ["TIMEOUT", "AUTH", "TRANSPORT"])
def test_failure_before_identity_resolution_is_persisted_without_hypotheses(tmp_path, category):
    store, result = store_for(tmp_path), sample(tmp_path)
    failed = replace(result, status="FAILED", resolved_model=None, identity_status="UNVERIFIED",
                     detections=(), error_category=category)
    outcome = ingest_result(store, failed, current_snapshot_version=0)
    assert not outcome.authorizes_current_state and outcome.hypothesis_ids == ()
    row = store.connection.execute("SELECT * FROM room_perception_results").fetchone()
    assert row["error_category"] == category and row["resolved_model"] is None
    assert count(store, "room_object_hypotheses") == 0


def test_late_result_is_historical_and_cannot_be_promoted_by_retry(tmp_path):
    store, result = store_for(tmp_path), sample(tmp_path)
    outcome = ingest_result(store, result, current_snapshot_version=1)
    assert not outcome.authorizes_current_state
    revision = store.connection.execute("SELECT * FROM room_hypothesis_revisions").fetchone()
    assert revision["authorizes_current_state"] == 0
    assert count(store, "room_detection_evidence") == 1
    assert not ingest_result(store, result, current_snapshot_version=0).authorizes_current_state
    assert store.current_hypotheses("s1", snapshot_version=1) == ()


@pytest.mark.parametrize("mutation", [
    {"resolved_model": "other"}, {"resolved_model": None, "identity_status": "UNVERIFIED"},
    {"identity_status": "MISMATCH"}, {"status": "garbage"}, {"error_category": "AUTH"},
])
def test_invalid_identity_and_status_rejected_before_transaction(tmp_path, mutation):
    store, result = store_for(tmp_path), sample(tmp_path)
    statements = []
    store.connection.set_trace_callback(statements.append)
    with pytest.raises(InvalidPerception):
        ingest_result(store, replace(result, **mutation), current_snapshot_version=0)
    assert not any(s.startswith("BEGIN") for s in statements)
    assert count(store, "room_observations") == 0


def test_frame_hash_and_missing_path_errors_do_not_disclose_paths_or_credentials(tmp_path):
    store, result = store_for(tmp_path), sample(tmp_path)
    for request in (replace(result.request, frame_sha256="b" * 64),
                    replace(result.request, frame_path=str(tmp_path / "api-key-super-secret.png"))):
        with pytest.raises(InvalidPerception) as error:
            ingest_result(store, replace(result, request=request), current_snapshot_version=0)
        assert "super-secret" not in str(error.value) and str(tmp_path) not in str(error.value)
    assert count(store, "room_observations") == 0


def test_tile_retains_original_box_and_resolves_box_for_consumers(tmp_path):
    store, result = store_for(tmp_path), sample(tmp_path)
    tile = replace(result.detections[0], bbox_coordinate_space="TILE_PIXELS", tile_origin_xy_px=(100, 50))
    tile = replace(tile, evidence_sha256=detection_digest(tile))
    ingest_result(store, replace(result, detections=(tile,)), current_snapshot_version=0)
    row = store.connection.execute("SELECT * FROM room_detection_evidence").fetchone()
    assert json.loads(row["original_bbox_json"]) == [1, 2, 30, 40]
    assert json.loads(row["full_frame_bbox_json"]) == [101, 52, 30, 40]
    current = store.current_hypotheses("s1", snapshot_version=0)
    assert current[0]["bbox_xywh"] == (101, 52, 30, 40)
    assert current[0]["raw_score"] is None and current[0]["confidence_band"] == "high"


@pytest.mark.parametrize("mutation", [
    {"frame_width_px": 0}, {"tile_origin_xy_px": None, "bbox_coordinate_space": "TILE_PIXELS"},
    {"tile_origin_xy_px": (630, 470), "bbox_coordinate_space": "TILE_PIXELS"},
    {"bbox_xywh": (1, 2, -3, 4)}, {"raw_score": 0.8, "score_kind": "VLM_CONFIDENCE_BAND"},
    {"confidence_band": "certain"}, {"bbox_xywh": (float("nan"), 2, 3, 4)},
])
def test_malformed_detection_is_rejected(tmp_path, mutation):
    store, result = store_for(tmp_path), sample(tmp_path)
    detection = replace(result.detections[0], **mutation)
    with pytest.raises(InvalidPerception):
        ingest_result(store, replace(result, detections=(detection,)), current_snapshot_version=0)
    assert count(store, "room_observations") == 0


def test_transaction_rolls_back_all_evidence_when_revision_insert_fails(tmp_path):
    store, result = store_for(tmp_path), sample(tmp_path)
    store.connection.execute("CREATE TRIGGER fail_revision BEFORE INSERT ON room_hypothesis_revisions "
                             "BEGIN SELECT RAISE(ABORT, 'forced revision failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="forced revision failure"):
        ingest_result(store, result, current_snapshot_version=0)
    for table in ("room_observations", "room_perception_payloads", "room_perception_results",
                  "room_detection_evidence", "room_object_hypotheses", "room_hypothesis_revisions"):
        assert count(store, table) == 0


def test_all_perception_tables_are_immutable_even_for_replace(tmp_path):
    store, result = store_for(tmp_path), sample(tmp_path)
    ingest_result(store, result, current_snapshot_version=0)
    for table in ("room_observations", "room_perception_payloads", "room_perception_results",
                  "room_detection_evidence", "room_object_hypotheses", "room_hypothesis_revisions"):
        column = store.connection.execute(f"PRAGMA table_info({table})").fetchone()[1]
        for sql in (f"UPDATE {table} SET {column} = {column}", f"DELETE FROM {table}",
                    f"INSERT OR REPLACE INTO {table} SELECT * FROM {table}"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only|immutable"):
                store.connection.execute(sql)
            store.connection.rollback()


def test_jsonl_roundtrip_rejects_extra_fields_and_defers_partial_records(tmp_path):
    store, result = store_for(tmp_path), sample(tmp_path)
    inbox = tmp_path / "inbox.jsonl"
    append_inbox_result(inbox, result)
    assert inbox.read_bytes() == canonical_line(asdict(result))
    assert result_from_dict(json.loads(inbox.read_text())) == result
    assert len(poll_inbox(store, inbox, current_snapshot_version=0, wait_s=0)) == 1
    with inbox.open("ab") as handle:
        handle.write(b'{"request":')
    replay = poll_inbox(store, inbox, current_snapshot_version=0, wait_s=0)
    assert len(replay) == 1 and replay[0].duplicate
    with pytest.raises(InvalidPerception):
        append_inbox_result(inbox, result)
    inbox.write_text('{"token":"secret"}\n', encoding="utf-8")
    assert poll_inbox(store, inbox, current_snapshot_version=0, wait_s=0) == ()
    receipt = store.connection.execute("SELECT * FROM room_inbox_rejections").fetchone()
    assert receipt["category"] == "SCHEMA"
    assert "secret" not in str(dict(receipt))
    with pytest.raises(InvalidPerception):
        result_from_dict({**asdict(result), "unexpected": "secret"})


def test_writer_publishes_only_after_fsync_and_preserves_old_file_on_failure(tmp_path, monkeypatch):
    import sentinel.room.ingest as module
    result, inbox = sample(tmp_path), tmp_path / "inbox.jsonl"
    append_inbox_result(inbox, result)
    old = inbox.read_bytes()
    called = []
    real_sync = module.os.fsync

    def sync(fd):
        assert inbox.read_bytes() == old
        called.append("fsync")
        real_sync(fd)

    monkeypatch.setattr(module.os, "fsync", sync)
    append_inbox_result(inbox, result)
    assert called == ["fsync"] and inbox.read_bytes() == old + old
    current = inbox.read_bytes()
    monkeypatch.setattr(module.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("full")))
    with pytest.raises(OSError):
        append_inbox_result(inbox, result)
    assert inbox.read_bytes() == current


def test_concurrent_deliveries_are_idempotent_and_writer_drops_no_records(tmp_path):
    store, result = store_for(tmp_path), sample(tmp_path)
    inbox = tmp_path / "inbox.jsonl"
    def deliver(_):
        append_inbox_result(inbox, result)
        return ingest_result(store, result, current_snapshot_version=0)
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(deliver, range(8)))
    assert sum(not outcome.duplicate for outcome in outcomes) == 1
    assert len(inbox.read_text().splitlines()) == 8
    assert count(store, "room_detection_evidence") == 1


def test_empty_inbox_poll_has_a_bounded_wait(tmp_path):
    store = store_for(tmp_path)
    times = iter((0, 0, 0.1, 0.2))
    sleeps = []
    assert poll_inbox(store, tmp_path / "missing.jsonl", current_snapshot_version=0,
                      wait_s=0.2, poll_s=0.1, monotonic_fn=lambda: next(times),
                      sleep_fn=sleeps.append) == ()
    assert sleeps == [0.1, 0.1]


def test_transaction_checks_actual_session_version_even_if_caller_snapshot_is_old(tmp_path):
    store, result = store_for(tmp_path), sample(tmp_path)
    store.append_event(session_id="s1", expected_version=0, event_type="NEW_STATE", payload={},
                       correlation_id="change", causation_id=None, idempotency_key="change")
    outcome = ingest_result(store, result, current_snapshot_version=0)
    assert not outcome.authorizes_current_state
    assert store.current_hypotheses("s1", snapshot_version=0) == ()


def test_current_hypotheses_disappear_when_session_moves_past_the_snapshot(tmp_path):
    store, result = store_for(tmp_path), sample(tmp_path)
    ingest_result(store, result, current_snapshot_version=0)
    store.append_event(session_id="s1", expected_version=0, event_type="NEW_STATE", payload={},
                       correlation_id="change", causation_id=None, idempotency_key="change")
    assert store.current_hypotheses("s1", snapshot_version=0) == ()
    assert not ingest_result(store, result, current_snapshot_version=0).authorizes_current_state


def test_inbox_rejects_duplicate_json_keys(tmp_path):
    store, result = store_for(tmp_path), sample(tmp_path)
    line = canonical_line(asdict(result)).decode()
    inbox = tmp_path / "duplicate-keys.jsonl"
    inbox.write_text(line.replace('"provider":"fixture"', '"provider":"ignored","provider":"fixture"'))
    assert poll_inbox(store, inbox, current_snapshot_version=0) == ()
    assert count(store, "room_inbox_rejections") == 1
    assert count(store, "room_observations") == 0


def test_migration_upgrades_real_version_three_database_without_rewriting_events(tmp_path, monkeypatch):
    from sentinel.room import store as module
    schema = module._SCHEMA.read_text(encoding="utf-8")
    old_schema = tmp_path / "v3-schema.sql"
    old_schema.write_text(schema[:schema.index("-- Perception evidence schema")] +
                          schema[schema.index("CREATE TRIGGER IF NOT EXISTS room_events_no_delete"):],
                          encoding="utf-8")
    path = tmp_path / "old.sqlite3"
    with monkeypatch.context() as context:
        context.setattr(module, "_MIGRATIONS", ((1, old_schema), *module._MIGRATIONS[1:3]))
        store = RoomStore.open(path)
    store.create_session("s1", "look", CapabilityProfile(()))
    event = store.append_event(session_id="s1", expected_version=0, event_type="OLD", payload={},
                               correlation_id="old", causation_id=None, idempotency_key="old")
    assert store.connection.execute("SELECT name FROM sqlite_master WHERE name='room_observations'").fetchone() is None
    store.close()
    upgraded = RoomStore.open(path)
    assert upgraded.events("s1") == (event,)
    result = sample(tmp_path, snapshot=1)
    assert ingest_result(upgraded, result, current_snapshot_version=1).authorizes_current_state
    assert upgraded.connection.execute("SELECT MAX(version) FROM room_schema_version").fetchone()[0] == 6


def test_unverified_payload_identity_cannot_be_replaced_through_null_index_alias(tmp_path):
    store, result = store_for(tmp_path), sample(tmp_path)
    failed = replace(result, status="FAILED", resolved_model=None, identity_status="UNVERIFIED",
                     detections=(), error_category="TIMEOUT")
    ingest_result(store, failed, current_snapshot_version=0)
    # Immutability also holds for a foreign SQLite connection without FK enforcement.
    with sqlite3.connect(tmp_path / "room.sqlite3") as foreign:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            foreign.execute(
                "INSERT OR REPLACE INTO room_perception_payloads "
                "SELECT 'replacement', provider, '', request_digest, frame_sha256, payload_json "
                "FROM room_perception_payloads",
            )


def test_mismatch_failure_is_persisted_and_polling_continues_then_replays(tmp_path):
    store, result = store_for(tmp_path), sample(tmp_path)
    mismatch = replace(result, status="FAILED", resolved_model="other-model", identity_status="MISMATCH",
                       detections=(), error_category="IDENTITY_MISMATCH")
    success = replace(result, request=replace(result.request, observation_id="o2"))
    inbox = tmp_path / "inbox.jsonl"
    append_inbox_result(inbox, mismatch)
    append_inbox_result(inbox, success)
    outcomes = poll_inbox(store, inbox, current_snapshot_version=0)
    assert len(outcomes) == 2
    assert not outcomes[0].authorizes_current_state and outcomes[0].hypothesis_ids == ()
    assert outcomes[1].authorizes_current_state and len(outcomes[1].hypothesis_ids) == 1
    row = store.connection.execute("SELECT * FROM room_perception_results WHERE observation_id='o1'").fetchone()
    assert (row["status"], row["identity_status"], row["resolved_model"], row["error_category"]) == (
        "FAILED", "MISMATCH", "other-model", "IDENTITY_MISMATCH",
    )
    assert count(store, "room_hypothesis_revisions") == 1
    assert all(outcome.duplicate for outcome in poll_inbox(store, inbox, current_snapshot_version=0))
    assert count(store, "room_perception_results") == 2


@pytest.mark.parametrize("mutation", [
    {"status": "SUCCEEDED"}, {"error_category": "SCHEMA"}, {"resolved_model": None},
    {"resolved_model": "pinned-model"}, {"detections": "original"},
])
def test_inconsistent_mismatch_is_rejected_without_persistence(tmp_path, mutation):
    store, result = store_for(tmp_path), sample(tmp_path)
    mismatch = replace(result, status="FAILED", resolved_model="other-model", identity_status="MISMATCH",
                       detections=(), error_category="IDENTITY_MISMATCH")
    if mutation.get("detections") == "original":
        mutation = {"detections": result.detections}
    with pytest.raises(InvalidPerception):
        ingest_result(store, replace(mismatch, **mutation), current_snapshot_version=0)
    assert count(store, "room_perception_results") == 0


def test_unverified_failure_payload_deduplicates_across_sessions_with_separate_links(tmp_path):
    store, result = store_for(tmp_path), sample(tmp_path)
    first = replace(result, status="FAILED", resolved_model=None, identity_status="UNVERIFIED",
                    detections=(), error_category="TIMEOUT")
    second = replace(first, request=replace(first.request, session_id="s2"))
    first_outcome = ingest_result(store, first, current_snapshot_version=0)
    second_outcome = ingest_result(store, second, current_snapshot_version=0)
    assert first_outcome.payload_id == second_outcome.payload_id
    assert first_outcome.result_id != second_outcome.result_id
    assert count(store, "room_perception_payloads") == 1
    rows = store.connection.execute("SELECT session_id, payload_id, resolved_model FROM room_perception_results ORDER BY session_id").fetchall()
    assert [(row["session_id"], row["payload_id"], row["resolved_model"]) for row in rows] == [
        ("s1", first_outcome.payload_id, None), ("s2", first_outcome.payload_id, None),
    ]
    assert count(store, "room_observations") == 2
    assert count(store, "room_hypothesis_revisions") == 0


@pytest.mark.parametrize("failed_upgrade", [None, "sql", "foreign_key"])
def test_v4_upgrade_retains_existing_results_evidence_and_foreign_keys(tmp_path, monkeypatch, failed_upgrade):
    from sentinel.room import store as module
    schema = module._SCHEMA.read_text(encoding="utf-8")
    legacy_schema = tmp_path / "old-schema.sql"
    legacy_schema.write_text(schema[:schema.index("-- Perception evidence schema")] +
                             schema[schema.index("CREATE TRIGGER IF NOT EXISTS room_events_no_delete"):],
                             encoding="utf-8")
    path = tmp_path / "v4.sqlite3"
    with monkeypatch.context() as context:
        context.setattr(module, "_MIGRATIONS", ((1, legacy_schema), *module._MIGRATIONS[1:4]))
        store = RoomStore.open(path)
    store.create_session("s1", "look", CapabilityProfile(()))
    original = sample(tmp_path)
    first = ingest_result(store, original, current_snapshot_version=0)
    before = {table: [tuple(row) for row in store.connection.execute(f"SELECT * FROM {table}")]
              for table in ("room_observations", "room_perception_results", "room_detection_evidence",
                            "room_object_hypotheses", "room_hypothesis_revisions")}
    if failed_upgrade:
        schema_before = store.connection.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall()
        broken = tmp_path / "broken-v5.sql"
        suffix = "\nTHIS IS INVALID SQL;\n" if failed_upgrade == "sql" else (
            "\nCREATE TABLE broken_reference (session_id TEXT REFERENCES room_sessions(session_id));\n"
            "INSERT INTO broken_reference VALUES ('missing');\n"
        )
        broken.write_text(module._MIGRATION_005.read_text(encoding="utf-8") + suffix, encoding="utf-8")
        with monkeypatch.context() as context:
            context.setattr(module, "_MIGRATIONS", (*module._MIGRATIONS[:4], (5, broken)))
            with pytest.raises(sqlite3.Error):
                module._run_migrations(store.connection)
        assert store.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert store.connection.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall() == schema_before
        assert store.connection.execute("SELECT MAX(version) FROM room_schema_version").fetchone()[0] == 4
        for table, rows in before.items():
            assert [tuple(row) for row in store.connection.execute(f"SELECT * FROM {table}")] == rows
    store.close()
    upgraded = RoomStore.open(path)
    for table, rows in before.items():
        assert [tuple(row) for row in upgraded.connection.execute(f"SELECT * FROM {table}")] == rows
    assert upgraded.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert upgraded.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert ingest_result(upgraded, original, current_snapshot_version=0).result_id == first.result_id
    mismatch = replace(original, request=replace(original.request, observation_id="o2"), status="FAILED",
                       resolved_model="other-model", identity_status="MISMATCH", detections=(),
                       error_category="IDENTITY_MISMATCH")
    assert not ingest_result(upgraded, mismatch, current_snapshot_version=0).authorizes_current_state
    assert upgraded.connection.execute("SELECT MAX(version) FROM room_schema_version").fetchone()[0] == 6


def test_exact_replay_after_frame_retention_expires_does_not_read_file(tmp_path):
    from pathlib import Path
    store, result = store_for(tmp_path), sample(tmp_path)
    first = ingest_result(store, result, current_snapshot_version=0)
    Path(result.request.frame_path).unlink()
    replay = ingest_result(store, result, current_snapshot_version=0)
    assert replay.duplicate and replay.result_id == first.result_id
    assert count(store, "room_detection_evidence") == 1
    with pytest.raises(IdempotencyConflict):
        ingest_result(store, replace(result, completed_at="2026-09-22T00:00:02+00:00"),
                      current_snapshot_version=0)


def test_poll_replays_history_without_retained_frame_then_ingests_new_delivery(tmp_path):
    from pathlib import Path
    store, result = store_for(tmp_path), sample(tmp_path)
    inbox = tmp_path / "inbox.jsonl"
    append_inbox_result(inbox, result)
    poll_inbox(store, inbox, current_snapshot_version=0)
    Path(result.request.frame_path).unlink()
    new_directory = tmp_path / "new"
    new_directory.mkdir()
    new_result = sample(new_directory, observation="o2")
    append_inbox_result(inbox, new_result)
    outcomes = poll_inbox(store, inbox, current_snapshot_version=0)
    assert len(outcomes) == 2 and outcomes[0].duplicate and not outcomes[1].duplicate
    assert count(store, "room_perception_results") == 2


def test_new_delivery_rechecks_identity_after_hash_validation_race(tmp_path, monkeypatch):
    import sentinel.room.ingest as module
    store, result = store_for(tmp_path), sample(tmp_path)
    other = RoomStore.open(tmp_path / "room.sqlite3")
    original_digest, delivered = module.frame_digest, []

    def interleaved_digest(path):
        assert not store.connection.in_transaction
        if not delivered:
            delivered.append(True)
            ingest_result(other, result, current_snapshot_version=0)
        return original_digest(path)

    monkeypatch.setattr(module, "frame_digest", interleaved_digest)
    assert ingest_result(store, result, current_snapshot_version=0).duplicate
    assert count(store, "room_perception_results") == 1
    other.close()


@pytest.mark.parametrize("bad_line,category", [
    (b'{"token":"never-persist-this-secret",\n', "JSON"),
    (b'\xff\n', "JSON"),
    (b'{"token":"never-persist-this-secret"}\n', "SCHEMA"),
    (b'\n', "SCHEMA"),
])
def test_rejected_record_does_not_block_valid_delivery_and_replays_after_restart(tmp_path, bad_line, category):
    store, result = store_for(tmp_path), sample(tmp_path)
    inbox = tmp_path / "never-persist-this-secret.jsonl"
    inbox.write_bytes(bad_line + canonical_line(asdict(result)))
    outcomes = poll_inbox(store, inbox, current_snapshot_version=0)
    assert len(outcomes) == 1 and not outcomes[0].duplicate
    receipt = dict(store.connection.execute("SELECT * FROM room_inbox_rejections").fetchone())
    assert receipt["category"] == category
    assert receipt["byte_offset"] == 0 and receipt["byte_length"] == len(bad_line)
    assert receipt["record_sha256"] == hashlib.sha256(bad_line).hexdigest()
    assert receipt["hashed_byte_length"] == len(bad_line)
    assert len(receipt["inbox_id"]) == len(receipt["rejection_id"]) == 64
    assert datetime.fromisoformat(receipt["rejected_at"]).tzinfo is not None
    assert receipt["monotonic_ms"] >= 0
    assert "never-persist-this-secret" not in str(receipt)
    for reopened in (False, True):
        if reopened:
            store.close()
            store = RoomStore.open(tmp_path / "room.sqlite3")
        replay = poll_inbox(store, inbox, current_snapshot_version=0)
        assert len(replay) == 1 and replay[0].duplicate
        assert [dict(row) for row in store.connection.execute("SELECT * FROM room_inbox_rejections")] == [receipt]
        assert count(store, "room_perception_results") == 1
    store.close()


def test_structurally_complete_invalid_envelope_is_rejected_before_valid_typed_failure(tmp_path):
    store, result = store_for(tmp_path), sample(tmp_path)
    invalid = replace(result, status="FAILED", error_category="private-error-secret")
    failure = replace(result, status="FAILED", resolved_model=None, identity_status="UNVERIFIED",
                      detections=(), error_category="TIMEOUT")
    inbox = tmp_path / "inbox.jsonl"
    inbox.write_bytes(canonical_line(asdict(invalid)) + canonical_line(asdict(failure)))
    outcomes = poll_inbox(store, inbox, current_snapshot_version=0)
    assert len(outcomes) == 1 and not outcomes[0].authorizes_current_state
    receipt = dict(store.connection.execute("SELECT * FROM room_inbox_rejections").fetchone())
    assert receipt["category"] == "SCHEMA" and "private-error-secret" not in str(receipt)
    assert count(store, "room_perception_results") == 1


def test_partial_poison_tail_is_not_rejected_until_newline_and_then_valid_row_continues(tmp_path):
    store, result = store_for(tmp_path), sample(tmp_path)
    inbox = tmp_path / "inbox.jsonl"
    inbox.write_bytes(b'{"token":"secret"')
    assert poll_inbox(store, inbox, current_snapshot_version=0) == ()
    assert count(store, "room_inbox_rejections") == 0
    store.close()
    store = RoomStore.open(tmp_path / "room.sqlite3")
    with inbox.open("ab") as handle:
        handle.write(b'\n' + canonical_line(asdict(result)))
    assert len(poll_inbox(store, inbox, current_snapshot_version=0)) == 1
    assert count(store, "room_inbox_rejections") == 1


def test_oversized_record_uses_bounded_reads_and_hashes_without_parsing(tmp_path, monkeypatch):
    from pathlib import Path
    import sentinel.room.ingest as module
    store, result = store_for(tmp_path), sample(tmp_path)
    maximum = getattr(module, "MAX_INBOX_RECORD_BYTES", 1024 * 1024)
    inbox = tmp_path / "inbox.jsonl"
    oversized = b'x' * (maximum * 3) + b'\n'
    inbox.write_bytes(oversized + canonical_line(asdict(result)))
    original_open, original_loads, original_hash = Path.open, module.json.loads, module.hashlib.sha256

    class BoundedReader:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.handle.close()
        def read(self, size=-1):
            assert 0 <= size <= maximum + 1
            return self.handle.read(size)
        def readline(self, size=-1):
            assert 0 <= size <= maximum + 1
            return self.handle.readline(size)
        def __getattr__(self, name):
            return getattr(self.handle, name)

    def open_bounded(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        return BoundedReader(handle) if path == inbox else handle

    def load_bounded(value, **kwargs):
        assert len(value) <= maximum
        return original_loads(value, **kwargs)

    def hash_bounded(value=b''):
        assert len(value) <= maximum
        return original_hash(value)

    monkeypatch.setattr(Path, "open", open_bounded)
    monkeypatch.setattr(module.json, "loads", load_bounded)
    monkeypatch.setattr(module.hashlib, "sha256", hash_bounded)
    assert len(poll_inbox(store, inbox, current_snapshot_version=0)) == 1
    receipt = store.connection.execute("SELECT * FROM room_inbox_rejections").fetchone()
    assert receipt["category"] == "RECORD_TOO_LARGE"
    assert receipt["byte_length"] == len(oversized)
    assert receipt["hashed_byte_length"] == maximum
    assert receipt["record_sha256"] == original_hash(oversized[:maximum]).hexdigest()
    assert len(poll_inbox(store, inbox, current_snapshot_version=0)) == 1
    assert count(store, "room_inbox_rejections") == 1


def test_oversized_partial_tail_waits_for_completion(tmp_path):
    import sentinel.room.ingest as module
    store = store_for(tmp_path)
    inbox = tmp_path / "inbox.jsonl"
    inbox.write_bytes(b'x' * (getattr(module, "MAX_INBOX_RECORD_BYTES", 1024 * 1024) + 1))
    assert poll_inbox(store, inbox, current_snapshot_version=0) == ()
    assert count(store, "room_inbox_rejections") == 0
    with inbox.open("ab") as handle:
        handle.write(b'\n')
    assert poll_inbox(store, inbox, current_snapshot_version=0) == ()
    assert count(store, "room_inbox_rejections") == 1


def test_rejection_receipts_are_immutable_including_replace_and_migrate_from_v5(tmp_path, monkeypatch):
    from sentinel.room import store as module
    path = tmp_path / "room.sqlite3"
    with monkeypatch.context() as context:
        context.setattr(module, "_MIGRATIONS", module._MIGRATIONS[:5])
        store = RoomStore.open(path)
    assert store.connection.execute("SELECT MAX(version) FROM room_schema_version").fetchone()[0] == 5
    store.close()
    store = RoomStore.open(path)
    inbox = tmp_path / "inbox.jsonl"
    inbox.write_bytes(b'bad\n')
    poll_inbox(store, inbox, current_snapshot_version=0)
    assert count(store, "room_inbox_rejections") == 1
    for sql in ("UPDATE room_inbox_rejections SET category = category", "DELETE FROM room_inbox_rejections",
                "INSERT OR REPLACE INTO room_inbox_rejections SELECT * FROM room_inbox_rejections"):
        with sqlite3.connect(path) as foreign:
            with pytest.raises(sqlite3.IntegrityError, match="append-only|immutable"):
                foreign.execute(sql)


def test_bounded_record_poll_resumes_across_restart_and_atomic_writer_append(tmp_path, monkeypatch):
    import sentinel.room.ingest as module
    monkeypatch.setattr(module, "MAX_INBOX_POLL_RECORDS", 2, raising=False)
    store, result = store_for(tmp_path), sample(tmp_path)
    inbox = tmp_path / "inbox.jsonl"
    for index in range(3):
        append_inbox_result(inbox, replace(result, request=replace(result.request, observation_id=f"o{index}")))
    first = poll_inbox(store, inbox, current_snapshot_version=0)
    assert len(first) == 2 and all(not item.duplicate for item in first)
    store.close()
    append_inbox_result(inbox, replace(result, request=replace(result.request, observation_id="o3")))
    store = RoomStore.open(tmp_path / "room.sqlite3")
    second = poll_inbox(store, inbox, current_snapshot_version=0)
    assert len(second) == 2 and all(not item.duplicate for item in second)
    assert count(store, "room_perception_results") == 4
    assert all(item.duplicate for item in poll_inbox(store, inbox, current_snapshot_version=0))


def test_huge_record_discard_is_byte_bounded_and_resumes_after_restart(tmp_path, monkeypatch):
    from pathlib import Path
    import sentinel.room.ingest as module
    store, result = store_for(tmp_path), sample(tmp_path)
    budget = getattr(module, "MAX_INBOX_POLL_BYTES", 4 * 1024 * 1024)
    inbox = tmp_path / "private-path-secret.jsonl"
    oversized = b'private-row-secret' + b'x' * (2 * budget) + b'\n'
    inbox.write_bytes(oversized + canonical_line(asdict(result)))
    original_open = Path.open
    bytes_read = [0]

    class CountedReader:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.handle.close()
        def read(self, size=-1):
            data = self.handle.read(size)
            bytes_read[0] += len(data)
            assert bytes_read[0] <= budget
            return data
        def readline(self, size=-1):
            data = self.handle.readline(size)
            bytes_read[0] += len(data)
            assert bytes_read[0] <= budget
            return data
        def __getattr__(self, name):
            return getattr(self.handle, name)

    def open_counted(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        return CountedReader(handle) if path == inbox else handle

    monkeypatch.setattr(Path, "open", open_counted)
    assert poll_inbox(store, inbox, current_snapshot_version=0) == ()
    assert count(store, "room_inbox_rejections") == 0
    progress = dict(store.connection.execute("SELECT * FROM room_inbox_cursors").fetchone())
    assert 0 < progress["scan_offset"] < len(oversized)
    assert "private-row-secret" not in str(progress) and "private-path-secret" not in str(progress)
    store.close()
    store = RoomStore.open(tmp_path / "room.sqlite3")
    all_outcomes = []
    for _ in range(3):
        bytes_read[0] = 0
        all_outcomes.extend(poll_inbox(store, inbox, current_snapshot_version=0))
        if all_outcomes:
            break
    assert len(all_outcomes) == 1 and not all_outcomes[0].duplicate
    assert count(store, "room_inbox_rejections") == 1
    receipt = store.connection.execute("SELECT * FROM room_inbox_rejections").fetchone()
    assert receipt["byte_length"] == len(oversized)
    for _ in range(4):
        bytes_read[0] = 0
        poll_inbox(store, inbox, current_snapshot_version=0)
    assert count(store, "room_inbox_rejections") == 1


@pytest.mark.parametrize("replacement", ["truncated", "divergent_prefix"])
def test_cursor_resets_idempotently_on_truncation_or_replacement(tmp_path, monkeypatch, replacement):
    import sentinel.room.ingest as module
    monkeypatch.setattr(module, "MAX_INBOX_POLL_RECORDS", 1, raising=False)
    store, result = store_for(tmp_path), sample(tmp_path)
    inbox = tmp_path / "inbox.jsonl"
    line = canonical_line(asdict(result))
    inbox.write_bytes(line + line)
    assert len(poll_inbox(store, inbox, current_snapshot_version=0)) == 1
    new = canonical_line(asdict(replace(result, request=replace(result.request, observation_id="o2"))))
    inbox.write_bytes(new if replacement == "truncated" else new + line)
    outcomes = poll_inbox(store, inbox, current_snapshot_version=0)
    assert len(outcomes) == 1 and not outcomes[0].duplicate
    assert count(store, "room_perception_results") == 2


def test_partial_huge_tail_resumes_after_atomic_append_without_receipt_until_complete(tmp_path):
    import sentinel.room.ingest as module
    store, result = store_for(tmp_path), sample(tmp_path)
    inbox = tmp_path / "inbox.jsonl"
    budget = getattr(module, "MAX_INBOX_POLL_BYTES", 4 * 1024 * 1024)
    tail = b'x' * (budget + 1)
    inbox.write_bytes(tail)
    assert poll_inbox(store, inbox, current_snapshot_version=0) == ()
    assert count(store, "room_inbox_rejections") == 0
    cursor = dict(store.connection.execute("SELECT * FROM room_inbox_cursors").fetchone())
    assert 0 < cursor["scan_offset"] < len(tail)
    assert poll_inbox(store, inbox, current_snapshot_version=0) == ()
    assert count(store, "room_inbox_rejections") == 0
    store.close()
    # Complete the trailing line using the producer's atomic replacement pattern.
    replacement = tmp_path / "replacement.jsonl"
    replacement.write_bytes(tail + b'\n' + canonical_line(asdict(result)))
    replacement.replace(inbox)
    store = RoomStore.open(tmp_path / "room.sqlite3")
    assert len(poll_inbox(store, inbox, current_snapshot_version=0)) == 1
    assert count(store, "room_inbox_rejections") == 1


def test_resumed_oversized_record_revalidates_its_hashed_prefix_on_replacement(tmp_path):
    import sentinel.room.ingest as module
    store = store_for(tmp_path)
    inbox = tmp_path / "inbox.jsonl"
    maximum = module.MAX_INBOX_RECORD_BYTES
    oversized = bytearray(b'x' * (module.MAX_INBOX_POLL_BYTES + 1) + b'\n')
    inbox.write_bytes(oversized)
    assert poll_inbox(store, inbox, current_snapshot_version=0) == ()
    # Change the stored digest's input outside the small initial/checkpoint
    # fingerprints, keeping the file length and cursor checkpoint unchanged.
    oversized[maximum // 2] = ord('y')
    replacement = tmp_path / "replacement.jsonl"
    replacement.write_bytes(oversized)
    replacement.replace(inbox)
    store.close()
    store = RoomStore.open(tmp_path / "room.sqlite3")
    for _ in range(3):
        poll_inbox(store, inbox, current_snapshot_version=0)
        if count(store, "room_inbox_rejections"):
            break
    receipt = store.connection.execute("SELECT * FROM room_inbox_rejections").fetchone()
    assert receipt["record_sha256"] == hashlib.sha256(oversized[:maximum]).hexdigest()
