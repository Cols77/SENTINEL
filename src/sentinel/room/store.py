"""SQLite persistence boundary for append-only room-session state."""
from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Mapping, Sequence
import uuid

from .models import CapabilityProfile

_SCHEMA = Path(__file__).with_name("sql") / "schema.sql"
_MIGRATION_002 = Path(__file__).with_name("sql") / "migration_002_event_insert_guard.sql"
_MIGRATION_003 = Path(__file__).with_name("sql") / "migration_003_event_batches.sql"
_MIGRATION_004 = Path(__file__).with_name("sql") / "migration_004_perception_evidence.sql"
_MIGRATION_005 = Path(__file__).with_name("sql") / "migration_005_perception_mismatch_failures.sql"
_MIGRATION_006 = Path(__file__).with_name("sql") / "migration_006_inbox_rejections.sql"
_MIGRATIONS = ((1, _SCHEMA), (2, _MIGRATION_002), (3, _MIGRATION_003), (4, _MIGRATION_004),
               (5, _MIGRATION_005), (6, _MIGRATION_006))
# Keys are case-folded and reduced to alphanumeric characters before comparison.
# Sensitive aliases are rejected as complete keys or compound stems.  The only
# exceptions are the narrowly named metadata entries below; no suffix alone
# makes a potentially secret-bearing field admissible.
_SENSITIVE_PAYLOAD_KEY_STEMS = frozenset({
    "accesstoken",
    "apikey",
    "authtoken",
    "authorization",
    "authorizationheader",
    "bearertoken",
    "clientsecret",
    "credential",
    "credentials",
    "password",
    "refreshtoken",
    "secretkey",
    "token",
    "xapikey",
})
_SAFE_STRING_METADATA_KEYS = frozenset({
    "apikeydescription", "credentialreference", "passwordpolicy",
})
_OPEN_RETRY_ATTEMPTS = 5
_OPEN_RETRY_SECONDS = 0.05


class RoomStoreError(RuntimeError):
    """Base class for room persistence errors."""


class VersionConflict(RoomStoreError):
    """The caller attempted to append against a stale state version."""


class IdempotencyConflict(RoomStoreError):
    """One idempotency key was reused for different canonical event content."""


class ForbiddenPayload(RoomStoreError):
    """The event payload contains data that must never enter the event ledger."""


@dataclass(frozen=True)
class StoredEvent:
    sequence_no: int
    event_id: str
    session_id: str
    state_version: int
    event_type: str
    correlation_id: str
    causation_id: str | None
    idempotency_key: str
    event_sha256: str
    payload: dict[str, object]
    wall_time: str
    monotonic_ms: int


