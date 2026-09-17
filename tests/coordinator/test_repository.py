from pathlib import Path

from sentinel.coordinator.repository import CoordinatorRepository


def test_repository_enables_wal_and_foreign_keys(tmp_path: Path):
    repository = CoordinatorRepository.open(tmp_path / "coordinator.sqlite3")
    assert repository.connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert repository.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert repository.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert repository.connection.execute("PRAGMA user_version").fetchone()[0] == 2
    tables = {
        row[0]
        for row in repository.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"runs", "events", "idempotency_keys", "plans", "operation_records"} <= tables
    repository.close()
