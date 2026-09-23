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


def test_append_current_keeps_assessment_version_and_recovers_stable_retry(tmp_path):
    store = _store(tmp_path)
    _append(store)
    values = dict(session_id="session-1", assessed_snapshot_version=0,
        event_type="SAFETY_ASSESSED", payload={"transition": "LAND_CONTINGENCY"},
        correlation_id="safety", causation_id=None, idempotency_key="safety")
    first = store.append_event_current(**values)
    assert first.payload["assessed_snapshot_version"] == 0
    assert first.payload["append_version"] == 1
    _append(store, expected_version=2, key="unrelated")
    assert store.append_event_current(**values) == first
    assert store.session_version("session-1") == 3
    with pytest.raises(IdempotencyConflict):
        store.append_event_current(**{**values, "payload": {"transition": "RETURN_NOW"}})


def test_ordinary_append_cannot_omit_snapshot_authorization(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(VersionConflict):
        _append(store, expected_version=None)
    assert store.events("session-1") == ()


def test_append_current_serializes_repeated_independent_writers_and_retries(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store = _store(tmp_path)
    second = RoomStore.open(tmp_path / "room.sqlite3")
    barrier = threading.Barrier(2)

    def writer(connection, prefix):
        barrier.wait()
        for index in range(8):
            key = f"{prefix}:{index}"
            values = dict(session_id="session-1", assessed_snapshot_version=0,
                event_type="SAFETY_ASSESSED", payload={"writer": prefix, "index": index},
                correlation_id=key, causation_id=None, idempotency_key=key)
            event = connection.append_event_current(**values)
            assert connection.append_event_current(**values) == event

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = [pool.submit(writer, connection, prefix) for connection, prefix in ((store, "a"), (second, "b"))]
            for future in pending:
                future.result(timeout=10)
        events = store.events("session-1")
        assert len(events) == store.session_version("session-1") == 16
        assert all(e.payload["append_version"] == e.state_version - 1 for e in events)
        assert all(e.payload["assessed_snapshot_version"] == 0 for e in events)
    finally:
        second.close()
        store.close()


def test_contingency_assurances_are_append_only_session_bound_and_idempotent(tmp_path):
    from dataclasses import replace
    from tests.cube.test_recovery import assurance
    store = _store(tmp_path)
    current = assurance(session_id="session-1")
    first = store.append_contingency_assurance(current, expected_version=0)
    assert store.append_contingency_assurance(current, expected_version=1) == first
    with pytest.raises(IdempotencyConflict):
        store.append_contingency_assurance(replace(current, expires_monotonic_ms=99999), expected_version=1)
    revoked = replace(current, state="REVOKED")
    store.append_contingency_assurance(revoked, expected_version=1)
    assert store.contingency_assurances("session-1", clock_domain_id="clock") == (current, revoked)
    assert store.contingency_assurances("session-1", clock_domain_id="other") == ()
    store.close()
    reopened = RoomStore.open(tmp_path / "room.sqlite3")
    assert reopened.contingency_assurances("session-1", clock_domain_id="clock") == (current, revoked)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        reopened.connection.execute("DELETE FROM room_events WHERE event_type = 'CONTINGENCY_ASSURED'")


def test_reanchor_and_invalidation_persistence_is_one_atomic_idempotent_batch(tmp_path):
    from tests.cube.test_recovery import controller, environment, reports
    store = _store(tmp_path)
    recovery, value, clock = controller()
    env = environment()
    from dataclasses import replace
    env = replace(env, session_id="session-1")
    action = recovery.next_action(value.health, env, ())
    outcome = recovery.assess_burst(action, reports(clock))
    events = store.append_reanchor(outcome, expected_version=0)
    assert len(events) == 4
    assert store.append_reanchor(outcome, expected_version=4) == events
    assert [event.event_type for event in events] == [
        "POSE_REANCHORED", "ROUTES_INVALIDATED", "CANDIDATES_INVALIDATED", "TARGET_LOCKS_INVALIDATED"]
    assert events[0].payload["requires_route_validation"]
    assert events[0].payload["requires_energy_validation"]
    for event in events[1:]:
        assert event.payload["pose_revision"] == events[0].payload["pose_revision"]
    assert store.session_version("session-1") == 4


def test_reanchor_failed_append_rolls_back_every_invalidation(tmp_path):
    from dataclasses import replace
    from tests.cube.test_recovery import controller, environment, reports
    store = _store(tmp_path)
    recovery, value, clock = controller()
    action = recovery.next_action(value.health, replace(environment(), session_id="session-1"), ())
    outcome = recovery.assess_burst(action, reports(clock))
    store.connection.execute("CREATE TRIGGER fail_invalidation BEFORE INSERT ON room_events "
        "WHEN NEW.event_type = 'CANDIDATES_INVALIDATED' "
        "BEGIN SELECT RAISE(ABORT, 'forced failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="forced failure"):
        store.append_reanchor(outcome, expected_version=0)
    assert store.events("session-1") == ()
    assert store.session_version("session-1") == 0


def test_failed_reanchor_persistence_never_resets_tracker_before_atomic_commit(tmp_path):
    from dataclasses import replace
    from sentinel.cube.recovery import RecoveryController
    from tests.cube.test_recovery import environment, reports
    from tests.cube.test_localization import command, tracker
    store = _store(tmp_path)
    value, clock = tracker()
    value.note_command(command(direction="right", distance_cm=20), 10)
    before = value.health
    recovery = RecoveryController(value, clock=clock, burst_source=lambda action: reports(clock),
        energy_admits=lambda action: True, commit_reanchor=lambda result: store.append_reanchor(result, expected_version=0))
    action = recovery.next_action(value.health, replace(environment(), session_id="session-1"), ())
    store.connection.execute("CREATE TRIGGER fail_reanchor BEFORE INSERT ON room_events "
        "WHEN NEW.event_type = 'CANDIDATES_INVALIDATED' "
        "BEGIN SELECT RAISE(ABORT, 'forced failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="forced failure"):
        recovery.assess_burst(action, reports(clock))
    assert value.health == before
    assert store.events("session-1") == ()
    store.connection.execute("DROP TRIGGER fail_reanchor")
    result = recovery.assess_burst(action, reports(clock))
    assert result.accepted and value.health.translation_cm == 0
    assert len(store.events("session-1")) == 4


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
        (version,) for version, _ in room_store_module._MIGRATIONS
    ]


def test_invalid_multi_statement_migration_rolls_back_all_ddl_and_version_rows(tmp_path, monkeypatch):
    path = tmp_path / "migration.sqlite3"
    RoomStore.open(path).close()
    broken = tmp_path / "broken_migration.sql"
    broken.write_text(
        "CREATE TABLE should_not_survive (value TEXT);\nTHIS IS INVALID SQL;\n",
        encoding="utf-8",
    )
    original_versions = [(version,) for version, _ in room_store_module._MIGRATIONS]
    monkeypatch.setattr(
        room_store_module,
        "_MIGRATIONS",
        room_store_module._MIGRATIONS + ((original_versions[-1][0] + 1, broken),),
    )
    connection = sqlite3.connect(path)
    with pytest.raises(sqlite3.OperationalError):
        room_store_module._run_migrations(connection)
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'should_not_survive'",
    ).fetchone() is None
    assert connection.execute("SELECT version FROM room_schema_version ORDER BY version").fetchall() == original_versions


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


@pytest.mark.parametrize("subset", [slice(0, 1), slice(1, 2), slice(None, None, -1)])
def test_batch_replay_rejects_prefix_suffix_and_reordering(tmp_path, subset):
    store = _store(tmp_path)
    batch = _batch()
    original = store.append_events(session_id="session-1", expected_version=0, events=batch)
    with pytest.raises(IdempotencyConflict, match="batch"):
        store.append_events(session_id="session-1", expected_version=2, events=batch[subset])
    assert store.events("session-1") == original
    assert store.session_version("session-1") == 2


def test_adjacent_atomic_batches_cannot_be_replayed_as_one_batch(tmp_path):
    store = _store(tmp_path)
    first = _batch()
    second = tuple({**event, "idempotency_key": "second:" + event["idempotency_key"]} for event in _batch())
    a = store.append_events(session_id="session-1", expected_version=0, events=first)
    b = store.append_events(session_id="session-1", expected_version=2, events=second)
    with pytest.raises(IdempotencyConflict, match="batch"):
        store.append_events(session_id="session-1", expected_version=4, events=first + second)
    assert store.events("session-1") == a + b
    assert store.session_version("session-1") == 4


def test_batch_metadata_records_digest_count_and_order_and_is_append_only(tmp_path):
    store = _store(tmp_path)
    events = store.append_events(session_id="session-1", expected_version=0, events=_batch())
    batch = store.connection.execute("SELECT * FROM room_event_batches").fetchone()
    assert batch["batch_id"]
    assert batch["session_id"] == "session-1"
    assert len(batch["batch_sha256"]) == 64
    assert batch["event_count"] == 2
    members = store.connection.execute(
        "SELECT event_id, ordinal FROM room_event_batch_members WHERE batch_id = ? ORDER BY ordinal",
        (batch["batch_id"],),
    ).fetchall()
    assert [(row["event_id"], row["ordinal"]) for row in members] == [
        (event.event_id, index) for index, event in enumerate(events)
    ]
    for table in ("room_event_batches", "room_event_batch_members"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store.connection.execute(f"UPDATE {table} SET batch_id = 'changed'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store.connection.execute(f"DELETE FROM {table}")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            store.connection.execute(f"INSERT OR REPLACE INTO {table} SELECT * FROM {table}")
    store.connection.rollback()  # finish the transaction opened by direct mutation probes
    assert store.append_events(session_id="session-1", expected_version=2, events=_batch()) == events


def test_failed_batch_has_no_metadata_or_members(tmp_path):
    store = _store(tmp_path)
    store.connection.execute(
        "CREATE TRIGGER fail_batch_member BEFORE INSERT ON room_event_batch_members "
        "WHEN NEW.ordinal = 1 BEGIN SELECT RAISE(ABORT, 'forced membership failure'); END",
    )
    with pytest.raises(sqlite3.IntegrityError, match="forced membership failure"):
        store.append_events(session_id="session-1", expected_version=0, events=_batch())
    assert store.events("session-1") == ()
    assert store.session_version("session-1") == 0
    assert store.connection.execute("SELECT COUNT(*) FROM room_event_batches").fetchone()[0] == 0
    assert store.connection.execute("SELECT COUNT(*) FROM room_event_batch_members").fetchone()[0] == 0


def test_batch_identity_survives_reopen_and_backup(tmp_path):
    store = _store(tmp_path)
    events = store.append_events(session_id="session-1", expected_version=0, events=_batch())
    backup = tmp_path / "batch-backup.sqlite3"
    store.snapshot_to(backup)
    store.close()
    for path in (tmp_path / "room.sqlite3", backup):
        reopened = RoomStore.open(path)
        assert reopened.append_events(session_id="session-1", expected_version=0, events=_batch()) == events
        with pytest.raises(IdempotencyConflict):
            reopened.append_events(session_id="session-1", expected_version=2, events=_batch()[:1])
        reopened.close()


def test_migration_preserves_legacy_singleton_idempotency_without_inventing_batch_boundaries(tmp_path, monkeypatch):
    source = _store(tmp_path)
    first = _append(source)
    second = _append(source, expected_version=1, key="key-2", payload={"goal": "find"})
    source.close()
    legacy_path = tmp_path / "legacy.sqlite3"
    with monkeypatch.context() as legacy:
        legacy.setattr(room_store_module, "_MIGRATIONS", room_store_module._MIGRATIONS[:2])
        store = RoomStore.open(legacy_path)
    store.create_session("session-1", "count computers", CapabilityProfile(()))
    for event in (first, second):
        store.connection.execute(
            "INSERT INTO room_events "
            "(sequence_no,event_id,session_id,state_version,event_type,correlation_id,causation_id,"
            "idempotency_key,event_sha256,payload_json,wall_time,monotonic_ms) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (event.sequence_no, event.event_id, event.session_id, event.state_version, event.event_type,
             event.correlation_id, event.causation_id, event.idempotency_key, event.event_sha256,
             room_store_module._canonical_payload(event.payload), event.wall_time, event.monotonic_ms),
        )
    store.connection.execute("UPDATE room_sessions SET state_version = 2 WHERE session_id = 'session-1'")
    store.connection.commit()
    store.close()
    migrated = RoomStore.open(legacy_path)
    assert migrated.events("session-1") == (first, second)
    assert _append(migrated, expected_version=2) == first
    assert _append(migrated, expected_version=2, key="key-2", payload={"goal": "find"}) == second
    rows = migrated.connection.execute("SELECT event_count FROM room_event_batches").fetchall()
    assert [row["event_count"] for row in rows] == [1, 1]
    regrouped = tuple({
        "event_type": event.event_type, "payload": event.payload, "correlation_id": event.correlation_id,
        "causation_id": event.causation_id, "idempotency_key": event.idempotency_key,
    } for event in (first, second))
    with pytest.raises(IdempotencyConflict, match="batch"):
        migrated.append_events(session_id="session-1", expected_version=2, events=regrouped)
