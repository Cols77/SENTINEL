"""Evidence-gated tasks and deterministic simulated assignment."""

from __future__ import annotations

import json
import uuid
from typing import Any, Mapping

from .errors import CoordinatorError
from .events import utc_now


class EvidenceTaskService:
    """Create tasks only from an exact, completed perception/artifact pair."""

    def __init__(self, repository):
        self.repository = repository

    def create_task(
        self, run_id: str, perception_result_id: str, section_id: str,
        evidence_artifact_id: str, *, task_id: str | None = None,
    ) -> dict[str, Any]:
        row = self.repository.connection.execute(
            """SELECT p.run_id, p.artifact_id, p.result_json, o.station_id
               FROM perception_references p JOIN observations o ON o.observation_id = p.observation_id
               WHERE p.result_id = ? AND p.run_id = ?""",
            (perception_result_id, run_id),
        ).fetchone()
        if row is None or row["artifact_id"] != evidence_artifact_id or row["station_id"] != section_id:
            raise CoordinatorError("INVALID_STATE_TRANSITION", "task evidence is not bound to the requested section and artifact")
        try:
            result = json.loads(row["result_json"])
        except json.JSONDecodeError as exc:
            raise CoordinatorError("INVALID_CONTRACT", "stored perception result is not JSON") from exc
        if result.get("status") != "COMPLETED":
            raise CoordinatorError("INVALID_STATE_TRANSITION", "only completed evidence can create a task")
        task_id = task_id or str(uuid.uuid4())
        existing = self.repository.connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if existing is not None:
            if (existing["run_id"], existing["perception_result_id"], existing["station_id"]) != (run_id, perception_result_id, section_id):
                raise CoordinatorError("IDEMPOTENCY_KEY_CONFLICT", "task ID is bound to different evidence")
            return dict(existing)
        with self.repository.connection:
            self.repository.connection.execute(
                "INSERT INTO tasks(task_id, run_id, station_id, status, perception_result_id, created_at) VALUES (?, ?, ?, 'PROPOSED', ?, ?)",
                (task_id, run_id, section_id, perception_result_id, utc_now()),
            )
        return {"task_id": task_id, "run_id": run_id, "section_id": section_id, "status": "PROPOSED"}


class SimulatedPatroller:
    """Persist a stable score-descending, ID-tiebroken ranking."""

    def __init__(self, repository):
        self.repository = repository

    def rank_for_task(self, task_id: str, scores: Mapping[str, float]) -> list[dict[str, Any]]:
        if self.repository.connection.execute("SELECT 1 FROM tasks WHERE task_id = ?", (task_id,)).fetchone() is None:
            raise CoordinatorError("INVALID_STATE_TRANSITION", "unknown task")
        ranking = sorted(((str(pid), float(score)) for pid, score in scores.items()), key=lambda item: (-item[1], item[0]))
        with self.repository.connection:
            self.repository.connection.execute("DELETE FROM task_rankings WHERE task_id = ?", (task_id,))
            for rank_no, (patroller_id, score) in enumerate(ranking, 1):
                self.repository.connection.execute(
                    "INSERT INTO task_rankings(task_id, rank_no, patroller_id, score) VALUES (?, ?, ?, ?)",
                    (task_id, rank_no, patroller_id, score),
                )
        return [{"rank_no": n, "patroller_id": p, "score": s} for n, (p, s) in enumerate(ranking, 1)]


class AssignmentService:
    """Propose only the currently persisted rank-1 patroller."""

    def __init__(self, repository):
        self.repository = repository

    def propose(self, task_id: str, patroller_id: str, reason: str) -> dict[str, Any]:
        row = self.repository.connection.execute(
            "SELECT patroller_id FROM task_rankings WHERE task_id = ? AND rank_no = 1", (task_id,)
        ).fetchone()
        if row is None or row["patroller_id"] != patroller_id:
            raise CoordinatorError("INVALID_STATE_TRANSITION", "assignment must bind to the persisted rank-1 proposal")
        with self.repository.connection:
            self.repository.connection.execute("UPDATE tasks SET status = 'ASSIGNMENT_PROPOSED', proposed_patroller_id = ?, proposal_reason = ? WHERE task_id = ?", (patroller_id, reason, task_id))
        return {"task_id": task_id, "patroller_id": patroller_id, "reason": reason, "status": "PROPOSED"}

    def decide(self, run_id: str, task_id: str, decision: str, *, actor_id: str, expected_state_version: int) -> dict[str, Any]:
        if not actor_id:
            raise CoordinatorError("UNAUTHORIZED_ACTOR", "operator identity is required")
        run = self.repository.connection.execute("SELECT state_version FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        task = self.repository.connection.execute("SELECT * FROM tasks WHERE task_id = ? AND run_id = ?", (task_id, run_id)).fetchone()
        if run is None or task is None or run["state_version"] != expected_state_version:
            raise CoordinatorError("STALE_STATE_VERSION", "task decision is not current")
        if task["status"] != "ASSIGNMENT_PROPOSED" or not task["proposed_patroller_id"]:
            raise CoordinatorError("INVALID_STATE_TRANSITION", "task has no pending assignment proposal")
        status = "ASSIGNED" if decision == "CONFIRM" else "PROPOSED"
        with self.repository.connection:
            self.repository.connection.execute("UPDATE tasks SET status = ? WHERE task_id = ?", (status, task_id))
        return {"task_id": task_id, "decision": decision, "status": status}
