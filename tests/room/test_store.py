import sqlite3
from dataclasses import dataclass
import threading

import pytest

from sentinel.room import store as room_store_module
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


@dataclass(frozen=True)
class _PayloadEnvelope:
    values: object


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
    unique_columns = {
        tuple(column["name"] for column in store.connection.execute(
            f"PRAGMA index_info({row['name']})",
        ).fetchall())
        for row in indexes if row["unique"]
    }
    assert ("event_id",) in unique_columns
    assert ("session_id", "idempotency_key") in unique_columns


@pytest.mark.parametrize("payload", [
    {"authorization": "Bearer secret"},
    {"nested": {"credentials": "secret"}},
    {"nested": {"API-Key": "secret"}},
    {"nested": [{"password": "secret"}]},
    {"nested": _PayloadEnvelope({"access_token": "secret"})},
    {"nested": {"envelope": _PayloadEnvelope([{"refresh-token": "secret"}])}},
    {"nested": [{"client_secret": "secret"}]},
    {"nested": _PayloadEnvelope({"Bearer Token": "secret"})},
    {"nested": {"Authorization-Header": "Bearer secret"}},
    {"frame": b"raw image bytes"},
    {"nested": [_PayloadEnvelope({"authorization_header": "Bearer secret"})]},
    {"nested": {"envelope": _PayloadEnvelope([b"raw image bytes"])}},
    {"nested": {"drone_credentials": "secret"}},
    {"nested": [{"api_key_value": "secret"}]},
    {"nested": _PayloadEnvelope({"auth_token": "secret"})},
    {"nested": {"secret-key": "secret"}},
    {"headers": [{"name": "Authorization", "value": "Bearer secret"}]},
    {"headers": [["API-Key", "secret"]]},
    {"api_key_value_description": "secret"},
    {"authorization_header_value_digest": "Bearer secret"},
    {"headers": [{"key": "Authorization", "value": "Bearer secret"}]},
    {"headers": [{"header": "API-Key", "value": "key-123"}]},
    {"request_headers": {"trace": "secret"}},
    {"response_headers": [{"name": "X-Trace", "value": "secret"}]},
    {"authorization_digest": "not-a-64-character-digest"},
    {"primary_api_key_value": "secret"},
    {"drone_auth_token_value": "secret"},
    {"provider_headers": {"name": "X-Trace", "value": "secret"}},
    {"token_count": "bearer-456"},
    {"token_count": -1},
    {"token_count": True},
])
def test_payload_boundary_rejects_secrets_and_raw_bytes_without_partial_append(tmp_path, payload):
    store = _store(tmp_path)
    with pytest.raises(ForbiddenPayload):
        _append(store, payload=payload)
    assert store.events("session-1") == ()
    assert store.session_version("session-1") == 0
    assert "secret" not in "\n".join(
        [row[0] for row in store.connection.execute("SELECT payload_json FROM room_events")],
    )


def test_payload_boundary_keeps_safe_descriptive_fields(tmp_path):
    store = _store(tmp_path)
    event = _append(store, payload={
        "api_key_description": "key rotation is managed elsewhere",
        "authorization_digest": "a" * 64,
        "password_policy": "minimum length 16",
        "token_count": 2,
        "credential_reference": "vault://credential-1",
        "authorization_digest": "b" * 64,
    })
    assert event.state_version == 1
    assert event.payload["token_count"] == 2


def test_concurrent_first_open_applies_each_migration_once(tmp_path):
    path = tmp_path / "first-open.sqlite3"
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def open_store() -> None:
        try:
            barrier.wait()
            store = RoomStore.open(path)
            store.close()
        except BaseException as exc:  # captured so assertions remain in this test thread
            errors.append(exc)

    workers = [threading.Thread(target=open_store) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)
    assert not any(worker.is_alive() for worker in workers)
    assert errors == []
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT version FROM room_schema_version ORDER BY version").fetchall() == [
        (1,), (2,),
    ]


def test_invalid_multi_statement_migration_rolls_back_all_ddl_and_version_rows(tmp_path, monkeypatch):
    path = tmp_path / "migration.sqlite3"
    RoomStore.open(path).close()
    broken = tmp_path / "broken_migration.sql"
    broken.write_text(
        "CREATE TABLE should_not_survive (value TEXT);\nTHIS IS INVALID SQL;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        room_store_module,
        "_MIGRATIONS",
        room_store_module._MIGRATIONS + ((3, broken),),
    )
    connection = sqlite3.connect(path)
    with pytest.raises(sqlite3.OperationalError):
        room_store_module._run_migrations(connection)
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'should_not_survive'",
    ).fetchone() is None
    assert connection.execute("SELECT version FROM room_schema_version ORDER BY version").fetchall() == [
        (1,), (2,),
    ]


def test_same_store_instance_supports_cross_thread_append_read_and_close(tmp_path):
    store = _store(tmp_path)
    result: list[object] = []

    def append_then_read() -> None:
        result.append(_append(store))
        result.append(store.events("session-1"))

    worker = threading.Thread(target=append_then_read)
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert len(result) == 2
    assert result[1] == (result[0],)

    closer = threading.Thread(target=store.close)
    closer.start()
    closer.join(timeout=5)
    assert not closer.is_alive()
    with pytest.raises(RuntimeError, match="closed"):
        store.events("session-1")


