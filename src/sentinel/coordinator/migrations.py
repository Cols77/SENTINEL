"""Versioned SQLite schema migrations for the coordinator."""

from __future__ import annotations

import sqlite3
from pathlib import Path

CURRENT_VERSION = 2
_SCHEMA_PATH = Path(__file__).with_name("sql") / "schema.sql"


def apply(connection: sqlite3.Connection) -> None:
    """Apply all migrations required by the current coordinator repository."""
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version > CURRENT_VERSION:
        raise RuntimeError(f"database schema version {version} is newer than {CURRENT_VERSION}")
    if version == CURRENT_VERSION:
        return
    if version not in {0, 1}:
        raise RuntimeError(f"unsupported database schema version {version}")
    schema = _SCHEMA_PATH.read_text(encoding="utf-8")
    with connection:
        connection.executescript(schema)
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, datetime('now'))",
            (CURRENT_VERSION,),
        )
        connection.execute(f"PRAGMA user_version = {CURRENT_VERSION}")
