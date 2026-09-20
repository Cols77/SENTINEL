"""Drive the real coordinator gateway to produce a representative decision-turn
fixture (P2 Task 1). Every turn's ``packet``/``candidates`` come from
``DecisionState.to_packet()``/``legal_set`` computed on state built by
submitting real commands through ``CommandGateway`` -- never hand-written.

``generate_turns()`` is the single entry point. It returns one dict per turn:
``SELECT_PLAN`` (24 request variants) and ``EVIDENCE_GATE`` (24 perception
variants) are judgment points and carry a hand label with a rationale; the
five RULE points (``WAIT_APPROVAL``, ``WAIT_OBSERVATION``, ``EVIDENCE_VETO``,
``RANK``, ``ASSIGN``) carry exactly one candidate each, labelled
automatically from that candidate.
"""
from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path
from typing import Any

from sentinel.coordinator.command_gateway import CommandGateway, EVIDENCE_SCORE_THRESHOLD
from sentinel.coordinator.decision_state import read_decision_state
from sentinel.coordinator.legal_tools import EVIDENCE_TASK_SCORE, legal_set, load_catalogue_plans

from .harness import envelope, open_harness

GENERATOR_REVISION = "turns.v1"
NOW = "2026-09-17T13:00:00Z"

ROUTE_PLAN_ID = "PLAN_ROUTE_ONLY_2_ZONE"
# The approved plan is always PLAN_ROUTE_ONLY_2_ZONE (LEFT_ALLEY_MIDDLE, RIGHT_ALLEY_MIDDLE).
# "Outside" stations are real catalogue zones (from PLAN_MARKER_VERIFIED_6) that are simply
# not part of that approved plan.
INSIDE_STATIONS = ("LEFT_ALLEY_MIDDLE", "RIGHT_ALLEY_MIDDLE")
OUTSIDE_STATIONS = ("LEFT_ALLEY_FRONT", "RIGHT_ALLEY_BACK")
EVIDENCE_SCORES = (0.51, 0.55, 0.62, 0.78, 0.9, 0.99)

# --- SELECT_PLAN request variants ------------------------------------------------

_SELECT_PLAN_REQUESTS: tuple[tuple[str, str], ...] = (
    ("single", "inspect only the left alley middle station"),
    ("single", "check just the right alley middle station"),
    ("single", "look at the left alley middle spot only"),
    ("single", "survey the right alley middle zone, nothing else"),
    ("single", "single station check: left alley middle"),
    ("single", "just the right alley middle, one look"),
    ("sweep", "full verified survey of all marked stations"),
    ("sweep", "run the complete marker-verified sweep of all six zones"),
    ("sweep", "verify every station with its marker, full survey"),
    ("sweep", "do the whole six-zone marker check"),
    ("sweep", "complete inspection of all stations with marker verification"),
    ("sweep", "sweep the entire site and verify each marker"),
    ("ambiguous", "have a look around the alley"),
    ("ambiguous", "check things out near the alleys"),
    ("ambiguous", "take a look and see what's there"),
    ("ambiguous", "go check on the site generally"),
    ("out_of_catalogue", "inspect the roof drains"),
    ("out_of_catalogue", "check the parking lot for anything unusual"),
    ("out_of_catalogue", "survey the north fence line"),
    ("out_of_catalogue", "look at the rooftop hvac units"),
    ("contradictory", "one station only, but verify all six markers"),
    ("contradictory", "just a quick single-zone look, but confirm every marker across the site"),
    ("contradictory", "inspect only the left alley middle, and also run the full six-zone marker sweep"),
    ("contradictory", "single station route only, full marker verification required everywhere"),
)

