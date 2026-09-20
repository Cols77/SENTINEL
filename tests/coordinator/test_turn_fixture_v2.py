"""P3a Task 10: decision-turn fixture v2 (judgment surface added on top of v1).

REPLAN is not a decision point on this branch (Task 9 was implemented and dropped,
commit 8248355): this file asserts no REPLAN turn exists and asserts no minimum for
it -- there is nothing to generate.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import jsonschema
import pytest

from .turn_generator_v2 import generate_turns

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "decisions_v2"
JSONL_PATH = FIXTURE_DIR / "turns.v2.jsonl"
SCHEMA_PATH = FIXTURE_DIR / "turns.v2.schema.json"

# Matches a UUID in any of its usual textual forms (hyphenated, most commonly).
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _read_committed() -> list[dict]:
    if not JSONL_PATH.exists():
        return []
    lines = JSONL_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _write_fixture(records: list[dict]) -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(record, sort_keys=True, separators=(",", ":")) for record in records)
    JSONL_PATH.write_text(body + "\n", encoding="utf-8")


def _maybe_regenerate(generated: list[dict]) -> list[dict]:
    """When SENTINEL_REGEN_TURNS_V2=1, rewrite the committed fixture from a fresh
    ``generate_turns()`` call, keeping any existing label/label_source/rationale
    per turn_id (so an operator's approved relabelling at G-labels-v2 survives
    regeneration)."""
    if os.environ.get("SENTINEL_REGEN_TURNS_V2") != "1":
        return generated
    existing = {record["turn_id"]: record for record in _read_committed()}
    merged = []
    for record in generated:
        prior = existing.get(record["turn_id"])
        if prior is not None:
            record = {**record, "label": prior["label"], "label_source": prior["label_source"],
                      "rationale": prior["rationale"]}
        merged.append(record)
    _write_fixture(merged)
    return merged


@pytest.fixture(scope="module")
def generated_turns() -> list[dict]:
    return generate_turns()


@pytest.fixture(scope="module")
def committed_turns(generated_turns) -> list[dict]:
    _maybe_regenerate(generated_turns)
    return _read_committed()


def _scored(turns: list[dict]) -> list[dict]:
    return [r for r in turns if r["label_source"] != "operator_pending"]


def test_schema_file_exists_and_is_a_schema():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)


def test_every_line_validates_against_schema(committed_turns):
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(schema)
    assert committed_turns, "fixture must not be empty"
    for record in committed_turns:
        errors = sorted(validator.iter_errors(record), key=str)
        assert not errors, f"{record.get('turn_id')}: {[str(e) for e in errors]}"


def test_every_packet_is_the_v2_context_version(committed_turns):
    for record in committed_turns:
        assert record["packet"]["context_version"] == "decision_packet.v2", record["turn_id"]


def test_label_is_among_candidate_labels(committed_turns):
    for record in _scored(committed_turns):
        labels = [c["label"] for c in record["candidates"]]
        assert record["label"] in labels, f"{record['turn_id']}: label {record['label']!r} not in {labels}"


def test_hand_labels_have_a_nonempty_rationale(committed_turns):
    for record in committed_turns:
        if record["label_source"] == "hand":
            assert record["rationale"] and record["rationale"].strip(), record["turn_id"]


def test_rule_label_equals_rule_choice_when_a_rule_settled_the_turn(committed_turns):
    for record in committed_turns:
        if record["label_source"] == "rule" and record["rule_choice"] is not None:
            assert record["label"] == record["rule_choice"], record["turn_id"]


def test_no_uuid_in_any_packet(committed_turns):
    for record in committed_turns:
        packet_text = json.dumps(record["packet"])
        assert not _UUID_RE.search(packet_text), f"{record['turn_id']}: packet contains a UUID-shaped value"


def test_turn_ids_are_unique(committed_turns):
    ids = [record["turn_id"] for record in committed_turns]
    assert len(ids) == len(set(ids))


def test_triple_is_unique_among_scored_turns(committed_turns):
    """Every (decision_point, packet, candidates) triple among scored (non-pending)
    turns is unique -- the anti-padding rule. The 24 operator_pending slots are
    deliberately identical placeholders for text the operator has not written yet
    (Task 10), so they are excluded here exactly as they are excluded from every
    count and from scoring."""
    seen = set()
    for record in _scored(committed_turns):
        key = (record["decision_point"], json.dumps(record["packet"], sort_keys=True),
               json.dumps(record["candidates"], sort_keys=True))
        assert key not in seen, f"duplicate triple at {record['turn_id']}"
        seen.add(key)


def test_no_replan_turns_exist(committed_turns):
    """Task 9 (REPLAN) was implemented and dropped (commit 8248355); REPLAN is not
    a decision point on this branch. No REPLAN turn should exist, and no minimum
    is asserted for it -- there is nothing reachable to generate."""
    assert all(record["decision_point"] != "REPLAN" for record in committed_turns)


def _is_judgment(record: dict) -> bool:
    """Mirrors sentinel.coordinator.legal_tools.needs_judgment: no rule settled
    it (rule_choice is None) AND there is a real choice (more than one
    candidate) -- a single-candidate RULE turn (e.g. the clear-gap ASSIGN
    turn) also carries rule_choice=None but is not a judgment point."""
    return record["rule_choice"] is None and len(record["candidates"]) > 1


def test_per_point_minimums(committed_turns):
    scored = _scored(committed_turns)
    select_plan = [r for r in scored if r["decision_point"] == "SELECT_PLAN"]
    duplicates = [r for r in scored if r["decision_point"] == "EVIDENCE_GATE" and _is_judgment(r)]
    near_ties = [r for r in scored if r["decision_point"] == "ASSIGN" and _is_judgment(r)]
    assert len(select_plan) >= 56, len(select_plan)
    assert len(duplicates) >= 8, len(duplicates)
    assert len(near_ties) >= 4, len(near_ties)


def test_pending_slots_are_flagged_and_excluded(committed_turns):
    pending = [r for r in committed_turns if r["label_source"] == "operator_pending"]
    assert len(pending) == 24
    for record in pending:
        assert record["decision_point"] == "SELECT_PLAN"
        assert record["label"] is None
        assert record["rationale"] is None
        assert record["packet"]["operator_request"] is None
    # excluded from the per-point SELECT_PLAN count computed above
    scored_select_plan = [r for r in _scored(committed_turns) if r["decision_point"] == "SELECT_PLAN"]
    assert all(r["label_source"] != "operator_pending" for r in scored_select_plan)


def test_headline_single_station_requests_get_an_exact_match_not_escalate(committed_turns):
    """The sharpest evidence for proposal item P-3: v1 escalated every single-station
    request (no v1 plan covers exactly one station); the draft catalogue's
    coverage-named plans give these an exact match instead."""
    single_station_ids = {f"PLAN_ROUTE_{s}" for s in (
        "LEFT_ALLEY_FRONT", "LEFT_ALLEY_MIDDLE", "LEFT_ALLEY_BACK",
        "RIGHT_ALLEY_FRONT", "RIGHT_ALLEY_MIDDLE", "RIGHT_ALLEY_BACK")}
    matches = [r for r in _scored(committed_turns)
               if r["decision_point"] == "SELECT_PLAN" and r["label"] in single_station_ids]
    assert len(matches) == 18, len(matches)  # 6 stations x 3 phrasings each
    for record in matches:
        assert record["label"] != "escalate"
        assert "flip" in record["rationale"].lower() or "v1" in record["rationale"].lower()


def test_golden_generator_reproduces_committed_packet_and_candidates(generated_turns, committed_turns):
    generated_by_id = {record["turn_id"]: record for record in generated_turns}
    committed_by_id = {record["turn_id"]: record for record in committed_turns}
    assert set(generated_by_id) == set(committed_by_id), (
        f"turn_id sets differ: only generated={set(generated_by_id) - set(committed_by_id)} "
        f"only committed={set(committed_by_id) - set(generated_by_id)}"
    )
    for turn_id, generated in generated_by_id.items():
        committed = committed_by_id[turn_id]
        assert generated["decision_point"] == committed["decision_point"], turn_id
        assert generated["packet"] == committed["packet"], turn_id
        assert generated["candidates"] == committed["candidates"], turn_id
        assert generated["rule_choice"] == committed["rule_choice"], turn_id
