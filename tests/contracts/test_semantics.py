import copy
import json
from pathlib import Path

import pytest

from sentinel.contracts import ContractValidationError, validate_document
from sentinel.contracts.validators import (
    ContractViolation,
    IdempotencyRegistry,
    OperationStateMachine,
    ensure_expected_state_version,
    replay_after_cursor,
    validate_artifact_hash,
    validate_bbox,
    validate_event_stream,
    validate_mountain_fixture,
    validate_preflight_report,
)


ROOT = Path(__file__).parents[2]
CONTRACTS = ROOT / "docs" / "superpowers" / "specs" / "contracts"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_artifact_hash_mismatch_has_stable_error_code():
    request = load(CONTRACTS / "fixtures" / "perception-request-artifact-hash-mismatch.invalid.json")
    with pytest.raises(ContractViolation) as error:
        validate_artifact_hash(request)
    assert error.value.code == "ARTIFACT_HASH_MISMATCH"


@pytest.mark.parametrize("box", [[0.8, 0.1, 0.2, 0.9], [0.2, 0.5, 0.2, 0.9]])
def test_inverted_or_zero_area_bbox_is_rejected(box):
    with pytest.raises(ContractViolation, match="bbox"):
        validate_bbox(box)


def test_mountain_fixture_has_six_closed_non_intersecting_zones():
    mountain = load(CONTRACTS / "mountain-v0.fixture.json")
    assert validate_mountain_fixture(mountain) == mountain
    assert len(mountain["sections"]) == 6


def test_mountain_fixture_rejects_open_ring():
    mountain = load(CONTRACTS / "mountain-v0.fixture.json")
    mountain["sections"][0]["polygon"][-1] = [8, 94]
    with pytest.raises(ContractViolation):
        validate_mountain_fixture(mountain)


def test_event_stream_requires_monotonic_run_scoped_sequence():
    events = [
        load(CONTRACTS / "fixtures" / "event-plan-approved.valid.json"),
        load(CONTRACTS / "fixtures" / "event-preflight-reported.valid.json"),
    ]
    assert validate_event_stream(events) == events
    with pytest.raises(ContractViolation) as error:
        validate_event_stream([events[1], events[0]])
    assert error.value.code == "EVENT_SEQUENCE_INVALID"


def test_preflight_report_requires_exact_eight_checks_and_conjunction():
    report = load(CONTRACTS / "fixtures" / "preflight-report.valid.json")
    assert validate_preflight_report(report) == report
    invalid = copy.deepcopy(report)
    invalid["checks"][0]["passed"] = False
    with pytest.raises(ContractViolation, match="passed"):
        validate_preflight_report(invalid)
    invalid = copy.deepcopy(report)
    invalid["checks"][0]["name"] = invalid["checks"][1]["name"]
    with pytest.raises(ContractViolation, match="exactly"):
        validate_preflight_report(invalid)


def test_idempotency_registry_replays_same_scope_and_rejects_conflict():
    command = {"run_id": "r", "actor_type": "OPERATOR", "actor_id": "o", "idempotency_key": "key-12345", "command_type": "RUN_CREATE", "expected_state_version": 0, "payload": {"request_text": "survey"}}
    registry = IdempotencyRegistry()
    assert registry.remember(command, {"status": "ACCEPTED"}) == {"status": "ACCEPTED"}
    assert registry.remember(command, {"status": "DIFFERENT"}) == {"status": "ACCEPTED"}
    changed = copy.deepcopy(command)
    changed["command_type"] = "TASK_CREATE"
    with pytest.raises(ContractViolation) as error:
        registry.remember(changed, {"status": "REJECTED"})
    assert error.value.code == "IDEMPOTENCY_KEY_CONFLICT"


def test_stale_version_is_rejected_with_stable_code():
    with pytest.raises(ContractViolation) as error:
        ensure_expected_state_version(2, 3)
    assert error.value.code == "STALE_STATE_VERSION"


def test_in_doubt_operation_only_reconciles_to_terminal_state():
    machine = OperationStateMachine("PENDING")
    machine.transition("DISPATCHED")
    machine.transition("IN_DOUBT")
    machine.transition("SUCCEEDED")
    assert machine.status == "SUCCEEDED"
    with pytest.raises(ContractViolation):
        machine.transition("DISPATCHED")


def test_restart_cursor_replays_events_after_cursor_without_duplicates():
    events = [
        load(CONTRACTS / "fixtures" / "event-plan-approved.valid.json"),
        load(CONTRACTS / "fixtures" / "event-preflight-reported.valid.json"),
    ]
    assert [event["sequence_no"] for event in replay_after_cursor(events, 7)] == [8]
    assert replay_after_cursor(events, 8) == []