_SELECT_PLAN_RATIONALE = {
    # Operator decision 2026-09-20: "Seems like PLAN_ROUTE_ONLY_2_ZONE does
    # not make sense if you're visiting one zone?" -> "Escalate, and propose
    # a catalogue entry". No catalogue plan covers exactly one station --
    # PLAN_ROUTE_ONLY_2_ZONE, the narrowest one, visits two -- so a
    # single-station request has no matching plan and must escalate. A WP0
    # proposal for a 1-zone catalogue entry is pending separately (Task C).
    "single": lambda text: (
        "escalate", "hand",
        f'the request names exactly one station ("{text}"); no catalogue plan covers exactly '
        "one station -- PLAN_ROUTE_ONLY_2_ZONE, the narrowest catalogue plan, visits two -- so "
        "escalate; a WP0 proposal for a 1-zone catalogue entry is pending (Task C)",
    ),
    "sweep": lambda text: (
        "PLAN_MARKER_VERIFIED_6", "hand",
        f'the request asks for a full or marker-verified survey ("{text}"); the six-zone '
        "marker-verified plan matches",
    ),
    "ambiguous": lambda text: (
        "escalate", "hand",
        f'the request does not identify which station(s) to inspect ("{text}"); escalate to the '
        "operator to clarify scope",
    ),
    "out_of_catalogue": lambda text: (
        "escalate", "hand",
        f'the request targets a location outside the survey catalogue ("{text}"); escalate rather '
        "than guess a plan",
    ),
    "contradictory": lambda text: (
        "escalate", "hand",
        f'the request contradicts itself on scope ("{text}"); escalate for operator clarification',
    ),
}

# --- EVIDENCE_GATE perception variants -------------------------------------------
# (raw_score, inside_plan, station_index)
#
# Deviation from the plan: the plan also asked for a "duplicate of an open task at the
# same station" dimension. That state is unreachable through the real gateway: reaching
# EVIDENCE_GATE at all requires DecisionState.task_id is None (legal_tools.legal_set()),
# and read_decision_state's open_task_station_ids is derived from the very same
# per-run `tasks` table as task_id -- so task_id can only be None when that table is
# empty for the run, which means open_task_station_ids is also empty. A duplicate-open-
# task fact and a live EVIDENCE_GATE turn cannot coexist in one run under P1's current,
# one-task-per-run DecisionState model. In place of that unreachable dimension, the
# second axis instead varies which of the two real catalogue stations is hit within each
# of "inside the approved plan" / "outside it", keeping the variant count at 24 and the
# inside/outside and score-band label boundaries the plan cares about.
_EVIDENCE_GATE_VARIANTS: tuple[tuple[float, bool, int], ...] = tuple(
    (score, inside, station_index)
    for score in EVIDENCE_SCORES
    for inside in (True, False)
    for station_index in (0, 1)
)


def _evidence_gate_label(raw_score: float, inside: bool, station_id: str) -> tuple[str, str, str]:
    # Operator policy 2026-09-20 ("Escalate below 0.75"): create_task iff the
    # detection is inside the approved plan AND raw_score >= EVIDENCE_TASK_SCORE
    # (0.75); otherwise escalate. This mirrors legal_tools.legal_set()'s
    # deterministic rule_choice (Task A) exactly -- these hand labels are now
    # a rule-agreement check, not an independent judgment benchmark.
    at_or_above_cut = raw_score >= EVIDENCE_TASK_SCORE
    if not inside:
        return (
            "escalate", "hand",
            f"score {raw_score} at {station_id} lies outside the approved plan's stations; the "
            f"{EVIDENCE_TASK_SCORE} create-task cut only applies inside the plan, so escalate for "
            "operator review",
        )
    if not at_or_above_cut:
        return (
            "escalate", "hand",
            f"score {raw_score} at {station_id} is inside the approved plan but below the operator's "
            f"{EVIDENCE_TASK_SCORE} create-task cut (\"Escalate below 0.75\"); escalate rather than "
            "auto-task",
        )
    return (
        "create_task", "hand",
        f"score {raw_score} at {station_id} is inside the approved plan and meets the operator's "
        f"{EVIDENCE_TASK_SCORE} create-task cut (\"Escalate below 0.75\"); create the patrol task",
    )


# --- gateway-driving helpers -------------------------------------------------------


