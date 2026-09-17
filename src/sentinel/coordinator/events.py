"""Append-only event cursor operations."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any, Mapping


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def append_event(
    connection: sqlite3.Connection,
    run_id: str,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    actor_type: str = "COORDINATOR",
    actor_id: str = "coordinator",
    correlation_id: str | None = None,
    source_kind: str = "SIMULATED",
    commit: bool = True,
) -> dict[str, Any]:
    """Atomically append one event and advance its run cursor and state version."""
    event_id = str(uuid.uuid4())
    correlation_id = correlation_id or event_id
    occurred_at = utc_now()
    transaction = connection if commit else nullcontext(connection)
    with transaction:
        row = connection.execute(
            "SELECT state_version, latest_event_sequence FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown run_id: {run_id}")
        sequence_no = int(row["latest_event_sequence"]) + 1
        state_version = int(row["state_version"]) + 1
        envelope = {
            "contract_version": "sentinel.v0",
            "wire_type": "event_envelope",
            "event_id": event_id,
            "run_id": run_id,
            "sequence_no": sequence_no,
            "event_type": event_type,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "correlation_id": correlation_id,
            "causation_id": None,
            "state_version": state_version,
            "source_kind": source_kind,
            "occurred_at": occurred_at,
            "recorded_at": occurred_at,
            "payload": dict(payload),
        }
        encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
        connection.execute(
            """INSERT INTO events(
                event_id, run_id, sequence_no, event_type, actor_type, actor_id,
                correlation_id, state_version, source_kind, occurred_at, recorded_at,
                payload_json, envelope_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                run_id,
                sequence_no,
                event_type,
                actor_type,
                actor_id,
                correlation_id,
                state_version,
                source_kind,
                occurred_at,
                occurred_at,
                json.dumps(dict(payload), sort_keys=True, separators=(",", ":")),
                encoded,
            ),
        )
        connection.execute(
            "UPDATE runs SET state_version = ?, latest_event_sequence = ?, updated_at = ? WHERE run_id = ?",
            (state_version, sequence_no, occurred_at, run_id),
        )
    return envelope


def read_events(connection: sqlite3.Connection, run_id: str, after_sequence: int, limit: int) -> list[dict[str, Any]]:
    if after_sequence < 0:
        raise ValueError("after_sequence must be non-negative")
    if limit < 1:
        raise ValueError("limit must be positive")
    rows = connection.execute(
        """SELECT envelope_json FROM events
           WHERE run_id = ? AND sequence_no > ?
           ORDER BY sequence_no ASC LIMIT ?""",
        (run_id, after_sequence, limit),
    ).fetchall()
    return [json.loads(row["envelope_json"]) for row in rows]
