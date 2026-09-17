from pathlib import Path

from sentinel.coordinator.repository import CoordinatorRepository


def test_events_use_an_exclusive_per_run_cursor(tmp_path: Path):
    repository = CoordinatorRepository.open(tmp_path / "events.sqlite3")
    repository.create_run("run-1")
    first = repository.append_event("run-1", "RUN_CREATED", {"state": "NEW"})
    second = repository.append_event("run-1", "PLAN_PROPOSED", {"plan_id": "plan-1"})

    assert first["sequence_no"] == 1
    assert second["sequence_no"] == 2
    assert [event["sequence_no"] for event in repository.read_events("run-1", 0, 10)] == [1, 2]
    assert [event["sequence_no"] for event in repository.read_events("run-1", 1, 10)] == [2]
    repository.close()