def _run_id_for(turn_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"sentinel-turns.v1:{turn_id}"))


class _Sequencer:
    def __init__(self) -> None:
        self._n = 0

    def next(self) -> int:
        self._n += 1
        return self._n


def _submit(gateway: CommandGateway, run_id: str, seq: _Sequencer, command_type: str, payload: dict,
            *, actor_type: str = "AGENT", expected: int | None = None) -> dict:
    return gateway.submit(envelope(command_type, payload, sequence=seq.next(), run_id=run_id,
                                    actor_type=actor_type, expected=expected))


def _state_version(repo, run_id: str) -> int:
    row = repo.connection.execute("SELECT state_version FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return int(row["state_version"])


def _create_run(gateway: CommandGateway, run_id: str, seq: _Sequencer, request_text: str) -> None:
    _submit(gateway, run_id, seq, "RUN_CREATE", {"request_text": request_text}, actor_type="OPERATOR")


def _propose_and_approve_route_plan(gateway: CommandGateway, repo, run_id: str, seq: _Sequencer) -> dict[str, Any]:
    _submit(gateway, run_id, seq, "AGENT_INTENT_PROPOSE",
            {"intent": "REQUEST_OPERATOR_APPROVAL", "catalogue_plan_id": ROUTE_PLAN_ID}, actor_type="AGENT")
    row = repo.connection.execute(
        "SELECT plan_id, catalogue_id, catalogue_revision, plan_sha256, station_ids_json FROM plans WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    reference = {
        "plan_id": row["plan_id"], "catalogue_id": row["catalogue_id"],
        "catalogue_revision": row["catalogue_revision"], "plan_sha256": row["plan_sha256"],
        "station_ids": json.loads(row["station_ids_json"]),
    }
    _submit(gateway, run_id, seq, "OPERATOR_DECIDE",
            {"decision": "CONFIRM", "target": {"kind": "PLAN", "plan_reference": reference}}, actor_type="OPERATOR",
            expected=_state_version(repo, run_id))
    return reference


def _propose_route_plan_only(gateway: CommandGateway, run_id: str, seq: _Sequencer) -> None:
    _submit(gateway, run_id, seq, "AGENT_INTENT_PROPOSE",
            {"intent": "REQUEST_OPERATOR_APPROVAL", "catalogue_plan_id": ROUTE_PLAN_ID}, actor_type="AGENT")


def _perception_payload(seed: str, plan_id: str, plan_sha256: str, station_id: str, raw_score: float | None,
                         status: str = "DETECTED") -> dict[str, Any]:
    result_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"result:{seed}"))
    observation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"observation:{seed}"))
    artifact_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"artifact:{seed}"))
    request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"request:{seed}"))
    provider_profile_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "sentinel-turns.v1:provider-profile"))
    return {
        "contract_version": "sentinel.v0", "wire_type": "perception_result",
        "result_id": result_id, "request_id": request_id,
        "observation_id": observation_id, "artifact_id": artifact_id,
        "artifact_sha256": "a" * 64, "artifact_source_kind": "SIMULATED",
        "judged_path": False, "provider_profile_id": provider_profile_id,
        "canonical_class": "rock", "source_class": "rock", "raw_score": raw_score,
        "score_semantics": "provider-defined", "status": status, "provider": "simulated",
        "model": "simulated-detector", "model_revision": "v1", "prompt_revision": "v1",
        "transform_revision": "v1", "class_mapping_revision": "v0-rock-only",
        "latency_ms": 1, "diagnostic_artifact_id": None,
        "source_kind": "SIMULATED", "created_at": NOW,
        "station_capture": {
            "plan_id": plan_id, "plan_sha256": plan_sha256, "plan_step": 1,
            "station_id": station_id, "zone_id": station_id,
            "marker_id": None, "height_m": 1.2, "artifact_id": artifact_id,
            "artifact_sha256": "a" * 64, "capture_index": 1, "captured_at": NOW,
        },
    }


