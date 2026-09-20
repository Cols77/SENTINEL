"""Deterministic legal-action state machine: DecisionState -> fully-argued, described candidates."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .command_gateway import EVIDENCE_SCORE_THRESHOLD
from .decision_state import DecisionState

TERMINAL, WAIT_APPROVAL, SELECT_PLAN, WAIT_OBSERVATION = "TERMINAL", "WAIT_APPROVAL", "SELECT_PLAN", "WAIT_OBSERVATION"
EVIDENCE_GATE, EVIDENCE_VETO, RANK, ASSIGN, DONE = "EVIDENCE_GATE", "EVIDENCE_VETO", "RANK", "ASSIGN", "DONE"
ESCALATE_LABEL = "escalate"

# Operator policy, decided 2026-09-20 ("Make it a rule, keep shadow" / "Escalate
# below 0.75"): at EVIDENCE_GATE the create_task/escalate choice is computable,
# so a deterministic rule decides it (`legal_set()` sets `LegalSet.rule_choice`
# below) -- advisors are still consulted in shadow at that point and recorded,
# they just no longer gate execution. This is distinct from the static veto
# `EVIDENCE_SCORE_THRESHOLD` (below which a detection never reaches
# EVIDENCE_GATE at all -- it is vetoed straight to EVIDENCE_VETO).
EVIDENCE_TASK_SCORE = 0.75

# JUDGMENT_POINTS: where the operator is actually asked to choose (P2 Task 3's
# submit_choice/pending_decision). ADVISORY_POINTS: where shadow advisors are
# consulted and recorded (P2 Task 2's ShadowLadder) -- a superset now that
# EVIDENCE_GATE is rule-decided but still shadowed.
JUDGMENT_POINTS = frozenset({SELECT_PLAN})
ADVISORY_POINTS = frozenset({SELECT_PLAN, EVIDENCE_GATE})


@dataclass(frozen=True)
class Candidate:
    tool_name: str
    arguments: dict
    label: str
    description: str


@dataclass(frozen=True)
class LegalSet:
    decision_point: str
    candidates: tuple[Candidate, ...]
    # The label a deterministic rule selects at an advisory point (e.g.
    # EVIDENCE_GATE), or None where no rule computes one (e.g. SELECT_PLAN,
    # which the operator alone decides). `candidates` stays the full option
    # set regardless, so advisors still choose among all of them.
    rule_choice: str | None = None

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(c.label for c in self.candidates)

    def by_label(self, label: str) -> Candidate:
        return next(c for c in self.candidates if c.label == label)


@dataclass(frozen=True)
class CataloguePlan:
    plan_id: str
    description: str


def load_catalogue_plans(path: Path | None = None) -> tuple[CataloguePlan, ...]:
    path = path or Path(__file__).resolve().parents[3] / "docs/superpowers/specs/contracts/fixtures/survey-catalogue-v1.fixture.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    plans = []
    for entry in doc["entries"]:
        stations = [step["zone_id"] for step in entry.get("station_steps", [])]
        mode = entry.get("mode")
        plans.append(CataloguePlan(
            entry["id"],
            f"mode={mode}; stations={len(stations)} ({', '.join(map(str, stations))}); "
            f"marker verification={'required' if 'MARKER' in str(mode) else 'not required'}",
        ))
    return tuple(plans)


def _escalate(summary: str) -> Candidate:
    return Candidate("finish", {"status": "NEEDS_REVIEW", "summary": summary[:256]}, ESCALATE_LABEL,
                      "hand the decision to the operator with the reason")


def _wait(reason: str, event_types: Sequence[str]) -> Candidate:
    return Candidate("wait_for_event", {"event_types": list(event_types), "reason": reason}, "wait", reason)


def legal_set(state: DecisionState, catalogue: Sequence[CataloguePlan], *,
              evidence_threshold: float = EVIDENCE_SCORE_THRESHOLD) -> LegalSet:
    if state.emergency_latched or state.run_status in {"EMERGENCY", "FAILED", "COMPLETED"}:
        return LegalSet(TERMINAL, ())
    if state.run_status == "WAITING_FOR_APPROVAL":
        return LegalSet(WAIT_APPROVAL, (_wait(
            "operator approval pending",
            ("PLAN_APPROVED", "PLAN_REJECTED", "PATROLLER_ASSIGNMENT_CONFIRMED", "OPERATOR_ASSIGNMENT_REJECTED"),
        ),))
    if state.plan_id is None:
        plans = tuple(Candidate("inspection.propose_plan", {"catalogue_plan_id": p.plan_id}, p.plan_id, p.description)
                       for p in catalogue)
        return LegalSet(SELECT_PLAN, plans + (_escalate("no catalogue plan fits the operator request"),))
    if state.task_id is None:
        det = state.detection
        if det is None:
            return LegalSet(WAIT_OBSERVATION, (_wait(
                "awaiting station observation", ("DETECTION_RECORDED", "OBSERVATION_CAPTURED"),
            ),))
        score = det.get("raw_score")
        if det.get("status") != "DETECTED" or score is None or score < evidence_threshold:
            return LegalSet(EVIDENCE_VETO, (_escalate("evidence not DETECTED or below threshold"),))
        create = Candidate("task.create", {"perception_result_id": det["result_id"], "section_id": det["station_id"],
                                            "evidence_artifact_id": det["artifact_id"]},
                            "create_task", "create a patrol task for this detection")
        escalate = _escalate("detection needs operator review before tasking")
        # Operator policy 2026-09-20: create_task iff the detection is inside
        # the approved plan AND raw_score >= EVIDENCE_TASK_SCORE; otherwise
        # escalate (marginal-band and out-of-plan detections alike).
        inside_plan = det["station_id"] in state.plan_station_ids
        rule_choice = "create_task" if inside_plan and score >= EVIDENCE_TASK_SCORE else ESCALATE_LABEL
        return LegalSet(EVIDENCE_GATE, (create, escalate), rule_choice=rule_choice)
    if not state.ranked_patrollers:
        return LegalSet(RANK, (Candidate("patroller.rank_for_task", {"task_id": state.task_id}, "rank",
                                          "rank patrollers for the task"),))
    if state.task_status not in {"ASSIGNMENT_PROPOSED", "ASSIGNED"}:
        return LegalSet(ASSIGN, (Candidate("assignment.propose", {"task_id": state.task_id,
                         "patroller_id": state.ranked_patrollers[0], "reason": "deterministic rank-1"},
                         "assign_rank_1", "propose the rank-1 patroller"),))
    return LegalSet(DONE, (_wait(
        "assignment decision pending", ("PATROLLER_ASSIGNMENT_CONFIRMED", "OPERATOR_ASSIGNMENT_REJECTED"),
    ),))
