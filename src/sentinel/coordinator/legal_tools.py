"""Deterministic legal-action state machine: DecisionState -> fully-argued, described candidates."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .command_gateway import EVIDENCE_SCORE_THRESHOLD
from .decision_state import DecisionState, DetectionView

TERMINAL, WAIT_APPROVAL, SELECT_PLAN, WAIT_OBSERVATION = "TERMINAL", "WAIT_APPROVAL", "SELECT_PLAN", "WAIT_OBSERVATION"
EVIDENCE_GATE, EVIDENCE_VETO, RANK, ASSIGN, DONE = "EVIDENCE_GATE", "EVIDENCE_VETO", "RANK", "ASSIGN", "DONE"
# DETECTION_TRIAGE is a Python alias of the same string as EVIDENCE_GATE (rev
# 1's rename would have broken the v1 fixture and three scripts -- kept as an
# alias instead, per Global Constraints).
DETECTION_TRIAGE = EVIDENCE_GATE
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


def needs_judgment(legal: LegalSet) -> bool:
    """A turn needs a human iff no rule settled it and there is a real choice.
    P2b leaves ``rule_choice = None`` on single-candidate sets too (RANK,
    ASSIGN, the waits) -- ``RulePolicy`` settles those by its
    ``len(candidates) == 1`` branch first, before ever looking at
    ``rule_choice``. So a predicate of ``rule_choice is None`` alone would
    wrongly turn every RULE point into an escalation; this is why both
    conditions are required."""
    return legal.rule_choice is None and len(legal.candidates) > 1


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


def _detections_view(state: DecisionState) -> tuple[DetectionView, ...]:
    """``DecisionState.detections`` (Task 2) is the source of truth for every
    state ``read_decision_state`` produces -- there ``detection`` is always
    the dict form of ``detections[0]``. This falls back to that legacy
    single-``detection`` field only for a ``DecisionState`` built by hand
    with the pre-P3a shape (``task_id`` is ``None``, ``detections`` left at
    its default ``()``), so P2b's own hand-built EVIDENCE_GATE fixtures keep
    working unchanged; it is inert for any real, gateway-driven state."""
    if state.detections:
        return state.detections
    if state.detection is not None and state.task_id is None:
        d = state.detection
        return (DetectionView(d["result_id"], d["artifact_id"], d["station_id"],
                               d.get("status"), d.get("raw_score"), d.get("canonical_class")),)
    return ()


def _triage(state: DecisionState, d: DetectionView, evidence_threshold: float) -> LegalSet:
    """EVIDENCE_GATE/EVIDENCE_VETO for the oldest queued detection ``d``.
    Extends P2b's rule (operator policy 2026-09-20, "Escalate below 0.75"):
    create_task iff inside the approved plan and score >= EVIDENCE_TASK_SCORE.
    P3a adds exactly one new case on top: ``None`` (a judgment point) when the
    detection duplicates an open task or repeats one already handled at the
    same station -- same rock, or a second, distinct hazard?"""
    if d.status != "DETECTED" or d.raw_score is None or d.raw_score < evidence_threshold:
        return LegalSet(EVIDENCE_VETO, (_escalate("evidence not DETECTED or below threshold"),))
    facts = state.detection_facts(d)
    create = Candidate("task.create", {"perception_result_id": d.result_id, "section_id": d.station_id,
                                        "evidence_artifact_id": d.artifact_id},
                       "create_task", "create a patrol task for this detection")
    escalate = _escalate("detection needs operator review before tasking")
    if facts["inside_plan"] is not True or d.raw_score < EVIDENCE_TASK_SCORE:
        rule_choice = ESCALATE_LABEL
    elif facts["duplicates_open_task"] or facts["repeat_of_handled"]:
        rule_choice = None
    else:
        rule_choice = "create_task"
    return LegalSet(EVIDENCE_GATE, (create, escalate), rule_choice=rule_choice)


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
    # Work queue (P3a Task 7): the oldest unreferenced detection is triaged
    # first; only once the detection queue is empty does the oldest task move
    # through RANK then ASSIGN. A vetoed or escalated detection stays at the
    # head forever (blocker A7, proposal P-4) -- not worked around here.
    detections = _detections_view(state)
    if detections:
        return _triage(state, detections[0], evidence_threshold)
    unranked = next((t for t in state.tasks if t.status == "PROPOSED" and not t.ranked_patrollers), None)
    if unranked is not None:
        return LegalSet(RANK, (Candidate("patroller.rank_for_task", {"task_id": unranked.task_id}, "rank",
                                          "rank patrollers for the task"),))
    ranked_unassigned = next((t for t in state.tasks if t.status == "PROPOSED" and t.ranked_patrollers), None)
    if ranked_unassigned is not None:
        return LegalSet(ASSIGN, (Candidate("assignment.propose", {"task_id": ranked_unassigned.task_id,
                         "patroller_id": ranked_unassigned.ranked_patrollers[0], "reason": "deterministic rank-1"},
                         "assign_rank_1", "propose the rank-1 patroller"),))
    if not state.tasks:
        return LegalSet(WAIT_OBSERVATION, (_wait(
            "awaiting station observation", ("DETECTION_RECORDED", "OBSERVATION_CAPTURED"),
        ),))
    return LegalSet(DONE, (_wait(
        "assignment decision pending", ("PATROLLER_ASSIGNMENT_CONFIRMED", "OPERATOR_ASSIGNMENT_REJECTED"),
    ),))
