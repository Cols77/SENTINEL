"""Read-only decision read model: what any policy may know at a decision point."""
from __future__ import annotations
import json
from dataclasses import dataclass, field

from sentinel.coordinator.assignment_policy import assignment_facts

SCORE_BANDS = ((0.9, "very_high"), (0.75, "high"), (0.6, "moderate"), (0.5, "marginal"))

def score_band(score: float | None) -> str | None:
    if score is None:
        return None
    for floor, name in SCORE_BANDS:
        if score >= floor:
            return name
    return "below_threshold"

_SETTLED_TASK = frozenset({"ASSIGNED", "CLOSED", "REJECTED"})
_OPEN_TASK = frozenset({"PROPOSED", "ASSIGNMENT_PROPOSED"})
_HANDLED_TASK = frozenset({"ASSIGNED", "CLOSED"})


@dataclass(frozen=True)
class DetectionView:
    result_id: str
    artifact_id: str
    station_id: str
    status: str | None
    raw_score: float | None
    canonical_class: str | None


@dataclass(frozen=True)
class TaskView:
    task_id: str
    station_id: str
    status: str
    perception_result_id: str | None
    ranked_patrollers: tuple[str, ...] = ()
    ranking_scores: tuple[float, ...] = ()


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
    detections: tuple[DetectionView, ...] = field(default_factory=tuple)
    tasks: tuple[TaskView, ...] = field(default_factory=tuple)
    proposed_catalogue_plan_ids: tuple[str, ...] = field(default_factory=tuple)

    @property
    def current_task(self) -> TaskView | None:
        for task in self.tasks:
            if task.status not in _SETTLED_TASK:
                return task
        return self.tasks[-1] if self.tasks else None

    def detection_facts(self, d: DetectionView) -> dict:
        same_station = [t for t in self.tasks if t.station_id == d.station_id]
        return {"score_band": score_band(d.raw_score),
                "inside_plan": (d.station_id in self.plan_station_ids) if self.plan_id else None,
                "duplicates_open_task": any(t.status in _OPEN_TASK for t in same_station),
                "repeat_of_handled": any(t.status in _HANDLED_TASK for t in same_station)}

    def to_packet_v2(self) -> dict:
        head = self.detections[0] if self.detections else None
        return {"context_version": "decision_packet.v2",
                "run": {"status": self.run_status, "phase": self.agent_phase},
                "operator_request": self.request_text,
                "facts": self.facts(),
                "queue": {"pending_detections": len(self.detections),
                          "open_tasks": sum(1 for t in self.tasks if t.status in _OPEN_TASK)},
                "detection": self.detection_facts(head) if head else None,
                "assignment": (assignment_facts(self.current_task.ranking_scores)
                               if self.current_task and self.current_task.ranking_scores else None)}

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
    plan_rows = c.execute(
        """SELECT p.plan_id, p.mode, p.station_ids_json,
                  (SELECT pa.action FROM plan_approvals pa
                   WHERE pa.run_id = p.run_id AND pa.plan_id = p.plan_id
                   ORDER BY pa.created_at DESC, pa.rowid DESC LIMIT 1) AS action
           FROM plans p WHERE p.run_id = ? ORDER BY p.created_at, p.rowid""", (run_id,)).fetchall()
    confirmed = [r for r in plan_rows if r["action"] == "CONFIRM"]
    undecided = [r for r in plan_rows if r["action"] is None]
    plan = confirmed[-1] if confirmed else (undecided[-1] if undecided else None)
    approved = bool(confirmed)
    proposed = tuple(e["payload"]["catalogue_plan_id"] for e in events
                     if e["event_type"] == "PLAN_PROPOSED" and "catalogue_plan_id" in e["payload"])
    open_stations = tuple(r["station_id"] for r in c.execute(
        "SELECT station_id FROM tasks WHERE run_id = ? AND status NOT IN ('CLOSED','REJECTED')", (run_id,)))
    det_rows = c.execute("""SELECT pr.result_id, pr.artifact_id, pr.result_json, o.station_id
                            FROM perception_references pr JOIN observations o USING (observation_id)
                            WHERE pr.run_id = ? AND pr.result_id NOT IN
                                  (SELECT COALESCE(perception_result_id, '') FROM tasks)
                            ORDER BY pr.created_at, pr.rowid""", (run_id,)).fetchall()
    detections = []
    for row in det_rows:
        result = json.loads(row["result_json"])
        detections.append(DetectionView(row["result_id"], row["artifact_id"], row["station_id"],
                                        result.get("status"), result.get("raw_score"), result.get("canonical_class")))
    tasks = []
    for row in c.execute("SELECT task_id, station_id, status, perception_result_id FROM tasks "
                         "WHERE run_id = ? ORDER BY created_at, rowid", (run_id,)).fetchall():
        ranking = c.execute("SELECT patroller_id, score FROM task_rankings WHERE task_id = ? ORDER BY rank_no",
                            (row["task_id"],)).fetchall()
        tasks.append(TaskView(row["task_id"], row["station_id"], row["status"], row["perception_result_id"],
                              tuple(r["patroller_id"] for r in ranking), tuple(float(r["score"]) for r in ranking)))
    head = detections[0] if detections else None
    detection = None if head is None else {
        "result_id": head.result_id, "artifact_id": head.artifact_id, "station_id": head.station_id,
        "status": head.status, "raw_score": head.raw_score, "canonical_class": head.canonical_class}
    current = next((t for t in tasks if t.status not in _SETTLED_TASK), tasks[-1] if tasks else None)
    return DecisionState(
        run_id=run_id, state_version=run["state_version"], run_status=run["status"],
        agent_phase=run["agent_phase"], emergency_latched=bool(run["emergency_latched"]),
        request_text=request_text,
        plan_id=plan["plan_id"] if plan else None, plan_mode=plan["mode"] if plan else None,
        plan_station_ids=tuple(json.loads(plan["station_ids_json"])) if plan else (),
        plan_approved=approved, detection=detection, open_task_station_ids=open_stations,
        task_id=current.task_id if current else None, task_status=current.status if current else None,
        ranked_patrollers=current.ranked_patrollers if current else (),
        detections=tuple(detections), tasks=tuple(tasks),
        proposed_catalogue_plan_ids=proposed)