def _ingest_perception(gateway: CommandGateway, run_id: str, seq: _Sequencer, payload: dict) -> None:
    _submit(gateway, run_id, seq, "PERCEPTION_INGEST", {"perception_result": payload}, actor_type="WORKER")


def _create_task(gateway: CommandGateway, run_id: str, seq: _Sequencer, perception: dict) -> None:
    _submit(gateway, run_id, seq, "TASK_CREATE", {
        "perception_result_id": perception["result_id"],
        "section_id": perception["station_capture"]["station_id"],
        "evidence_artifact_id": perception["artifact_id"],
    }, actor_type="AGENT")


def _capture_turn(repo, catalogue, run_id: str, turn_id: str, *, expected_point: str | None = None) -> dict[str, Any]:
    state = read_decision_state(repo, run_id)
    legal = legal_set(state, catalogue, evidence_threshold=EVIDENCE_SCORE_THRESHOLD)
    if expected_point is not None and legal.decision_point != expected_point:
        raise AssertionError(f"{turn_id}: expected decision point {expected_point!r}, got {legal.decision_point!r}")
    return {
        "turn_id": turn_id,
        "decision_point": legal.decision_point,
        "packet": state.to_packet(),
        "candidates": [{"label": c.label, "description": c.description} for c in legal.candidates],
        "generator_revision": GENERATOR_REVISION,
    }


# --- turn builders -----------------------------------------------------------------


def _generate_select_plan_turns(gateway: CommandGateway, repo, catalogue) -> list[dict[str, Any]]:
    turns = []
    for index, (category, request_text) in enumerate(_SELECT_PLAN_REQUESTS, start=1):
        turn_id = f"sp-{index:03d}"
        run_id = _run_id_for(turn_id)
        seq = _Sequencer()
        _create_run(gateway, run_id, seq, request_text)
        turn = _capture_turn(repo, catalogue, run_id, turn_id, expected_point="SELECT_PLAN")
        label, label_source, rationale = _SELECT_PLAN_RATIONALE[category](request_text)
        turn.update(label=label, label_source=label_source, rationale=rationale)
        turns.append(turn)
    return turns


def _generate_evidence_gate_turns(gateway: CommandGateway, repo, catalogue) -> list[dict[str, Any]]:
    turns = []
    for index, (raw_score, inside, station_index) in enumerate(_EVIDENCE_GATE_VARIANTS, start=1):
        turn_id = f"eg-{index:03d}"
        run_id = _run_id_for(turn_id)
        seq = _Sequencer()
        _create_run(gateway, run_id, seq, "route-only survey of the alley stations")
        reference = _propose_and_approve_route_plan(gateway, repo, run_id, seq)
        station_id = (INSIDE_STATIONS if inside else OUTSIDE_STATIONS)[station_index]
        current = _perception_payload(turn_id, reference["plan_id"], reference["plan_sha256"], station_id, raw_score)
        _ingest_perception(gateway, run_id, seq, current)
        turn = _capture_turn(repo, catalogue, run_id, turn_id, expected_point="EVIDENCE_GATE")
        label, label_source, rationale = _evidence_gate_label(raw_score, inside, station_id)
        turn.update(label=label, label_source=label_source, rationale=rationale)
        turns.append(turn)
    return turns


def _rule_turn(turn: dict[str, Any]) -> dict[str, Any]:
    candidate = turn["candidates"][0]
    turn.update(
        label=candidate["label"],
        label_source="rule",
        rationale=f"single legal candidate at {turn['decision_point']}; rule executes deterministically: "
                   f"{candidate['description']}",
    )
    return turn


