"""Required A15/A16 one-station coordinator flow evidence."""
from __future__ import annotations

from .harness import ARTIFACT_ID, RESULT_ID, envelope, seeded_one_station, close


def test_one_station_flow_runs_from_run_create_through_final_assignment(tmp_path):
    repo, gateway = seeded_one_station(tmp_path)
    try:
        # RUN_CREATE is exercised on a separate fresh run because the harness
        # provides the immutable capture/perception evidence for the final loop.
        run_id = "88888888-8888-4888-8888-888888888888"
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"}, sequence=1, run_id=run_id, actor_type="OPERATOR"))
        assert repo.connection.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()[0] == "RUNNING"

        create = gateway.submit(envelope("TASK_CREATE", {
            "perception_result_id": RESULT_ID, "section_id": "LEFT_ALLEY_MIDDLE", "evidence_artifact_id": ARTIFACT_ID,
        }, sequence=2, expected=0))
        # The command gateway derives task identity from command_id.
        assert create["status"] == "COMPLETED"
        task_id = repo.connection.execute("SELECT task_id FROM tasks").fetchone()[0]
        ranking = gateway.submit(envelope("TASK_RANK", {"task_id": task_id}, sequence=3, expected=1))
        assert ranking["payload"]["ranking"][0]["patroller_id"] == "PATROLLER_1"
        gateway.submit(envelope("ASSIGNMENT_PROPOSE", {"task_id": task_id, "patroller_id": "PATROLLER_1", "reason": "deterministic rank-1"}, sequence=4, expected=1))
        gateway.submit(envelope("OPERATOR_DECIDE", {"decision": "CONFIRM", "target": {"kind": "TASK", "task_id": task_id}}, sequence=5, actor_type="OPERATOR", expected=2))
        assert repo.connection.execute("SELECT status FROM tasks WHERE task_id = ?", (task_id,)).fetchone()[0] == "ASSIGNED"
    finally:
        close(repo)
