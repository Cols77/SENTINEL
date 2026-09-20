"""Read-only decision read model: what any policy may know at a decision point."""
from __future__ import annotations
import json
from dataclasses import dataclass, field

SCORE_BANDS = ((0.9, "very_high"), (0.75, "high"), (0.6, "moderate"), (0.5, "marginal"))

def score_band(score: float | None) -> str | None:
    if score is None:
        return None
    for floor, name in SCORE_BANDS:
        if score >= floor:
            return name
    return "below_threshold"

@dataclass(frozen=True)
class DecisionState:
    run_id: str
    state_version: int
    run_status: str
    agent_phase: str
    emergency_latched: bool
    request_text: str | None
    plan_id: str | None
    plan_mode: str | None
    plan_station_ids: tuple[str, ...]
    plan_approved: bool
    detection: dict | None   # {"result_id","artifact_id","station_id","status","raw_score","canonical_class"}
    open_task_station_ids: tuple[str, ...]
    task_id: str | None
    task_status: str | None
    ranked_patrollers: tuple[str, ...] = field(default_factory=tuple)

    def facts(self) -> dict:
        det = self.detection or {}
        station = det.get("station_id")
        return {
            "plan_exists": self.plan_id is not None,
            "plan_mode": self.plan_mode,
            "plan_station_count": len(self.plan_station_ids) if self.plan_id else None,
            "plan_approved": self.plan_approved,
            "detection_present": bool(det),
            "detection_status": det.get("status"),
            "detection_score_band": score_band(det.get("raw_score")),
            "detection_class": det.get("canonical_class"),
            "detection_inside_plan": (station in self.plan_station_ids) if det and self.plan_id else None,
            "detection_duplicates_open_task": (station in self.open_task_station_ids) if det else None,
            "task_exists": self.task_id is not None,
            "task_status": self.task_status,
            "patrollers_ranked": len(self.ranked_patrollers),
        }

    def to_packet(self) -> dict:
        return {"context_version": "decision_packet.v1",
                "run": {"status": self.run_status, "phase": self.agent_phase},
                "operator_request": self.request_text,
                "facts": self.facts()}

    def ids(self) -> dict:
        det = self.detection or {}
        return {"run_id": self.run_id, "plan_id": self.plan_id, "task_id": self.task_id,
                "perception_result_id": det.get("result_id"), "artifact_id": det.get("artifact_id"),
                "station_id": det.get("station_id")}

def read_decision_state(repository, run_id: str) -> DecisionState:
    c = repository.connection
    run = c.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if run is None:
        raise KeyError(run_id)
    events = repository.read_events(run_id, 0, 256)
    request_text = next((e["payload"].get("request_text") for e in events
                         if e["event_type"] == "USER_MESSAGE_RECEIVED"), None)
    plan = c.execute("SELECT plan_id, mode, station_ids_json FROM plans WHERE run_id = ? "
                     "ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
    approved = any(e["event_type"] == "PLAN_APPROVED" for e in events)
    task = c.execute("SELECT task_id, status FROM tasks WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
                     (run_id,)).fetchone()
    open_stations = tuple(r["station_id"] for r in c.execute(
        "SELECT station_id FROM tasks WHERE run_id = ? AND status NOT IN ('CLOSED','REJECTED')", (run_id,)))
    ref = c.execute("""SELECT pr.result_id, pr.artifact_id, pr.result_json, o.station_id
                       FROM perception_references pr JOIN observations o USING (observation_id)
                       WHERE pr.run_id = ? AND pr.result_id NOT IN
                             (SELECT COALESCE(perception_result_id, '') FROM tasks)
                       ORDER BY pr.created_at DESC LIMIT 1""", (run_id,)).fetchone()
    detection = None
    if ref is not None:
        result = json.loads(ref["result_json"])
        detection = {"result_id": ref["result_id"], "artifact_id": ref["artifact_id"],
                     "station_id": ref["station_id"], "status": result.get("status"),
                     "raw_score": result.get("raw_score"), "canonical_class": result.get("canonical_class")}
    ranked = ()
    if task is not None:
        ranked = tuple(r["patroller_id"] for r in c.execute(
            "SELECT patroller_id FROM task_rankings WHERE task_id = ? ORDER BY rank_no", (task["task_id"],)))
    return DecisionState(
        run_id=run_id, state_version=run["state_version"], run_status=run["status"],
        agent_phase=run["agent_phase"], emergency_latched=bool(run["emergency_latched"]),
        request_text=request_text,
        plan_id=plan["plan_id"] if plan else None, plan_mode=plan["mode"] if plan else None,
        plan_station_ids=tuple(json.loads(plan["station_ids_json"])) if plan else (),
        plan_approved=approved, detection=detection, open_task_station_ids=open_stations,
        task_id=task["task_id"] if task else None, task_status=task["status"] if task else None,
        ranked_patrollers=ranked)
