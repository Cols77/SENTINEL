"""Materialized, contract-validated coordinator state snapshots."""

from __future__ import annotations

import sqlite3
from typing import Any

from sentinel.contracts import StateSnapshot, validate_document


def build_state_snapshot(connection: sqlite3.Connection, run_id: str) -> StateSnapshot:
    row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(f"unknown run_id: {run_id}")
    operation = connection.execute(
        """SELECT operation_id, run_id, mission_id, command_id, worker_id,
                  idempotency_key, status, dispatch_recorded_at, reconciled_at
           FROM operation_records
           WHERE run_id = ? AND status IN ('PENDING','DISPATCHED','IN_DOUBT')
           ORDER BY operation_id LIMIT 1""",
        (run_id,),
    ).fetchone()
    active_operation: dict[str, Any] | None = None
    if operation is not None:
        active_operation = {
            "contract_version": "sentinel.v0",
            "wire_type": "operation_record",
            **dict(operation),
        }
    actions = ["CONFIRM", "REJECT"] if row["status"] == "WAITING_FOR_APPROVAL" else []
    snapshot = StateSnapshot(
        run_id=run_id,
        state_version=row["state_version"],
        run_status=row["status"],
        agent_phase=row["agent_phase"],
        legal_operator_actions=actions,
        active_operation=active_operation,
        latest_event_sequence=row["latest_event_sequence"],
        mountain_snapshot_version=row["mountain_snapshot_version"],
        updated_at=row["updated_at"],
    )
    validate_document(snapshot.to_dict())
    return snapshot
