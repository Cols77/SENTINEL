from pathlib import Path

import pytest

from sentinel.coordinator.command_gateway import CommandGateway
from sentinel.coordinator.errors import CoordinatorError
from sentinel.coordinator.repository import CoordinatorRepository

RUN_ID = "11111111-1111-4111-8111-111111111111"
COMMAND_ID = "22222222-2222-4222-8222-222222222222"
CORRELATION_ID = "33333333-3333-4333-8333-333333333333"
PLAN_ID = "44444444-4444-4444-8444-444444444444"


def command(command_type, actor_type="OPERATOR", expected_state_version=0, payload=None):
    return {
        "contract_version": "sentinel.v0",
        "wire_type": "command_envelope",
        "command_id": COMMAND_ID,
        "command_type": command_type,
        "run_id": RUN_ID,
        "actor_type": actor_type,
        "actor_id": "OPERATOR_1",
        "correlation_id": CORRELATION_ID,
        "idempotency_key": "command-1",
        "expected_state_version": expected_state_version,
        "issued_at": "2026-09-17T13:00:00Z",
        "payload": payload or {},
    }


def operator_payload():
    return {
        "decision": "CONFIRM",
        "target": {
            "kind": "PLAN",
            "plan_reference": {
            "plan_id": PLAN_ID,
            "catalogue_id": "SURVEY_CATALOGUE_V1",
            "catalogue_revision": "REV_1",
            "plan_sha256": "a" * 64,
            "station_ids": ["LEFT_ALLEY_MIDDLE", "RIGHT_ALLEY_MIDDLE"],
            },
        },
    }


def test_malformed_typed_command_is_rejected_without_mutation(tmp_path: Path):
    repository = CoordinatorRepository.open(tmp_path / "gateway.sqlite3")
    repository.create_run(RUN_ID)
    gateway = CommandGateway(repository)

    with pytest.raises(CoordinatorError) as caught:
        gateway.submit(command("OPERATOR_DECIDE", payload={"decision": "CONFIRM"}))

    assert caught.value.code == "INVALID_CONTRACT"
    assert repository.read_events(RUN_ID, 0, 10) == []
    repository.close()


def test_operator_decision_rejects_stale_state_version(tmp_path: Path):
    repository = CoordinatorRepository.open(tmp_path / "gateway.sqlite3")
    repository.create_run(RUN_ID)
    repository.append_event(RUN_ID, "RUN_CREATED", {"run_id": RUN_ID})
    gateway = CommandGateway(repository)

    with pytest.raises(CoordinatorError) as caught:
        gateway.submit(command("OPERATOR_DECIDE", expected_state_version=0, payload=operator_payload()))

    assert caught.value.code == "STALE_STATE_VERSION"
    assert len(repository.read_events(RUN_ID, 0, 10)) == 1
    repository.close()


def test_agent_cannot_submit_operator_decision(tmp_path: Path):
    repository = CoordinatorRepository.open(tmp_path / "gateway.sqlite3")
    repository.create_run(RUN_ID)
    gateway = CommandGateway(repository)

    with pytest.raises(CoordinatorError) as caught:
        gateway.submit(command("OPERATOR_DECIDE", actor_type="AGENT", payload=operator_payload()))

    assert caught.value.code == "UNAUTHORIZED_ACTOR"
    repository.close()


def test_dispatch_requires_approved_plan_binding(tmp_path: Path):
    repository = CoordinatorRepository.open(tmp_path / "gateway.sqlite3")
    repository.create_run(RUN_ID, status="RUNNING")
    gateway = CommandGateway(repository)

    with pytest.raises(CoordinatorError) as caught:
        gateway.submit(command("INSPECTION_DISPATCH", payload={
            "section_id": "LEFT_ALLEY_MIDDLE",
            "mission_id": PLAN_ID,
            "worker_id": "WORKER_FAKE",
        }))

    assert caught.value.code == "INVALID_STATE_TRANSITION"
    assert repository.read_events(RUN_ID, 0, 10) == []
    repository.close()


def test_task_rank_gateway_persists_ranking_and_event(tmp_path: Path):
    repository = CoordinatorRepository.open(tmp_path / "gateway.sqlite3")
    repository.create_run(RUN_ID, status="RUNNING")
    repository.connection.execute("INSERT INTO tasks(task_id, run_id, station_id, status, created_at) VALUES (?, ?, ?, 'PROPOSED', ?)", (PLAN_ID, RUN_ID, "LEFT_ALLEY_MIDDLE", "2026-09-17T00:00:00Z"))
    repository.connection.commit()
    gateway = CommandGateway(repository)
    ranked = command("TASK_RANK", actor_type="AGENT", payload={"task_id": PLAN_ID})

    result = gateway.submit(ranked)

    assert result["status"] == "COMPLETED"
    assert [row["patroller_id"] for row in repository.connection.execute("SELECT * FROM task_rankings ORDER BY rank_no").fetchall()] == ["PATROLLER_1"]
    assert repository.read_events(RUN_ID, 0, 10)[0]["event_type"] == "PATROLLER_ASSIGNMENT_PROPOSED"