class RoomStore:
    """Thread-safe room persistence boundary.

    A store serializes all access to its SQLite connection.  ``close`` waits
    for any current operation, is terminal, and makes later store calls raise
    ``RuntimeError``; callers must not use ``connection`` directly after sharing
    the store with controller or supervisor threads.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self._lock = threading.RLock()
        self._closed = False

    @classmethod
    def open(cls, path: str | Path) -> "RoomStore":
        database_path = Path(path)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(_OPEN_RETRY_ATTEMPTS):
            connection = sqlite3.connect(str(database_path), timeout=5.0, check_same_thread=False)
            try:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA busy_timeout = 5000")
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA foreign_keys = ON")
                _run_migrations(connection)
                return cls(connection)
            except sqlite3.OperationalError as exc:
                connection.close()
                if "locked" not in str(exc).lower() or attempt + 1 == _OPEN_RETRY_ATTEMPTS:
                    raise
                time.sleep(_OPEN_RETRY_SECONDS * (attempt + 1))
        raise RuntimeError("room store opening exhausted its retry budget")

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self.connection.close()
                self._closed = True

    def create_session(
        self, session_id: str, request_text: str, capabilities: CapabilityProfile,
    ) -> None:
        capability_json = _canonical_json(_json_value(capabilities))
        with self._lock, self.connection:
            self._require_open()
            self.connection.execute(
                "INSERT INTO room_sessions "
                "(session_id, request_text, capability_json, status, state_version, created_at) "
                "VALUES (?, ?, ?, 'PENDING', 0, ?)",
                (session_id, request_text, capability_json, _utc_now()),
            )

    def session_version(self, session_id: str) -> int:
        with self._lock:
            self._require_open()
            row = self.connection.execute(
                "SELECT state_version FROM room_sessions WHERE session_id = ?", (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown room session {session_id!r}")
            return int(row["state_version"])

    def append_event(
        self,
        *,
        session_id: str,
        expected_version: int,
        event_type: str,
        payload: Mapping[str, object],
        correlation_id: str,
        causation_id: str | None,
        idempotency_key: str,
    ) -> StoredEvent:
        return self.append_events(
            session_id=session_id, expected_version=expected_version,
            events=({"event_type": event_type, "payload": payload,
                     "correlation_id": correlation_id, "causation_id": causation_id,
                     "idempotency_key": idempotency_key},),
        )[0]

    def append_events(
        self, *, session_id: str, expected_version: int,
        events: Sequence[Mapping[str, object]],
    ) -> tuple[StoredEvent, ...]:
        """Append a complete ordered batch, or recover its complete prior delivery.

        Replay requires the same persisted batch, digest, count and ordered
        membership. Prefixes, suffixes and concatenated adjacent batches are
        conflicts. No metadata, version or event survives a failed append.
        """
        prepared = []
        keys = set()
        for event in events:
            key = event["idempotency_key"]
            if key in keys:
                raise IdempotencyConflict("duplicate idempotency key within batch")
            keys.add(key)
            payload_json = _canonical_payload(event["payload"])
            digest = _event_sha256(
                session_id=session_id, event_type=event["event_type"],
                payload_json=payload_json, correlation_id=event["correlation_id"],
                causation_id=event["causation_id"],
            )
            prepared.append((dict(event), payload_json, digest))
        if not prepared:
            raise ValueError("event batch must not be empty")
        with self._lock:
            self._require_open()
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                recovered = []
                for event, _, digest in prepared:
                    existing = self.connection.execute(
                        "SELECT * FROM room_events WHERE session_id = ? AND idempotency_key = ?",
                        (session_id, event["idempotency_key"]),
                    ).fetchone()
                    if existing is not None:
                        if existing["event_sha256"] != digest:
                            raise IdempotencyConflict("idempotency key has different event content")
                        recovered.append(_stored_event(existing))
                if recovered:
                    if len(recovered) != len(prepared):
                        raise IdempotencyConflict("partial replay of an atomic event batch")
                    _validate_batch_replay(self.connection, recovered)
                    self.connection.commit()
                    return tuple(recovered)
                session = self.connection.execute(
                    "SELECT state_version FROM room_sessions WHERE session_id = ?", (session_id,),
                ).fetchone()
                if session is None:
                    raise KeyError(f"unknown room session {session_id!r}")
                if int(session["state_version"]) != expected_version:
                    raise VersionConflict(
                        f"session {session_id!r} expected version {expected_version}, "
                        f"found {session['state_version']}",
                    )
                sequence_no = int(self.connection.execute(
                    "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM room_events WHERE session_id = ?",
                    (session_id,),
                ).fetchone()[0])
                appended = []
                for index, (event, payload_json, digest) in enumerate(prepared):
                    stored = StoredEvent(
                        sequence_no=sequence_no + index, event_id=str(uuid.uuid4()),
                        session_id=session_id, state_version=expected_version + index + 1,
                        event_type=event["event_type"], correlation_id=event["correlation_id"],
                        causation_id=event["causation_id"], idempotency_key=event["idempotency_key"],
                        event_sha256=digest, payload=json.loads(payload_json), wall_time=_utc_now(),
                        monotonic_ms=time.monotonic_ns() // 1_000_000,
                    )
                    self.connection.execute(
                        "INSERT INTO room_events "
                        "(sequence_no, event_id, session_id, state_version, event_type, correlation_id, "
                        "causation_id, idempotency_key, event_sha256, payload_json, wall_time, monotonic_ms) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (stored.sequence_no, stored.event_id, session_id, stored.state_version,
                         stored.event_type, stored.correlation_id, stored.causation_id,
                         stored.idempotency_key, digest, payload_json, stored.wall_time, stored.monotonic_ms),
                    )
                    appended.append(stored)
                _record_event_batch(self.connection, appended)
                updated = self.connection.execute(
                    "UPDATE room_sessions SET state_version = state_version + ? "
                    "WHERE session_id = ? AND state_version = ?",
                    (len(appended), session_id, expected_version),
                )
                if updated.rowcount != 1:
                    raise VersionConflict(f"session {session_id!r} changed during append")
                self.connection.commit()
                return tuple(appended)
            except Exception:
                self.connection.rollback()
                raise

    def events(self, session_id: str, *, after_sequence: int = 0) -> tuple[StoredEvent, ...]:
        with self._lock:
            self._require_open()
            rows = self.connection.execute(
                "SELECT * FROM room_events WHERE session_id = ? AND sequence_no > ? "
                "ORDER BY sequence_no",
                (session_id, after_sequence),
            ).fetchall()
            return tuple(_stored_event(row) for row in rows)

    def snapshot_to(self, destination: Path) -> None:
        """Take a consistent SQLite backup; never copy a live database or WAL file."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._require_open()
            with sqlite3.connect(str(destination)) as snapshot:
                self.connection.backup(snapshot)

    def current_hypotheses(self, session_id: str, *, snapshot_version: int) -> tuple[dict, ...]:
        """Consumer view: current verified boxes, with scores and bands separate."""
        with self._lock:
            self._require_open()
            rows = self.connection.execute(
                "SELECT hypothesis.hypothesis_id, evidence.*, revision.revision_no "
                "FROM room_object_hypotheses AS hypothesis "
                "JOIN room_sessions AS session ON session.session_id = hypothesis.session_id "
                "JOIN room_hypothesis_revisions AS revision "
                "ON revision.session_id = hypothesis.session_id AND revision.hypothesis_id = hypothesis.hypothesis_id "
                "JOIN room_detection_evidence AS evidence ON evidence.session_id = hypothesis.session_id "
                "AND evidence.observation_id = hypothesis.observation_id AND evidence.detection_id = hypothesis.detection_id "
                "WHERE hypothesis.session_id = ? AND revision.snapshot_version = ? "
                "AND session.state_version = revision.snapshot_version "
                "AND revision.authorizes_current_state = 1 AND NOT EXISTS ("
                "SELECT 1 FROM room_hypothesis_revisions AS later WHERE later.session_id = revision.session_id "
                "AND later.hypothesis_id = revision.hypothesis_id AND later.revision_no > revision.revision_no) "
                "ORDER BY hypothesis.hypothesis_id", (session_id, snapshot_version),
            ).fetchall()
            return tuple({**dict(row), "bbox_xywh": tuple(json.loads(row["full_frame_bbox_json"]))} for row in rows)

    def poll_perception_inbox(self, path: Path, *, current_snapshot_version: int,
                             wait_s: float = 0) -> tuple:
        from .ingest import poll_inbox
        return poll_inbox(self, path, current_snapshot_version=current_snapshot_version, wait_s=wait_s)

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("room store is closed")


