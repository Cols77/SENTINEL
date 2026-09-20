import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "judgment"
ALLOWED_PRIORITIES = {"ignore", "low", "medium", "high", "critical"}


def _load_cases():
    with (FIXTURES / "cases.v2.jsonl").open(encoding="utf-8") as handle:
        cases = [json.loads(line) for line in handle if line.strip()]
    assert cases, "cases.v2.jsonl must not be empty"
    return cases


@pytest.fixture(scope="module")
def v2_cases():
    return _load_cases()


def test_every_v2_case_validates_against_v2_schema(v2_cases):
    schema = json.loads((FIXTURES / "cases.v2.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    for case in v2_cases:
        errors = list(validator.iter_errors(case))
        assert not errors, (case["case_id"], [e.message for e in errors])


def test_v2_state_validates_as_judgment_context_v1(v2_cases):
    """J2 acceptance: every v2 case's replay state is a judgment_context.v1
    structured snapshot (no prose observations)."""
    from sentinel.judgment import validate_judgment_state

    for case in v2_cases:
        assert validate_judgment_state(case["state"]) == case["state"], case["case_id"]


def test_v2_labels_match_approved_draft_matrix(v2_cases):
    expected = {}
    for i in range(1, 6):
        expected[f"neg-{i:03d}"] = (False, False, "ignore")
    expected.update({
        "pos-001": (True, False, "high"),
        "pos-002": (True, True, "critical"),
        "pos-003": (True, False, "high"),
        "pos-004": (True, True, "high"),
    })
    for i in range(1, 7):
        expected[f"amb-{i:03d}"] = (None, True, None)
    expected.update({
        "wf-001": (False, False, "ignore"),
        "wf-002": (False, True, "low"),
        "wf-003": (True, True, "medium"),
        "wf-004": (True, False, "low"),
        "wf-005": (True, True, "high"),
        "wf-006": (True, False, "low"),
    })
    actual = {
        case["case_id"]: (case["hazard"], case["needs_review"], case["priority"])
        for case in v2_cases
    }
    assert actual == expected


def test_no_case_has_hazard_true_and_priority_ignore(v2_cases):
    for case in v2_cases:
        assert not (case["hazard"] is True and case["priority"] == "ignore"), case["case_id"]


def test_every_case_has_nonempty_rationale(v2_cases):
    for case in v2_cases:
        assert isinstance(case["rationale"], str) and case["rationale"].strip(), case["case_id"]


def test_v2_priorities_are_from_allowed_set_or_null(v2_cases):
    for case in v2_cases:
        assert case["priority"] is None or case["priority"] in ALLOWED_PRIORITIES, case["case_id"]
