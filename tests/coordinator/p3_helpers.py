"""Gateway-driving helper for P3 tests. Every mutation goes through CommandGateway.submit."""
from __future__ import annotations

import json
from pathlib import Path

from sentinel.coordinator.decision_state import read_decision_state

from . import turn_generator as tg
from .harness import open_harness

LEFT_MID, RIGHT_MID, LEFT_FRONT = "LEFT_ALLEY_MIDDLE", "RIGHT_ALLEY_MIDDLE", "LEFT_ALLEY_FRONT"  # LEFT_FRONT is outside the route plan


class P3Run:
    def __init__(self, tmp_path: Path, name: str, request: str = "route-only survey of the alley stations"):
        self.repo, self.gateway = open_harness(tmp_path)
        self.run_id = tg._run_id_for(f"p3:{name}")
        self.seq = tg._Sequencer()
        self.reference: dict | None = None
        tg._create_run(self.gateway, self.run_id, self.seq, request)

    def approve_route_plan(self) -> dict:
        """Propose and CONFIRM PLAN_ROUTE_ONLY_2_ZONE (LEFT_ALLEY_MIDDLE, RIGHT_ALLEY_MIDDLE)."""
        self.reference = tg._propose_and_approve_route_plan(self.gateway, self.repo, self.run_id, self.seq)
        return self.reference

    def ingest(self, seed: str, station_id: str, raw_score: float | None, status: str = "DETECTED") -> dict:
        payload = tg._perception_payload(f"{self.run_id}:{seed}", self.reference["plan_id"],
                                         self.reference["plan_sha256"], station_id, raw_score, status)
        tg._ingest_perception(self.gateway, self.run_id, self.seq, payload)
        return payload

    def create_task(self, perception: dict) -> str:
        tg._create_task(self.gateway, self.run_id, self.seq, perception)
        row = self.repo.connection.execute(
            "SELECT task_id FROM tasks WHERE perception_result_id = ?", (perception["result_id"],)).fetchone()
        return row["task_id"]

    def submit(self, command_type: str, payload: dict, *, actor_type: str = "AGENT", with_version: bool = False) -> dict:
        expected = tg._state_version(self.repo, self.run_id) if with_version else None
        return tg._submit(self.gateway, self.run_id, self.seq, command_type, payload,
                          actor_type=actor_type, expected=expected)

    def rank(self, task_id: str) -> None:
        self.submit("TASK_RANK", {"task_id": task_id})

    def propose_plan(self, catalogue_plan_id: str) -> None:
        self.submit("AGENT_INTENT_PROPOSE", {"intent": "REQUEST_OPERATOR_APPROVAL", "catalogue_plan_id": catalogue_plan_id})

    def decide_latest_plan(self, decision: str) -> None:
        row = self.repo.connection.execute(
            "SELECT plan_id, catalogue_id, catalogue_revision, plan_sha256, station_ids_json FROM plans "
            "WHERE run_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1", (self.run_id,)).fetchone()
        reference = {"plan_id": row["plan_id"], "catalogue_id": row["catalogue_id"],
                     "catalogue_revision": row["catalogue_revision"], "plan_sha256": row["plan_sha256"],
                     "station_ids": json.loads(row["station_ids_json"])}
        self.submit("OPERATOR_DECIDE", {"decision": decision, "target": {"kind": "PLAN", "plan_reference": reference}},
                    actor_type="OPERATOR", with_version=True)

    def state(self):
        return read_decision_state(self.repo, self.run_id)

    def close(self) -> None:
        self.repo.close()
