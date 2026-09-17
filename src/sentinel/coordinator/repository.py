"""File-backed SQLite coordinator repository."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import migrations


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

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "CoordinatorRepository":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()