def test_session_update_failure_after_insert_rolls_back_the_event_and_allows_next_append(tmp_path):
    store = _store(tmp_path)
    store.connection.execute(
        "CREATE TRIGGER fail_room_session_update BEFORE UPDATE ON room_sessions "
        "BEGIN SELECT RAISE(ABORT, 'forced update failure'); END",
    )
    with pytest.raises(sqlite3.IntegrityError, match="forced update failure"):
        _append(store)
    assert store.events("session-1") == ()
    assert store.session_version("session-1") == 0
    store.connection.execute("DROP TRIGGER fail_room_session_update")
    assert _append(store).state_version == 1


def test_insert_or_replace_cannot_replace_existing_event_identity_or_idempotency_key(tmp_path):
    path = tmp_path / "room.sqlite3"
    store = RoomStore.open(path)
    store.create_session("session-1", "count computers", CapabilityProfile(()))
    original = _append(store)
    foreign = sqlite3.connect(path)
    values = (
        original.sequence_no, original.event_id, original.session_id, original.state_version,
        "MUTATED", original.correlation_id, original.causation_id, original.idempotency_key,
        original.event_sha256, "{}", original.wall_time, original.monotonic_ms,
    )
    with pytest.raises(sqlite3.IntegrityError, match="identity is immutable"):
        foreign.execute(
            "INSERT OR REPLACE INTO room_events "
            "(sequence_no,event_id,session_id,state_version,event_type,correlation_id,causation_id,"
            "idempotency_key,event_sha256,payload_json,wall_time,monotonic_ms) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", values,
        )
    duplicate_key_values = (
        original.sequence_no + 1, "different-event-id", original.session_id,
        original.state_version + 1, "MUTATED", original.correlation_id,
        original.causation_id, original.idempotency_key, original.event_sha256,
        "{}", original.wall_time, original.monotonic_ms,
    )
    with pytest.raises(sqlite3.IntegrityError, match="identity is immutable"):
        foreign.execute(
            "INSERT OR REPLACE INTO room_events "
            "(sequence_no,event_id,session_id,state_version,event_type,correlation_id,causation_id,"
            "idempotency_key,event_sha256,payload_json,wall_time,monotonic_ms) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", duplicate_key_values,
        )
    assert store.events("session-1") == (original,)


def test_event_id_is_global_while_idempotency_key_is_scoped_to_its_session(tmp_path):
    path = tmp_path / "room.sqlite3"
    store = RoomStore.open(path)
    store.create_session("session-1", "count computers", CapabilityProfile(()))
    store.create_session("session-2", "find computers", CapabilityProfile(()))
    first = _append(store)
    second = store.append_event(
        session_id="session-2", expected_version=0, event_type="GOAL_ACCEPTED",
        payload={"goal": "find"}, correlation_id="correlation-2", causation_id=None,
        idempotency_key=first.idempotency_key,
    )
    foreign = sqlite3.connect(path)
    with pytest.raises(sqlite3.IntegrityError, match="identity is immutable"):
        foreign.execute(
            "INSERT INTO room_events "
            "(sequence_no,event_id,session_id,state_version,event_type,correlation_id,causation_id,"
            "idempotency_key,event_sha256,payload_json,wall_time,monotonic_ms) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (2, first.event_id, "session-2", 2, "MUTATED", "c", None, "different-key",
             "0" * 64, "{}", second.wall_time, second.monotonic_ms),
        )
    assert store.events("session-1") == (first,)
    assert store.events("session-2") == (second,)


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


def _batch():
    return tuple({
        "event_type": kind, "payload": {"index": index},
        "correlation_id": "compile-1", "causation_id": None,
        "idempotency_key": f"compile-1:{index}",
    } for index, kind in enumerate(("GOAL_CHOICE_ATTEMPT", "GOAL_LEDGER_ACCEPTED")))


def test_batch_is_contiguous_and_duplicate_delivery_recovers_every_original_event(tmp_path):
    store = _store(tmp_path)
    batch = _batch()
    events = store.append_events(session_id="session-1", expected_version=0, events=batch)
    assert [event.state_version for event in events] == [1, 2]
    assert [event.sequence_no for event in events] == [1, 2]
    assert store.session_version("session-1") == 2
    assert store.append_events(session_id="session-1", expected_version=0, events=batch) == events
    altered = (batch[0], {**batch[1], "payload": {"index": 99}})
    with pytest.raises(IdempotencyConflict):
        store.append_events(session_id="session-1", expected_version=2, events=altered)
    assert store.events("session-1") == events


def test_batch_rolls_back_earlier_events_when_last_insert_fails(tmp_path):
    store = _store(tmp_path)
    store.connection.execute(
        "CREATE TRIGGER fail_last_event BEFORE INSERT ON room_events "
        "WHEN NEW.event_type = 'GOAL_LEDGER_ACCEPTED' "
        "BEGIN SELECT RAISE(ABORT, 'forced last event failure'); END",
    )
    with pytest.raises(sqlite3.IntegrityError, match="forced last event failure"):
        store.append_events(session_id="session-1", expected_version=0, events=_batch())
    assert store.events("session-1") == ()
    assert store.session_version("session-1") == 0


def test_batch_rejects_partial_replay_duplicate_keys_and_stale_version_without_writes(tmp_path):
    store = _store(tmp_path)
    batch = _batch()
    first = store.append_event(session_id="session-1", expected_version=0, **batch[0])
    with pytest.raises(IdempotencyConflict):
        store.append_events(session_id="session-1", expected_version=1, events=batch)
    with pytest.raises(IdempotencyConflict):
        store.append_events(session_id="session-1", expected_version=1, events=(batch[1], batch[1]))
    with pytest.raises(VersionConflict):
        store.append_events(session_id="session-1", expected_version=0, events=(batch[1],))
    assert store.events("session-1") == (first,)
