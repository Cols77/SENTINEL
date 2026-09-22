import sqlite3

import pytest

from sentinel.room.models import CapabilityProfile
from sentinel.room.store import (
    ForbiddenPayload,
    IdempotencyConflict,
    RoomStore,
    VersionConflict,
)
from sentinel.survey.store import SurveyStore


def _store(tmp_path):
    store = RoomStore.open(tmp_path / "room.sqlite3")
    store.create_session("session-1", "count computers", CapabilityProfile(()))
    return store


def _append(store, *, expected_version=0, key="key-1", payload=None):
    return store.append_event(
        session_id="session-1", expected_version=expected_version,
        event_type="GOAL_ACCEPTED", payload=payload or {"goal": "count"},
        correlation_id="correlation-1", causation_id="cause-1", idempotency_key=key,
    )


def test_append_increments_version_atomically_and_preserves_correlation_fields(tmp_path):
    store = _store(tmp_path)
    event = _append(store)
    assert (event.sequence_no, event.state_version) == (1, 1)
    assert event.correlation_id == "correlation-1"
    assert event.causation_id == "cause-1"
    assert store.session_version("session-1") == 1
    with pytest.raises(VersionConflict):
        _append(store, expected_version=0, key="key-2")
    assert store.events("session-1") == (event,)


def test_duplicate_delivery_returns_original_event_but_different_digest_is_rejected(tmp_path):
    store = _store(tmp_path)
    original = _append(store)
    assert _append(store, expected_version=1) == original
    with pytest.raises(IdempotencyConflict):
        _append(store, expected_version=1, payload={"goal": "find"})
    assert store.events("session-1") == (original,)


def test_event_rows_are_immutable_and_keys_are_pinned_in_sql(tmp_path):
    store = _store(tmp_path)
    event = _append(store)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.connection.execute("UPDATE room_events SET event_type = 'MUTATED'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.connection.execute("DELETE FROM room_events WHERE event_id = ?", (event.event_id,))
    table = store.connection.execute("PRAGMA table_info(room_events)").fetchall()
    assert {row["name"] for row in table if row["pk"]} == {"session_id", "sequence_no"}
    indexes = store.connection.execute("PRAGMA index_list(room_events)").fetchall()
    assert any(row["unique"] for row in indexes)


@pytest.mark.parametrize("payload", [
    {"authorization": "Bearer secret"},
    {"nested": {"credentials": "secret"}},
    {"frame": b"raw image bytes"},
])
def test_payload_boundary_rejects_secrets_and_raw_bytes_without_partial_append(tmp_path, payload):
    store = _store(tmp_path)
    with pytest.raises(ForbiddenPayload):
        _append(store, payload=payload)
    assert store.events("session-1") == ()
    assert store.session_version("session-1") == 0


def test_event_stream_replays_identically_after_reopen_and_sqlite_backup(tmp_path):
    source = tmp_path / "room.sqlite3"
    store = RoomStore.open(source)
    store.create_session("session-1", "count computers", CapabilityProfile(()))
    first = _append(store)
    second = _append(store, expected_version=1, key="key-2", payload={"goal": "answer"})
    backup = tmp_path / "room-snapshot.sqlite3"
    store.snapshot_to(backup)
    store.close()
    reopened = RoomStore.open(source)
    copied = RoomStore.open(backup)
    assert reopened.events("session-1") == (first, second)
    assert copied.events("session-1", after_sequence=1) == (second,)
    assert copied.session_version("session-1") == 2


def test_room_and_survey_stores_share_a_database_through_separate_wal_connections(tmp_path):
    path = tmp_path / "shared.sqlite3"
    room = RoomStore.open(path)
    survey = SurveyStore.open(path)
    room.create_session("session-1", "count computers", CapabilityProfile(()))
    survey.create_run("run-1", "count computers")
    _append(room)
    assert room.connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert survey.connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert survey.flights_used("run-1") == 0
