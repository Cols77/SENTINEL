"""Drive the real coordinator gateway to produce the P3a decision-turn fixture v2
(P3a Task 10). Same method as v1 (``tests/coordinator/turn_generator.py``,
``tests/coordinator/test_turn_fixture.py``): every turn's ``packet``/``candidates``
come from ``DecisionState.to_packet_v2()``/``legal_set`` computed on state built by
submitting real commands through ``CommandGateway`` (via ``P3Run``,
``tests/coordinator/p3_helpers.py``) -- never hand-written.

``generate_turns()`` is the single entry point. It returns one dict per turn, each
carrying v1's fields plus ``rule_choice``:

- ``SELECT_PLAN`` against the **draft** rich catalogue (Task 1,
  ``fixtures/catalogue_draft/survey-catalogue-v2.draft.json`` -- never proposed
  through the gateway, only fed to ``legal_set`` for enumeration): 56 scored
  requests (42 exact per-plan matches, 5 ambiguous, 5 out-of-catalogue,
  4 contradictory) plus 24 generated requests in the former pending slots.
  Generated expectations are not independently labelled operator evidence.
- ``EVIDENCE_GATE`` duplicate turns (``rule_choice is None``): a detection that
  duplicates an already-tasked station, varied by score band, whether the
  earlier task is ranked, and whether a second pending detection is queued
  behind it.
- ``ASSIGN`` near-tie turns (``rule_choice is None``): a patroller ranking whose
  top two scores are within ``ASSIGNMENT_CLEAR_GAP``, varied by gap band and
  patroller count.
- 8 RULE turns, one per rule outcome (label_source ``"rule"``, label = the
  ``rule_choice`` when one was computed, else the sole legal candidate's label).

REPLAN is **not** a decision point on this branch: Task 9 was implemented and
then dropped (commit ``8248355``) because it degenerated and overrode an
operator-approved label (see that commit's message and
``src/sentinel/coordinator/replan.py``'s module docstring). No REPLAN turns are
generated here, and none should be expected.

Honest sizing (rev 1's 80/80/60/60 targets are not reachable): packets carry
bands, not raw values, so distinct reachable states are few (blocker A12).
``generate_turns()`` builds every raw combination the plan's tables ask for and
keeps only the turns whose ``(decision_point, packet, candidates)`` triple is
new (see ``_dedupe_and_relabel``) -- it never pads with duplicates to hit a
target. Counts actually achieved are recorded in
``fixtures/decisions_v2/README.md``.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Callable

from sentinel.coordinator.legal_tools import legal_set, load_catalogue_plans
from sentinel.coordinator.tasks import SimulatedPatroller

from .p3_helpers import LEFT_FRONT, LEFT_MID, RIGHT_MID, P3Run
from .test_catalogue_draft import DRAFT_CATALOGUE_PATH

GENERATOR_REVISION = "turns.v2"

# --- SELECT_PLAN request variants against the draft catalogue --------------------

_DRAFT_PLAN_REQUESTS: dict[str, tuple[str, str, str]] = {
    "PLAN_ROUTE_LEFT_ALLEY_FRONT": (
        "route only, left alley front station",
        "just the left alley front spot, no marker check",
        "single stop: left alley front, route pass only",
    ),
    "PLAN_ROUTE_LEFT_ALLEY_MIDDLE": (
        "route only, left alley middle station",
        "just the left alley middle spot, no marker check",
        "single stop: left alley middle, route pass only",
    ),
    "PLAN_ROUTE_LEFT_ALLEY_BACK": (
        "route only, left alley back station",
        "just the left alley back spot, no marker check",
        "single stop: left alley back, route pass only",
    ),
    "PLAN_ROUTE_RIGHT_ALLEY_FRONT": (
        "route only, right alley front station",
        "just the right alley front spot, no marker check",
        "single stop: right alley front, route pass only",
    ),
    "PLAN_ROUTE_RIGHT_ALLEY_MIDDLE": (
        "route only, right alley middle station",
        "just the right alley middle spot, no marker check",
        "single stop: right alley middle, route pass only",
    ),
    "PLAN_ROUTE_RIGHT_ALLEY_BACK": (
        "route only, right alley back station",
        "just the right alley back spot, no marker check",
        "single stop: right alley back, route pass only",
    ),
    "PLAN_ROUTE_LEFT_ALLEY": (
        "route the whole left alley, front through back, no marker check",
        "drive the left alley end to end, route only",
        "left alley full pass: front, middle, and back stations, route only",
    ),
    "PLAN_ROUTE_RIGHT_ALLEY": (
        "route the whole right alley, front through back, no marker check",
        "drive the right alley end to end, route only",
        "right alley full pass: front, middle, and back stations, route only",
    ),
    "PLAN_ROUTE_FRONT_PAIR": (
        "route only, both alley front stations, left and right",
        "front of both alleys, route pass, no markers",
        "left alley front and right alley front, that's it",
    ),
    "PLAN_ROUTE_MIDDLE_PAIR": (
        "route only, both alley middle stations, left and right",
        "middle of both alleys, route pass, no markers",
        "left alley middle and right alley middle, that's it",
    ),
    "PLAN_ROUTE_BACK_PAIR": (
        "route only, both alley back stations, left and right",
        "back of both alleys, route pass, no markers",
        "left alley back and right alley back, that's it",
    ),
    "PLAN_VERIFIED_LEFT_ALLEY": (
        "verify every marker down the left alley, front through back",
        "marker-verified sweep of the left alley, all three stations",
        "left alley marker check: front, middle, and back, confirm each tag",
    ),
    "PLAN_VERIFIED_RIGHT_ALLEY": (
        "verify every marker down the right alley, front through back",
        "marker-verified sweep of the right alley, all three stations",
        "right alley marker check: front, middle, and back, confirm each tag",
    ),
    "PLAN_VERIFIED_FULL_SITE": (
        "full marker-verified survey of the entire site, all six stations",
        "verify every marker across both alleys, complete site check",
        "complete six-zone inspection with marker verification everywhere",
    ),
}

_AMBIGUOUS = (
    "have a look around when you get a chance",
    "check on the site, whatever needs it",
    "go take a look and report back",
    "see what's going on out there",
    "do a once-over of the area",
)
_OUT_OF_CATALOGUE = (
    "inspect the loading dock",
    "check the rooftop solar array",
    "survey the perimeter fence",
    "look at the generator shed",
    "check the visitor parking area",
)
_CONTRADICTORY = (
    "just the left alley front, but also verify every marker site-wide",
    "route-only single station, and a full six-zone marker sweep too",
    "one quick stop only, but confirm all markers everywhere",
    "left alley middle route only, plus complete marker verification of the whole site",
)

_SINGLE_STATION_PLAN_IDS = frozenset(
    f"PLAN_ROUTE_{s}" for s in ("LEFT_ALLEY_FRONT", "LEFT_ALLEY_MIDDLE", "LEFT_ALLEY_BACK",
                                 "RIGHT_ALLEY_FRONT", "RIGHT_ALLEY_MIDDLE", "RIGHT_ALLEY_BACK"))


def _draft_match_rationale(plan_id: str, text: str) -> str:
    # Headline result (Task 10): the rich draft catalogue flips the v1 label for
    # every single-station request. v1's fixture escalated sp-001..sp-006 because
    # no v1 catalogue plan covered exactly one station (PLAN_ROUTE_ONLY_2_ZONE,
    # the narrowest v1 plan, visits two); the draft catalogue's coverage-named
    # per-station plans give these an exact match instead -- proposal item P-3
    # deletes this whole escalation class.
    note = ""
    if plan_id in _SINGLE_STATION_PLAN_IDS:
        note = (" This flips the v1 fixture's label for a single-station request: v1 escalated it "
                 "because no v1 catalogue plan covered exactly one station (PLAN_ROUTE_ONLY_2_ZONE, "
                 "the narrowest v1 plan, visits two); the draft catalogue's coverage-named "
                 f"{plan_id} gives it an exact match instead, deleting that whole escalation class -- "
                 "the headline evidence for proposal item P-3, the rich catalogue.")
    return (f'the request names the coverage of {plan_id} exactly ("{text}"); no other draft catalogue '
            f"plan matches as precisely.{note}")


_ESCALATE_RATIONALE: dict[str, Callable[[str], str]] = {
    "ambiguous": lambda text: (
        f'the request does not identify which station(s) to inspect ("{text}"); escalate to the '
        "operator to clarify scope"),
    "out_of_catalogue": lambda text: (
        f'the request targets a location outside the survey catalogue, draft or v1 ("{text}"); '
        "escalate rather than guess a plan"),
    "contradictory": lambda text: (
        f'the request contradicts itself on scope ("{text}"); escalate for operator clarification'),
}

GENERATED_REQUESTS_PATH = Path(__file__).resolve().parents[2] / "fixtures/decisions_v2/generated-requests.v1.json"

# --- EVIDENCE_GATE duplicate variants --------------------------------------------

_DUPLICATE_SCORES = (0.78, 0.90, 0.99)
_DUPLICATE_STATIONS = (LEFT_MID, RIGHT_MID)

# --- ASSIGN near-tie variants -----------------------------------------------------

_NEAR_TIE_GAPS = (0.00, 0.02, 0.06)
_NEAR_TIE_COUNTS = (2, 3)


# --- gateway-driving helpers -------------------------------------------------------


def _new_run(tmp_path: Path, turn_id: str, request: str = "route-only survey of the alley stations") -> P3Run:
    sub = tmp_path / turn_id
    sub.mkdir(parents=True, exist_ok=True)
    return P3Run(sub, turn_id, request=request)


def _capture_turn_v2(run: P3Run, catalogue, turn_id: str, *, expected_point: str | None = None) -> dict[str, Any]:
    state = run.state()
    legal = legal_set(state, catalogue)
    if expected_point is not None and legal.decision_point != expected_point:
        raise AssertionError(f"{turn_id}: expected decision point {expected_point!r}, got {legal.decision_point!r}")
    return {
        "turn_id": turn_id,
        "decision_point": legal.decision_point,
        "packet": state.to_packet_v2(),
        "candidates": [{"label": c.label, "description": c.description} for c in legal.candidates],
        "rule_choice": legal.rule_choice,
        "generator_revision": GENERATOR_REVISION,
    }


def _dedupe_and_relabel(raw_turns: list[dict[str, Any]], *, prefix: str, label: str, label_source: str,
                         rationale) -> list[dict[str, Any]]:
    """Keep only the turns whose (decision_point, packet, candidates) triple is
    new, in the order first seen; renumber and hand-label the survivors. This is
    the ``generate what IS distinct`` step -- it never pads a short list back up
    to a target count."""
    seen: set[tuple[str, str, str]] = set()
    ordered: list[dict[str, Any]] = []
    for turn in raw_turns:
        key = (turn["decision_point"], json.dumps(turn["packet"], sort_keys=True),
               json.dumps(turn["candidates"], sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        ordered.append(turn)
    result = []
    for index, turn in enumerate(ordered, start=1):
        turn = dict(turn)
        turn["turn_id"] = f"{prefix}-{index:02d}"
        text = rationale(turn) if callable(rationale) else rationale
        turn.update(label=label, label_source=label_source, rationale=text)
        result.append(turn)
    return result


def _assert_rule_label(turn_id: str, turn: dict[str, Any], expected: str) -> None:
    label = turn["rule_choice"] or turn["candidates"][0]["label"]
    if label != expected:
        raise AssertionError(f"{turn_id}: expected rule label {expected!r}, got {label!r}")


def _finalize_rule_turn(turn: dict[str, Any]) -> dict[str, Any]:
    label = turn["rule_choice"] or turn["candidates"][0]["label"]
    candidate = next(c for c in turn["candidates"] if c["label"] == label)
    reason = (f"rule_choice={turn['rule_choice']!r}" if turn["rule_choice"] is not None
              else "single legal candidate")
    turn.update(label=label, label_source="rule",
                rationale=f"rule-settled at {turn['decision_point']} ({reason}): {candidate['description']}")
    return turn


# --- SELECT_PLAN (draft catalogue) --------------------------------------------------


def _generate_select_plan_scored_turns(tmp_path: Path, draft_catalogue) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    index = 0
    for plan_id, phrasings in _DRAFT_PLAN_REQUESTS.items():
        for text in phrasings:
            index += 1
            turn_id = f"sp2-{index:03d}"
            run = _new_run(tmp_path, turn_id, request=text)
            try:
                turn = _capture_turn_v2(run, draft_catalogue, turn_id, expected_point="SELECT_PLAN")
            finally:
                run.close()
            turn.update(label=plan_id, label_source="hand", rationale=_draft_match_rationale(plan_id, text))
            turns.append(turn)
    for category, texts in (("ambiguous", _AMBIGUOUS), ("out_of_catalogue", _OUT_OF_CATALOGUE),
                             ("contradictory", _CONTRADICTORY)):
        for text in texts:
            index += 1
            turn_id = f"sp2-{index:03d}"
            run = _new_run(tmp_path, turn_id, request=text)
            try:
                turn = _capture_turn_v2(run, draft_catalogue, turn_id, expected_point="SELECT_PLAN")
            finally:
                run.close()
            turn.update(label="escalate", label_source="hand", rationale=_ESCALATE_RATIONALE[category](text))
            turns.append(turn)
    return turns


def _generate_select_plan_pending_turns(tmp_path: Path, draft_catalogue) -> list[dict[str, Any]]:
    """Drive canonical generated requests through the gateway, retaining old IDs."""
    source = json.loads(GENERATED_REQUESTS_PATH.read_text(encoding="utf-8"))
    if source.get("version") != "generated-requests.v1" or source.get("provenance") != "generated" or source.get("catalogue_status") != "draft":
        raise ValueError("unsupported generated request provenance")
    rows = source["requests"]
    expected_ids = {f"sp2-pending-{i:02d}" for i in range(1, 25)}
    if len(rows) != 24 or {row["turn_id"] for row in rows} != expected_ids:
        raise ValueError("generated request asset must fill exactly the 24 former slots")
    turns = []
    for row in rows:
        turn_id = row["turn_id"]
        if not row["request"].strip() or not row["rationale"].strip():
            raise ValueError("generated requests and rationales must be nonblank")
        run = _new_run(tmp_path, turn_id, request=row["request"])
        try:
            turn = _capture_turn_v2(run, draft_catalogue, turn_id, expected_point="SELECT_PLAN")
        finally:
            run.close()
        if row["label"] not in {candidate["label"] for candidate in turn["candidates"]}:
            raise ValueError(f"{turn_id}: generated label is outside the draft catalogue")
        turn.update(label=row["label"], label_source="generated", rationale=row["rationale"])
        turns.append(turn)
    return turns


# --- EVIDENCE_GATE duplicate -------------------------------------------------------


def _generate_duplicate_turns(tmp_path: Path, catalogue) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    for score in _DUPLICATE_SCORES:
        for station in _DUPLICATE_STATIONS:
            for ranked in (False, True):
                for extra_pending in (False, True):
                    raw_id = f"eg-dup-raw-{score}-{station}-{ranked}-{extra_pending}"
                    run = _new_run(tmp_path, raw_id)
                    try:
                        run.approve_route_plan()
                        earlier = run.ingest("a", station, 0.90)
                        task_id = run.create_task(earlier)
                        if ranked:
                            run.rank(task_id)
                        run.ingest("b", station, score)
                        if extra_pending:
                            other = RIGHT_MID if station == LEFT_MID else LEFT_MID
                            run.ingest("c", other, 0.90)
                        turn = _capture_turn_v2(run, catalogue, raw_id, expected_point="EVIDENCE_GATE")
                    finally:
                        run.close()
                    if turn["rule_choice"] is not None:
                        raise AssertionError(
                            f"{raw_id}: expected a duplicate judgment point (rule_choice None), "
                            f"got {turn['rule_choice']!r}")
                    if turn["candidates"] and [c["label"] for c in turn["candidates"]] != ["create_task", "escalate"]:
                        raise AssertionError(f"{raw_id}: unexpected candidate labels {turn['candidates']!r}")
                    raw.append(turn)
    return _dedupe_and_relabel(
        raw, prefix="eg-dup", label="escalate", label_source="hand",
        rationale="no fact in the packet distinguishes a second hazard from a repeat sighting")


# --- ASSIGN near-tie ----------------------------------------------------------------


def _generate_near_tie_turns(tmp_path: Path, catalogue) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    for gap in _NEAR_TIE_GAPS:
        for count in _NEAR_TIE_COUNTS:
            raw_id = f"assign-tie-raw-{gap}-{count}"
            run = _new_run(tmp_path, raw_id)
            try:
                run.approve_route_plan()
                task_id = run.create_task(run.ingest("a", LEFT_MID, 0.90))
                top = 0.80
                scores = {"PATROLLER_1": top, "PATROLLER_2": round(top - gap, 6)}
                if count == 3:
                    scores["PATROLLER_3"] = 0.10
                SimulatedPatroller(run.repo).rank_for_task(task_id, scores)
                turn = _capture_turn_v2(run, catalogue, raw_id, expected_point="ASSIGN")
            finally:
                run.close()
            if turn["rule_choice"] is not None:
                raise AssertionError(
                    f"{raw_id}: expected a near-tie judgment point (rule_choice None), "
                    f"got {turn['rule_choice']!r}")
            raw.append(turn)

    def _rationale(turn: dict[str, Any]) -> str:
        band = turn["packet"]["assignment"]["rank_gap_band"]
        n = turn["packet"]["assignment"]["patrollers_ranked"]
        return (f"the top two ranked patrollers are a {band} call ({n} patrollers ranked); rank-1 remains "
                "an acceptable assignment absent a reason to prefer rank-2")

    return _dedupe_and_relabel(raw, prefix="assign-tie", label="assign_rank_1", label_source="hand",
                                rationale=_rationale)


# --- RULE turns -----------------------------------------------------------------


def _generate_rule_turns(tmp_path: Path, catalogue) -> list[dict[str, Any]]:
    turns = []

    turn_id = "rule2-wait-approval"
    run = _new_run(tmp_path, turn_id)
    try:
        run.propose_plan("PLAN_ROUTE_ONLY_2_ZONE")
        turn = _capture_turn_v2(run, catalogue, turn_id, expected_point="WAIT_APPROVAL")
    finally:
        run.close()
    _assert_rule_label(turn_id, turn, "wait")
    turns.append(_finalize_rule_turn(turn))

    turn_id = "rule2-wait-observation"
    run = _new_run(tmp_path, turn_id)
    try:
        run.approve_route_plan()
        turn = _capture_turn_v2(run, catalogue, turn_id, expected_point="WAIT_OBSERVATION")
    finally:
        run.close()
    _assert_rule_label(turn_id, turn, "wait")
    turns.append(_finalize_rule_turn(turn))

    turn_id = "rule2-evidence-veto"
    run = _new_run(tmp_path, turn_id)
    try:
        run.approve_route_plan()
        run.ingest("veto", LEFT_MID, 0.35)
        turn = _capture_turn_v2(run, catalogue, turn_id, expected_point="EVIDENCE_VETO")
    finally:
        run.close()
    _assert_rule_label(turn_id, turn, "escalate")
    turns.append(_finalize_rule_turn(turn))

    turn_id = "rule2-rank"
    run = _new_run(tmp_path, turn_id)
    try:
        run.approve_route_plan()
        run.create_task(run.ingest("rank", LEFT_MID, 0.99))
        turn = _capture_turn_v2(run, catalogue, turn_id, expected_point="RANK")
    finally:
        run.close()
    _assert_rule_label(turn_id, turn, "rank")
    turns.append(_finalize_rule_turn(turn))

    turn_id = "rule2-assign-clear-gap"
    run = _new_run(tmp_path, turn_id)
    try:
        run.approve_route_plan()
        task_id = run.create_task(run.ingest("assign", LEFT_MID, 0.99))
        SimulatedPatroller(run.repo).rank_for_task(task_id, {"PATROLLER_1": 0.90, "PATROLLER_2": 0.60})
        turn = _capture_turn_v2(run, catalogue, turn_id, expected_point="ASSIGN")
    finally:
        run.close()
    _assert_rule_label(turn_id, turn, "assign_rank_1")
    turns.append(_finalize_rule_turn(turn))

    turn_id = "rule2-evidence-gate-clean-create"
    run = _new_run(tmp_path, turn_id)
    try:
        run.approve_route_plan()
        run.ingest("clean", LEFT_MID, 0.90)
        turn = _capture_turn_v2(run, catalogue, turn_id, expected_point="EVIDENCE_GATE")
    finally:
        run.close()
    _assert_rule_label(turn_id, turn, "create_task")
    turns.append(_finalize_rule_turn(turn))

    turn_id = "rule2-evidence-gate-out-of-plan-low"
    run = _new_run(tmp_path, turn_id)
    try:
        run.approve_route_plan()
        run.ingest("outlow", LEFT_FRONT, 0.60)
        turn = _capture_turn_v2(run, catalogue, turn_id, expected_point="EVIDENCE_GATE")
    finally:
        run.close()
    _assert_rule_label(turn_id, turn, "escalate")
    turns.append(_finalize_rule_turn(turn))

    turn_id = "rule2-evidence-gate-in-plan-below-075"
    run = _new_run(tmp_path, turn_id)
    try:
        run.approve_route_plan()
        run.ingest("inlow", LEFT_MID, 0.62)
        turn = _capture_turn_v2(run, catalogue, turn_id, expected_point="EVIDENCE_GATE")
    finally:
        run.close()
    _assert_rule_label(turn_id, turn, "escalate")
    turns.append(_finalize_rule_turn(turn))

    return turns


def generate_turns() -> list[dict[str, Any]]:
    """Drive the real gateway and return every v2 turn: 56 original + 24
    generated ``SELECT_PLAN`` (against the draft catalogue), the distinct
    ``EVIDENCE_GATE`` duplicate turns, the distinct ``ASSIGN`` near-tie turns,
    and 8 single-candidate RULE turns. No REPLAN turns (Task 9 was dropped)."""
    catalogue = load_catalogue_plans()
    draft_catalogue = load_catalogue_plans(DRAFT_CATALOGUE_PATH)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        turns: list[dict[str, Any]] = []
        turns.extend(_generate_select_plan_scored_turns(tmp_path, draft_catalogue))
        turns.extend(_generate_select_plan_pending_turns(tmp_path, draft_catalogue))
        turns.extend(_generate_duplicate_turns(tmp_path, catalogue))
        turns.extend(_generate_near_tie_turns(tmp_path, catalogue))
        turns.extend(_generate_rule_turns(tmp_path, catalogue))
    return turns


__all__ = ["GENERATOR_REVISION", "generate_turns"]