def _generate_rule_turns(gateway: CommandGateway, repo, catalogue) -> list[dict[str, Any]]:
    turns = []

    # WAIT_APPROVAL: plan proposed, not yet approved.
    turn_id = "rule-wait-approval"
    run_id = _run_id_for(turn_id)
    seq = _Sequencer()
    _create_run(gateway, run_id, seq, "route-only survey of the alley stations")
    _propose_route_plan_only(gateway, run_id, seq)
    turns.append(_rule_turn(_capture_turn(repo, catalogue, run_id, turn_id, expected_point="WAIT_APPROVAL")))

    # WAIT_OBSERVATION: plan approved, no detection yet.
    turn_id = "rule-wait-observation"
    run_id = _run_id_for(turn_id)
    seq = _Sequencer()
    _create_run(gateway, run_id, seq, "route-only survey of the alley stations")
    _propose_and_approve_route_plan(gateway, repo, run_id, seq)
    turns.append(_rule_turn(_capture_turn(repo, catalogue, run_id, turn_id, expected_point="WAIT_OBSERVATION")))

    # EVIDENCE_VETO: detection below the evidence threshold.
    turn_id = "rule-evidence-veto"
    run_id = _run_id_for(turn_id)
    seq = _Sequencer()
    _create_run(gateway, run_id, seq, "route-only survey of the alley stations")
    reference = _propose_and_approve_route_plan(gateway, repo, run_id, seq)
    below_threshold = _perception_payload(turn_id, reference["plan_id"], reference["plan_sha256"],
                                           reference["station_ids"][0], 0.35)
    _ingest_perception(gateway, run_id, seq, below_threshold)
    turns.append(_rule_turn(_capture_turn(repo, catalogue, run_id, turn_id, expected_point="EVIDENCE_VETO")))

    # RANK: task created, not yet ranked.
    turn_id = "rule-rank"
    run_id = _run_id_for(turn_id)
    seq = _Sequencer()
    _create_run(gateway, run_id, seq, "route-only survey of the alley stations")
    reference = _propose_and_approve_route_plan(gateway, repo, run_id, seq)
    detected = _perception_payload(turn_id, reference["plan_id"], reference["plan_sha256"],
                                    reference["station_ids"][0], 0.99)
    _ingest_perception(gateway, run_id, seq, detected)
    _create_task(gateway, run_id, seq, detected)
    turns.append(_rule_turn(_capture_turn(repo, catalogue, run_id, turn_id, expected_point="RANK")))

    # ASSIGN: task ranked, not yet proposed for assignment.
    turn_id = "rule-assign"
    run_id = _run_id_for(turn_id)
    seq = _Sequencer()
    _create_run(gateway, run_id, seq, "route-only survey of the alley stations")
    reference = _propose_and_approve_route_plan(gateway, repo, run_id, seq)
    detected = _perception_payload(turn_id, reference["plan_id"], reference["plan_sha256"],
                                    reference["station_ids"][0], 0.99)
    _ingest_perception(gateway, run_id, seq, detected)
    _create_task(gateway, run_id, seq, detected)
    task_row = repo.connection.execute("SELECT task_id FROM tasks WHERE run_id = ?", (run_id,)).fetchone()
    _submit(gateway, run_id, seq, "TASK_RANK", {"task_id": task_row["task_id"]}, actor_type="AGENT")
    turns.append(_rule_turn(_capture_turn(repo, catalogue, run_id, turn_id, expected_point="ASSIGN")))

    return turns


def generate_turns() -> list[dict[str, Any]]:
    """Drive the real gateway and return every turn: 24 ``SELECT_PLAN``, 24
    ``EVIDENCE_GATE``, and 5 single-candidate RULE points, each fully
    populated (packet, candidates, label, label_source, rationale)."""
    catalogue = load_catalogue_plans()
    with tempfile.TemporaryDirectory() as tmp:
        repo, gateway = open_harness(Path(tmp))
        try:
            turns: list[dict[str, Any]] = []
            turns.extend(_generate_select_plan_turns(gateway, repo, catalogue))
            turns.extend(_generate_evidence_gate_turns(gateway, repo, catalogue))
            turns.extend(_generate_rule_turns(gateway, repo, catalogue))
        finally:
            repo.close()
    return turns


__all__ = ["GENERATOR_REVISION", "generate_turns"]