def _run_migrations(connection: sqlite3.Connection) -> None:
    # SQLite table rebuilds require FK enforcement disabled before BEGIN. The
    # connection is private during open; all rebuilt references are checked
    # before commit, and the caller's enforcement setting is always restored.
    foreign_keys = int(connection.execute("PRAGMA foreign_keys").fetchone()[0])
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        # SQLite's write lock serializes first-open callers.  Version rows are
        # read only after obtaining it, so a waiting opener sees completed work.
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS room_schema_version "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)",
        )
        current = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM room_schema_version",
        ).fetchone()[0]
        for version, migration in _MIGRATIONS:
            if version <= current:
                continue
            _execute_migration(connection, migration.read_text(encoding="utf-8"))
            if version == 3:
                _backfill_legacy_event_batches(connection)
            connection.execute(
                "INSERT INTO room_schema_version(version, applied_at) VALUES (?, ?)",
                (version, _utc_now()),
            )
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise sqlite3.IntegrityError("room migration violates foreign keys")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute(f"PRAGMA foreign_keys = {foreign_keys}")


def _execute_migration(connection: sqlite3.Connection, script: str) -> None:
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip():
        raise ValueError("migration ends with an incomplete SQL statement")


def _batch_sha256(events: Sequence[StoredEvent]) -> str:
    return hashlib.sha256(_canonical_json({
        "session_id": events[0].session_id,
        "members": [{"idempotency_key": event.idempotency_key, "event_sha256": event.event_sha256}
                    for event in events],
    }).encode("utf-8")).hexdigest()


def _record_event_batch(
    connection: sqlite3.Connection, events: Sequence[StoredEvent], *, legacy_singleton: bool = False,
) -> None:
    batch_id = str(uuid.uuid4())
    connection.execute(
        "INSERT INTO room_event_batches (batch_id, session_id, batch_sha256, event_count, legacy_singleton) "
        "VALUES (?, ?, ?, ?, ?)",
        (batch_id, events[0].session_id, _batch_sha256(events), len(events), int(legacy_singleton)),
    )
    connection.executemany(
        "INSERT INTO room_event_batch_members (batch_id, ordinal, event_id) VALUES (?, ?, ?)",
        ((batch_id, index, event.event_id) for index, event in enumerate(events)),
    )


