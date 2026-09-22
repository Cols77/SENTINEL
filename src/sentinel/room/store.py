"""SQLite persistence boundary for append-only room-session state."""
from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Mapping
import uuid

from .models import CapabilityProfile

_SCHEMA = Path(__file__).with_name("sql") / "schema.sql"
_MIGRATIONS = ((1, _SCHEMA),)
_FORBIDDEN_PAYLOAD_KEYS = {"authorization", "authorizationheader", "credential", "credentials"}


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
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self._lock = threading.RLock()

    @classmethod
    def open(cls, path: str | Path) -> "RoomStore":
        database_path = Path(path)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(database_path), timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        _run_migrations(connection)
        return cls(connection)

    def close(self) -> None:
        self.connection.close()

    def create_session(
        self, session_id: str, request_text: str, capabilities: CapabilityProfile,
    ) -> None:
        capability_json = _canonical_json(_json_value(capabilities))
        with self._lock, self.connection:
            self.connection.execute(
                "INSERT INTO room_sessions "
                "(session_id, request_text, capability_json, status, state_version, created_at) "
                "VALUES (?, ?, ?, 'PENDING', 0, ?)",
                (session_id, request_text, capability_json, _utc_now()),
            )

    def session_version(self, session_id: str) -> int:
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
        payload_json = _canonical_payload(payload)
        digest = _event_sha256(
            session_id=session_id,
            event_type=event_type,
            payload_json=payload_json,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        with self._lock:
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                existing = self.connection.execute(
                    "SELECT * FROM room_events WHERE session_id = ? AND idempotency_key = ?",
                    (session_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    if existing["event_sha256"] != digest:
                        raise IdempotencyConflict(
                            f"idempotency key {idempotency_key!r} has different event content",
                        )
                    self.connection.commit()
                    return _stored_event(existing)
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
                state_version = expected_version + 1
                event_id = str(uuid.uuid4())
                wall_time = _utc_now()
                monotonic_ms = time.monotonic_ns() // 1_000_000
                self.connection.execute(
                    "INSERT INTO room_events "
                    "(sequence_no, event_id, session_id, state_version, event_type, correlation_id, "
                    "causation_id, idempotency_key, event_sha256, payload_json, wall_time, monotonic_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (sequence_no, event_id, session_id, state_version, event_type, correlation_id,
                     causation_id, idempotency_key, digest, payload_json, wall_time, monotonic_ms),
                )
                updated = self.connection.execute(
                    "UPDATE room_sessions SET state_version = state_version + 1 "
                    "WHERE session_id = ? AND state_version = ?",
                    (session_id, expected_version),
                )
                if updated.rowcount != 1:
                    raise VersionConflict(f"session {session_id!r} changed during append")
                self.connection.commit()
                return StoredEvent(
                    sequence_no=sequence_no, event_id=event_id, session_id=session_id,
                    state_version=state_version, event_type=event_type,
                    correlation_id=correlation_id, causation_id=causation_id,
                    idempotency_key=idempotency_key, event_sha256=digest,
                    payload=json.loads(payload_json), wall_time=wall_time, monotonic_ms=monotonic_ms,
                )
            except Exception:
                self.connection.rollback()
                raise

    def events(self, session_id: str, *, after_sequence: int = 0) -> tuple[StoredEvent, ...]:
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
            with sqlite3.connect(str(destination)) as snapshot:
                self.connection.backup(snapshot)


def _run_migrations(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS room_schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)",
    )
    current = connection.execute("SELECT COALESCE(MAX(version), 0) FROM room_schema_version").fetchone()[0]
    for version, migration in _MIGRATIONS:
        if version <= current:
            continue
        with connection:
            connection.executescript(migration.read_text(encoding="utf-8"))
            connection.execute(
                "INSERT INTO room_schema_version(version, applied_at) VALUES (?, ?)",
                (version, _utc_now()),
            )


def _canonical_payload(payload: Mapping[str, object]) -> str:
    _reject_forbidden_payload(payload)
    return _canonical_json(_json_value(payload))


def _reject_forbidden_payload(value: object) -> None:
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise ForbiddenPayload("event payloads cannot include raw bytes")
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower().replace("_", "").replace("-", "")
            if normalized in _FORBIDDEN_PAYLOAD_KEYS or normalized.startswith("authorization"):
                raise ForbiddenPayload(f"event payload key {key!r} is forbidden")
            _reject_forbidden_payload(child)
    elif isinstance(value, (tuple, list, set, frozenset)):
        for child in value:
            _reject_forbidden_payload(child)


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
