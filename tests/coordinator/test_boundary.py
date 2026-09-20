"""CoordinatorBoundary lives in src/ (P4 Task 2)."""
from __future__ import annotations

from tests.coordinator import harness
from sentinel.coordinator import boundary


def test_harness_reexports_the_same_class():
    assert harness.CoordinatorBoundary is boundary.CoordinatorBoundary


def test_submit_delegates_to_the_gateway(tmp_path):
    repo, gateway = harness.open_harness(tmp_path)
    try:
        repo.create_run(harness.RUN_ID, status="RUNNING")
        adapter = boundary.CoordinatorBoundary(repo, gateway)
        events_before = repo.read_events(harness.RUN_ID, 0, 10)
        result = adapter.submit(harness.envelope("EMERGENCY_SIGNAL", {
            "signal_id": "77777777-7777-4777-8777-777777777777",
        }, sequence=1, actor_type="WORKER"))
        assert result["status"] == "COMPLETED"
        events_after = repo.read_events(harness.RUN_ID, 0, 10)
        assert len(events_after) == len(events_before) + 1
    finally:
        harness.close(repo)


def test_read_state_returns_a_dict_with_state_version(tmp_path):
    repo, gateway = harness.open_harness(tmp_path)
    try:
        repo.create_run(harness.RUN_ID, status="RUNNING")
        adapter = boundary.CoordinatorBoundary(repo, gateway)
        state = adapter.read_state(harness.RUN_ID)
        assert isinstance(state, dict)
        assert "state_version" in state
    finally:
        harness.close(repo)
