import json
from pathlib import Path

import pytest

from sentinel.contracts import ContractValidationError, validate_document


ROOT = Path(__file__).parents[2]
NORMATIVE = ROOT / "docs" / "superpowers" / "specs" / "contracts"
FIXTURES = NORMATIVE / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_valid_live_perception_fixture_is_accepted_by_public_root():
    assert validate_document(load("judged-perception-live.valid.json")) == []


def test_non_wire_survey_plan_reference_is_rejected_by_public_root():
    with pytest.raises(ContractValidationError):
        validate_document(load("survey-plan-reference.valid.json"), schema="root")


@pytest.mark.parametrize(
    "name",
    [
        "command-run-create-approval.invalid.json",
        "command-operator-decide-request-text.invalid.json",
        "event-run-created-operation.invalid.json",
        "agent-intent-missing-intent.invalid.json",
        "agent-no-action-inspect.invalid.json",
        "judged-perception-simulated.invalid.json",
        "judged-perception-replayed.invalid.json",
    ],
)
def test_malformed_or_forbidden_wire_fixtures_are_rejected(name):
    with pytest.raises(ContractValidationError):
        validate_document(load(name))


def test_valid_event_fixture_is_accepted_by_public_root():
    assert validate_document(load("event-plan-approved.valid.json")) == []


def test_valid_perception_fixture_can_be_validated_without_raising():
    assert validate_document(load("perception-request-artifact-hash-mismatch.invalid.json")) == []