def _validate_batch_replay(connection: sqlite3.Connection, events: Sequence[StoredEvent]) -> None:
    batch = connection.execute(
        "SELECT batch.* FROM room_event_batches AS batch "
        "JOIN room_event_batch_members AS member ON member.batch_id = batch.batch_id "
        "WHERE member.event_id = ?", (events[0].event_id,),
    ).fetchone()
    if batch is None or batch["event_count"] != len(events) or batch["batch_sha256"] != _batch_sha256(events):
        raise IdempotencyConflict("replay does not match a complete original event batch")
    members = connection.execute(
        "SELECT event_id FROM room_event_batch_members WHERE batch_id = ? ORDER BY ordinal",
        (batch["batch_id"],),
    ).fetchall()
    if tuple(row["event_id"] for row in members) != tuple(event.event_id for event in events):
        raise IdempotencyConflict("replay differs from original batch membership or order")


def _backfill_legacy_event_batches(connection: sqlite3.Connection) -> None:
    """Old event rows do not identify original transaction boundaries.

    Preserve singleton append_event retries, explicitly label that provenance,
    and reject any attempted regrouping of legacy events into larger batches.
    This runs in the same migration transaction as schema and version writes.
    """
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    rows = cursor.execute("SELECT * FROM room_events ORDER BY session_id, sequence_no").fetchall()
    for row in rows:
        _record_event_batch(connection, (_stored_event(row),), legacy_singleton=True)


def _canonical_payload(payload: Mapping[str, object]) -> str:
    normalized = _json_value(payload)
    _reject_forbidden_payload(normalized)
    return _canonical_json(normalized)


def _reject_forbidden_payload(value: object) -> None:
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise ForbiddenPayload("event payloads cannot include raw bytes")
    if isinstance(value, Mapping):
        if _is_named_header(value):
            raise ForbiddenPayload("event payloads cannot include credential headers")
        for key, child in value.items():
            normalized = _normalize_payload_key(key)
            if "headers" in normalized:
                raise ForbiddenPayload("event payloads cannot include headers")
            if _is_safe_metadata_entry(normalized, child):
                continue
            if _is_forbidden_payload_key(key):
                raise ForbiddenPayload(f"event payload key {key!r} is forbidden")
            _reject_forbidden_payload(child)
    elif isinstance(value, (tuple, list, set, frozenset)):
        if _is_header_pair(value):
            raise ForbiddenPayload("event payloads cannot include credential headers")
        for child in value:
            _reject_forbidden_payload(child)


def _normalize_payload_key(key: object) -> str:
    return "".join(character for character in str(key).casefold() if character.isalnum())


def _is_forbidden_payload_key(key: object) -> bool:
    normalized = _normalize_payload_key(key)
    return any(
        stem in normalized
        for stem in _SENSITIVE_PAYLOAD_KEY_STEMS
    )


def _is_safe_metadata_entry(normalized_key: str, value: object) -> bool:
    if normalized_key in _SAFE_STRING_METADATA_KEYS:
        return isinstance(value, str)
    if normalized_key == "tokencount":
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    return normalized_key == "authorizationdigest" and _is_sha256(value)


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value) is not None


def _is_header_pair(value: tuple[object, ...] | list[object] | set[object] | frozenset[object]) -> bool:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        return False
    return isinstance(value[0], str) and _is_forbidden_payload_key(value[0])


def _is_named_header(value: Mapping[object, object]) -> bool:
    name = next((child for key, child in value.items() if _normalize_payload_key(key) == "name"), None)
    has_value = any(_normalize_payload_key(key) == "value" for key in value)
    return has_value and isinstance(name, str) and _is_forbidden_payload_key(name)


def _event_sha256(
    *, session_id: str, event_type: str, payload_json: str, correlation_id: str,
    causation_id: str | None,
) -> str:
    canonical = _canonical_json({
        "causation_id": causation_id,
        "correlation_id": correlation_id,
        "event_type": event_type,
        "payload": json.loads(payload_json),
        "session_id": session_id,
    })
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _stored_event(row: sqlite3.Row) -> StoredEvent:
    return StoredEvent(
        sequence_no=int(row["sequence_no"]), event_id=row["event_id"], session_id=row["session_id"],
        state_version=int(row["state_version"]), event_type=row["event_type"],
        correlation_id=row["correlation_id"], causation_id=row["causation_id"],
        idempotency_key=row["idempotency_key"], event_sha256=row["event_sha256"],
        payload=json.loads(row["payload_json"]), wall_time=row["wall_time"],
        monotonic_ms=int(row["monotonic_ms"]),
    )


def _json_value(value: object) -> object:
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(child) for child in value]
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
