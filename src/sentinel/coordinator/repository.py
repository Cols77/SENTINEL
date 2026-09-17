"""File-backed SQLite coordinator repository."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import migrations
from .events import append_event, read_events


class CoordinatorRepository:
    """Own one SQLite connection and apply the coordinator schema on open."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    @classmethod
    def open(cls, path: str | Path) -> "CoordinatorRepository":
        database_path = Path(path)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(database_path), timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        migrations.apply(connection)
        return cls(connection)

    def create_run(
        self,
        run_id: str,
        *,
        status: str = "NEW",
        agent_phase: str = "RECEIVE_REQUEST",
        updated_at: str | None = None,
    ) -> None:
        from .events import utc_now

        with self.connection:
            self.connection.execute(
                """INSERT INTO runs(run_id, state_version, status, agent_phase,
                   latest_event_sequence, mountain_snapshot_version, updated_at)
                   VALUES (?, 0, ?, ?, 0, 0, ?)""",
                (run_id, status, agent_phase, updated_at or utc_now()),
            )

    def append_event(self, run_id: str, event_type: str, payload: dict, **kwargs):
        return append_event(self.connection, run_id, event_type, payload, **kwargs)

    def read_events(self, run_id: str, after_sequence: int, limit: int):
        return read_events(self.connection, run_id, after_sequence, limit)

    def read_state(self, run_id: str):
        from .state import build_state_snapshot

        return build_state_snapshot(self.connection, run_id)

    def set_pending_approval(self, run_id: str, target: str) -> dict:
        if target not in {"PLAN", "TASK"}:
            raise ValueError(f"unknown approval target: {target}")
        with self.connection:
            self.connection.execute(
                "UPDATE runs SET status = ? WHERE run_id = ?",
                ("WAITING_FOR_APPROVAL", run_id),
            )
        return self.append_event(run_id, "PLAN_PROPOSED", {"approval_target": target})

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "CoordinatorRepository":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()
