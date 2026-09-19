"""P2 Task 1: representative decision-turn fixture, driven from the real gateway."""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

import jsonschema
import pytest

from .turn_generator import generate_turns

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "decisions"
JSONL_PATH = FIXTURE_DIR / "turns.v1.jsonl"
SCHEMA_PATH = FIXTURE_DIR / "turns.v1.schema.json"

_JUDGMENT_POINTS = {"SELECT_PLAN", "EVIDENCE_GATE"}

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
    """When SENTINEL_REGEN_TURNS=1, rewrite the committed fixture from a fresh
    ``generate_turns()`` call, keeping any existing label/label_source/rationale
    per turn_id (so an operator's approved relabelling survives regeneration)."""
    if os.environ.get("SENTINEL_REGEN_TURNS") != "1":
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


def test_label_is_among_candidate_labels(committed_turns):
    for record in committed_turns:
        labels = [c["label"] for c in record["candidates"]]
        assert record["label"] in labels, f"{record['turn_id']}: label {record['label']!r} not in {labels}"


def test_rule_turns_have_exactly_one_candidate(committed_turns):
    for record in committed_turns:
        if record["decision_point"] not in _JUDGMENT_POINTS:
            assert len(record["candidates"]) == 1, record["turn_id"]


def test_hand_labels_have_a_nonempty_rationale(committed_turns):
    for record in committed_turns:
        if record["label_source"] == "hand":
            assert record["rationale"] and record["rationale"].strip(), record["turn_id"]


def test_at_least_20_turns_each_for_select_plan_and_evidence_gate(committed_turns):
    by_point: dict[str, int] = {}
    for record in committed_turns:
        by_point[record["decision_point"]] = by_point.get(record["decision_point"], 0) + 1
    assert by_point.get("SELECT_PLAN", 0) >= 20
    assert by_point.get("EVIDENCE_GATE", 0) >= 20


def test_no_uuid_in_any_packet(committed_turns):
    for record in committed_turns:
        packet_text = json.dumps(record["packet"])
        assert not _UUID_RE.search(packet_text), f"{record['turn_id']}: packet contains a UUID-shaped value"


def test_turn_ids_are_unique(committed_turns):
    ids = [record["turn_id"] for record in committed_turns]
    assert len(ids) == len(set(ids))


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
