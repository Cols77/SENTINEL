from pathlib import Path

import pytest

from sentinel.coordinator.command_gateway import CommandGateway
from sentinel.coordinator.errors import CoordinatorError
from sentinel.coordinator.repository import CoordinatorRepository

RUN_ID = "11111111-1111-4111-8111-111111111111"


def intent_command(payload=None):
    return {
        "contract_version": "sentinel.v0",
        "wire_type": "command_envelope",
        "command_id": "22222222-2222-4222-8222-222222222222",
        "command_type": "AGENT_INTENT_PROPOSE",
        "run_id": RUN_ID,
        "actor_type": "AGENT",
        "actor_id": "AGENT_1",
        "correlation_id": "33333333-3333-4333-8333-333333333333",
        "idempotency_key": "retry-key-1",
        "expected_state_version": 0,
        "issued_at": "2026-09-17T13:00:00Z",
        "payload": payload or {"intent": "NO_ACTION_REQUIRED"},
    }


def test_exact_duplicate_replays_original_result_without_new_event(tmp_path: Path):
    repository = CoordinatorRepository.open(tmp_path / "idempotency.sqlite3")
    repository.create_run(RUN_ID, status="RUNNING")
    gateway = CommandGateway(repository)
    original = gateway.submit(intent_command())
    replay = gateway.submit(intent_command())

    assert replay == original
    assert len(repository.read_events(RUN_ID, 0, 10)) == 1
    repository.close()


def test_conflicting_scoped_key_fails_without_mutation(tmp_path: Path):
    repository = CoordinatorRepository.open(tmp_path / "idempotency.sqlite3")
    repository.create_run(RUN_ID, status="RUNNING")
    gateway = CommandGateway(repository)
    gateway.submit(intent_command())

    with pytest.raises(CoordinatorError) as caught:
        gateway.submit(intent_command({"intent": "INSPECT_SECTION"}))

    assert caught.value.code == "IDEMPOTENCY_KEY_CONFLICT"
    assert len(repository.read_events(RUN_ID, 0, 10)) == 1
    repository.close()
